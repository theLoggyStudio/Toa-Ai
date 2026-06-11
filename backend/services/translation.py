"""Traduction via Cursor API avec prompt expert BD."""

import contextlib
import io
import logging
import os
import re
import secrets
import time
from pathlib import Path

from dotenv import load_dotenv

from config import (
    BASE_DIR,
    CURSOR_API_KEY,
    CURSOR_MAX_IMAGE_PX,
    CURSOR_MODEL,
    CURSOR_USE_CLOUD,
    CURSOR_WORKSPACE_DIR,
)
from languages import LANG_NAMES_FOR_PROMPT, normalize_lang_code
from models import BoundingBox, TextBlock
from services import glossary_rag

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu es un expert en localisation de bandes dessinées (manga/manhwa).

REGLES CRITIQUES :
- Chaque requete est isolee : traduis UNIQUEMENT le texte fourni, sans inventer de contexte.
- Dialogue : traduction naturelle en francais, phrase complete, ton manga.
- Onomatopee (katakana/hiragana courts, sons) : equivalences sensorielles FR, PAS de traduction litterale.
  Exemples : ゴロゴロ/ゴロゴ = ronronnement → « Prrr… » ou « Ron-ron » (JAMAIS « Rouler »).
  にゃー / ニャー = miaulement → « Miaou ! »
- Conserve !! et l'intensite.
- Reponds UNIQUEMENT : NUMERO|traduction (une ligne par bulle)."""

SFX_SYSTEM_PROMPT = """Tu localises des onomatopees de manga vers le francais.
Interdit : traduction mot a mot (ゴロゴロ ≠ Rouler, ドンドン ≠ Don).
Donne une onomatopee ou expression FR courte (2-5 syllabes) qui evoque le MEME son.
Reponds UNIQUEMENT par la traduction finale, sans guillemets ni explication."""

SFX_CLASSIFIER_SYSTEM_PROMPT = """Tu es un classifieur de texte manga.
Ta tache: dire si le texte est une ONOMATOPEE (SFX) ou un DIALOGUE.
Regles:
- Reponds UNIQUEMENT par SFX ou DIALOGUE.
- Pas d'explication, pas de ponctuation supplementaire.
- Si doute sur un texte tres court sonore (katakana/hiragana repetitif), reponds SFX."""

OCR_REVIEW_SYSTEM_PROMPT = """Tu es un reviseur OCR manga.
Objectif:
- Corriger legerement les erreurs OCR evidentes pour chaque bulle.
- Reordonner les bulles dans l'ordre de lecture naturel manga.

Regles critiques:
- N'invente aucun nouveau contenu.
- Conserve la langue source (ja/ko), ne traduis pas.
- Si incertitude, garde le texte OCR d'origine.
- Retourne STRICTEMENT une ligne par bulle au format:
  ID|ORDER|TEXTE_CORRIGE
- ID doit etre celui fourni, ORDER est un entier unique 1..N.
- Aucune autre ligne, aucun commentaire."""

FULL_PAGE_VISION_SYSTEM_PROMPT = """Tu es un expert manga vision + traduction + mise en page HTML/CSS.
Tu recois UNE PAGE COMPLETE de manga/manhwa.

Taches:
1) DETECTER la langue source (SOURCE_LANG|code ISO).
2) Reperer CHAQUE zone de texte separement (dialogue ET onomatopee).
3) Traduire vers la langue cible.
4) Pour chaque zone, produire le HTML du texte traduit.

REGLES CRITIQUES DE DETECTION:
- UNE ligne BUBBLE par zone distincte. JAMAIS fusionner dialogue + onomatopee.
- Onomatopees stylisees (ex. ずんっ, ぎゃあああ) = zone SEPAREE, bbox serree sur l'encre.
- Bulles de dialogue = bbox epousant la bulle blanche, PAS les visages.
- Aucune bbox ne doit depasser 20% de la surface de la page.
- Si deux textes sont a gauche (SFX) et a droite (dialogue), deux BUBBLE distinctes.

Coordonnees OBLIGATOIRES (entiers pixels image):
- x_min,y_min = coin haut-gauche de la zone
- x_max,y_max = coin bas-droite
- Zone serree sur la bulle ou l'onomatopee, sans couvrir l'art adjacent.

Traduction:
- Dialogue: naturel, ton manga (ex. 大人買い → « Quel achat en bloc ! », PAS « achat d'adulte »).
- 箱ごと / これ全部 → « Je prends tout, boîtes comprises. »
- Onomatopees: equivalent sensoriel FR court (ずんっ → « Boum ! », ぎゃああ → « GYAAAA ! »).

Format STRICT (pas de markdown):
SOURCE_LANG|ja
STYLES|(optionnel, laisse vide)
BUBBLE|ORDER|X_MIN|Y_MIN|X_MAX|Y_MAX|TEXTE_SOURCE|TRADUCTION
HTML_B64|ORDER|<base64 UTF-8 du fragment HTML, ex: <p>...</p>>

Regles HTML:
- HTML_B64 = encodage base64 (balises <p>, <span> seulement).
- Dialogue: fond blanc OPAQUE dans le HTML si besoin.
- Onomatopees: pas de fond blanc, texte seul.
- Une paire BUBBLE + HTML_B64 par zone."""

COUNT_PAGE_BUBBLES_SYSTEM = """Tu es un expert manga vision.
Detecte chaque bulle de dialogue ET chaque onomatopee SEPAREMENT.
Ne fusionne jamais plusieurs zones en une seule bbox.
Reponds STRICTEMENT (pas de markdown, pas de traduction, pas de HTML):
SOURCE_LANG|code ISO (ja, ko, zh, en, …)
BUBBLE|ORDER|X_MIN|Y_MIN|X_MAX|Y_MAX|TEXTE_SOURCE
Une ligne BUBBLE par zone distincte (dialogue ou onomatopee)."""

SFX_LEXICON: dict[tuple[str, str], dict[str, str]] = {
    ("ja", "fr"): {
        "ゴロゴロ": "Prrr…",
        "ゴロゴロ…": "Prrr…",
        "ゴロゴ": "Prrr…",
        "ゴロ": "Prrr…",
        "にゃー": "Miaou !",
        "ニャー": "Miaou !",
        "にゃ": "Miaou",
        "ニャ": "Miaou",
        "わん": "Ouaf !",
        "ワン": "Ouaf !",
        "ドン": "Boum !",
        "ドンドン": "Poum ! Poum !",
        "ガタ": "Clac !",
        "ガタガタ": "Clac-clac !",
        "シーン": "Silence…",
        "はあ": "Haah…",
        "はぁ": "Haah…",
        "ふん": "Humph !",
        "えっ": "Hein ?!",
        "え？": "Hein ?",
        "ずんっ": "Boum !",
        "ずん": "Boum !",
        "ズン": "Boum !",
        "ぎゃああああ": "GYAAAA !",
        "ぎゃあああ": "GYAAAA !",
        "ぎゃああ": "GYAAAA !",
        "ぎゃあ": "GYAAAA !",
        "ギャアアア": "GYAAAA !",
    },
    ("ja", "en"): {
        "ゴロゴロ": "Purr…",
        "ゴロゴ": "Purr…",
        "にゃー": "Meow!",
        "ニャー": "Meow!",
    },
}

_LITERAL_SFX_MISTAKES_FR = frozenset(
    {
        "rouler",
        "roule",
        "roulez",
        "son",
        "bruit",
        "goro",
        "gorogoro",
        "miaou son",
    }
)

_translator_checked: bool | None = None
_translator_available: bool = False

_OCR_PLACEHOLDER_RE = re.compile(
    r"^\[page\s+\d+\s+bulle\s+\d+\]$|^\(dialogue manga\)$",
    re.IGNORECASE,
)


def _is_ocr_placeholder(text: str) -> bool:
    return bool(_OCR_PLACEHOLDER_RE.match(text.strip()))


def is_ocr_placeholder(text: str) -> bool:
    return _is_ocr_placeholder(text)


def _cursor_api_key() -> str:
    load_dotenv(override=True)
    return os.getenv("CURSOR_API_KEY", CURSOR_API_KEY)


def _cursor_model() -> str:
    load_dotenv(override=True)
    model = os.getenv("CURSOR_MODEL", CURSOR_MODEL).strip()
    if not model or model.lower() == "auto":
        return "default"
    return model


def _cursor_workspace_dir() -> str:
    load_dotenv(override=True)
    raw = os.getenv("CURSOR_WORKSPACE_DIR", CURSOR_WORKSPACE_DIR)
    return str(Path(raw).expanduser().resolve())


def _cursor_use_cloud() -> bool:
    load_dotenv(override=True)
    return os.getenv("CURSOR_USE_CLOUD", "true").lower() in ("true", "1", "yes")


def _prepare_cursor_image(image_path: Path) -> Path:
    """Redimensionne les scans trop lourds pour limiter les erreurs Cursor."""
    try:
        from PIL import Image
    except ImportError:
        return image_path

    max_px = int(os.getenv("CURSOR_MAX_IMAGE_PX", str(CURSOR_MAX_IMAGE_PX)))
    with Image.open(image_path) as img:
        w, h = img.size
        longest = max(w, h)
        if longest <= max_px:
            return image_path
        scale = max_px / longest
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        resized = img.convert("RGB").resize(new_size, Image.Resampling.LANCZOS)
        cache_dir = BASE_DIR / "data" / "cursor_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        out = cache_dir / f"{image_path.stem}_{new_size[0]}x{new_size[1]}.jpg"
        resized.save(out, format="JPEG", quality=88, optimize=True)
        return out


def get_translator_status() -> dict:
    key = _cursor_api_key()
    model = _cursor_model()
    if not key:
        return {
            "available": False,
            "provider": "cursor",
            "model": model,
            "error": "CURSOR_API_KEY manquant",
        }
    return {"available": True, "provider": "cursor", "model": model}


def is_translator_available() -> bool:
    global _translator_checked, _translator_available
    if _translator_checked:
        return _translator_available
    _translator_checked = True
    _translator_available = bool(_cursor_api_key())
    return _translator_available


def reset_translator_probe() -> None:
    global _translator_checked, _translator_available
    _translator_checked = None
    _translator_available = False


def _ensure_os_blocking_apis() -> None:
    """Compat Windows: certains builds n'exposent pas os.get_blocking."""
    if not hasattr(os, "get_blocking"):
        os.get_blocking = lambda _fd: False  # type: ignore[attr-defined]
    if not hasattr(os, "set_blocking"):
        os.set_blocking = lambda _fd, _flag: None  # type: ignore[attr-defined]


def _chat(
    prompt: str,
    request_id: str,
    *,
    system_prompt: str = SYSTEM_PROMPT,
    images: list[Path] | None = None,
) -> str:
    _ensure_os_blocking_apis()
    try:
        from cursor_sdk import (
            Agent,
            AgentOptions,
            CloudAgentOptions,
            LocalAgentOptions,
            SDKImage,
            UserMessage,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Le package cursor-sdk n'est pas installé. "
            "Exécutez: pip install cursor-sdk"
        ) from exc

    api_key = _cursor_api_key()
    if not api_key:
        raise RuntimeError(
            "CURSOR_API_KEY manquant. Configurez la clé dans backend/.env."
        )

    model = _cursor_model()
    workspace = _cursor_workspace_dir()
    use_cloud = _cursor_use_cloud() or bool(images)
    prepared_images = (
        [_prepare_cursor_image(p) for p in images] if images else None
    )
    isolated_prompt = (
        f"[Requete isolee {request_id} — ZERO historique]\n"
        f"{prompt}"
    )
    final_prompt = f"{system_prompt}\n\n{isolated_prompt}"

    message = UserMessage(text=final_prompt)
    if prepared_images:
        sdk_images = [SDKImage.from_file(str(p)) for p in prepared_images]
        message = UserMessage(text=final_prompt, images=sdk_images)

    def _agent_options(model_id: str) -> AgentOptions:
        if use_cloud:
            return AgentOptions(
                api_key=api_key,
                model=model_id,
                cloud=CloudAgentOptions(),
            )
        return AgentOptions(
            api_key=api_key,
            model=model_id,
            local=LocalAgentOptions(cwd=workspace),
        )

    def _run_with_model(model_id: str):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            return Agent.prompt(message, _agent_options(model_id))

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            try:
                result = _run_with_model(model)
            except Exception as first_exc:
                if model != "default":
                    logger.warning(
                        "Cursor model '%s' refuse, fallback vers 'default': %s",
                        model,
                        first_exc,
                    )
                    result = _run_with_model("default")
                else:
                    raise

            status = str(getattr(result, "status", "finished")).lower()
            if status in ("error", "cancelled", "expired"):
                raise RuntimeError(f"Cursor run termine avec le statut: {status}")

            content = (result.result or "").strip()
            if content:
                return content

            last_error = RuntimeError("Reponse Cursor vide.")
            logger.warning(
                "Cursor reponse vide (tentative %s/3, request=%s)",
                attempt + 1,
                request_id,
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Cursor echec tentative %s/3 (%s): %s",
                attempt + 1,
                request_id,
                exc,
            )

        if attempt < 2:
            time.sleep(2 * (attempt + 1))

    raise RuntimeError(f"Erreur Cursor API: {last_error}")


def _fallback_translate(text: str, target_lang: str) -> str:
    if not text.strip() or text.startswith("["):
        return ""
    if target_lang == "fr":
        cleaned = re.sub(
            r"[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]+", "", text
        ).strip()
        return cleaned if cleaned else ""
    return text


def _parse_numbered_response(raw: str, count: int) -> list[str]:
    lines = raw.strip().splitlines()
    result = [""] * count
    for line in lines:
        stripped = line.strip()
        match = re.match(r"^(\d+)\s*[|:.\-)\]]\s*(.+)$", stripped)
        if match:
            idx = int(match.group(1)) - 1
            if 0 <= idx < count:
                result[idx] = match.group(2).strip()
            continue
        match2 = re.match(r"^(\d+)[.)]\s+(.+)$", stripped)
        if match2:
            idx = int(match2.group(1)) - 1
            if 0 <= idx < count:
                result[idx] = match2.group(2).strip()
    non_empty = [r for r in result if r]
    if len(non_empty) == 1 and count == 1:
        result[0] = non_empty[0]
    elif len(non_empty) >= count and not any(result):
        for i in range(count):
            if i < len(non_empty):
                result[i] = non_empty[i]
    return result


def _parse_ocr_review_response(raw: str) -> tuple[dict[int, str], dict[int, int]]:
    text_by_id: dict[int, str] = {}
    order_by_id: dict[int, int] = {}
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^(\d+)\|(\d+)\|(.+)$", stripped)
        if not match:
            continue
        block_id = int(match.group(1))
        order = int(match.group(2))
        text = match.group(3).strip()
        if text:
            text_by_id[block_id] = text
            order_by_id[block_id] = order
    return text_by_id, order_by_id


def _decode_html_b64(payload: str) -> str:
    raw = (payload or "").strip()
    if not raw:
        return ""
    try:
        import base64

        return base64.b64decode(raw).decode("utf-8").strip()
    except Exception:
        return raw


def _parse_full_page_response(
    raw: str,
    *,
    width: int,
    height: int,
    page_index: int,
) -> tuple[list[TextBlock], str | None, str]:
    blocks: list[TextBlock] = []
    detected_lang: str | None = None
    page_css = ""
    html_by_order: dict[int, str] = {}
    pending: dict[int, dict[str, object]] = {}

    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith("SOURCE_LANG|"):
            detected_lang = normalize_lang_code(stripped.split("|", 1)[1])
            continue
        if upper.startswith("STYLES|"):
            page_css = stripped.split("|", 1)[1].strip()
            continue
        if upper.startswith("HTML_B64|"):
            parts = stripped.split("|", 2)
            if len(parts) < 3:
                continue
            try:
                order = int(parts[1])
            except ValueError:
                continue
            html_by_order[order] = _decode_html_b64(parts[2])
            continue

        if not upper.startswith("BUBBLE|"):
            continue
        parts = stripped.split("|")
        if len(parts) < 7:
            continue
        try:
            order = int(parts[1])
            x_min = max(0, min(width - 1, int(parts[2])))
            y_min = max(0, min(height - 1, int(parts[3])))
            x_max = max(0, min(width, int(parts[4])))
            y_max = max(0, min(height, int(parts[5])))
        except ValueError:
            continue
        if x_max <= x_min or y_max <= y_min:
            continue
        src = parts[6].strip()
        trg = parts[7].strip() if len(parts) > 7 else ""
        if not src:
            continue
        pending[order] = {
            "bbox": (x_min, y_min, x_max, y_max),
            "src": src,
            "trg": trg,
        }

    for order in sorted(pending.keys()):
        data = pending[order]
        x_min, y_min, x_max, y_max = data["bbox"]  # type: ignore[misc]
        src = str(data["src"])
        trg = str(data["trg"])
        bubble_html = html_by_order.get(order, "")
        if not trg and bubble_html:
            trg = re.sub(r"<[^>]+>", "", bubble_html).strip()
        if not trg and not bubble_html:
            continue
        blocks.append(
            TextBlock(
                id=page_index * 1000 + max(0, order - 1),
                boundingBox=BoundingBox(
                    x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max
                ),
                originalText=src,
                translatedText=trg,
                bubbleHtml=bubble_html,
            )
        )
    return blocks, detected_lang, page_css


def _bbox_iou(a: BoundingBox, b: BoundingBox) -> float:
    x1 = max(a.x_min, b.x_min)
    y1 = max(a.y_min, b.y_min)
    x2 = min(a.x_max, b.x_max)
    y2 = min(a.y_max, b.y_max)
    inter_w = max(0, x2 - x1)
    inter_h = max(0, y2 - y1)
    inter = inter_w * inter_h
    if inter <= 0:
        return 0.0
    area_a = max(1, (a.x_max - a.x_min) * (a.y_max - a.y_min))
    area_b = max(1, (b.x_max - b.x_min) * (b.y_max - b.y_min))
    return inter / (area_a + area_b - inter)


def _apply_glossary_corrections(
    blocks: list[TextBlock],
    source_lang: str,
    target_lang: str,
) -> list[TextBlock]:
    """Applique glossaire manga et corrige les traductions litterales connues."""
    corrected: list[TextBlock] = []
    for block in blocks:
        src = (block.originalText or "").strip()
        tr = (block.translatedText or "").strip()
        direct = glossary_rag.try_direct_translation(src, source_lang, target_lang)
        lex = _lookup_sfx_lexicon(src, source_lang, target_lang)
        if direct:
            tr = direct
        elif lex:
            tr = lex
        low = tr.lower()
        if "achat d'adulte" in low or "achat d adulte" in low:
            tr = "Quel achat en bloc !"
        if "donnez-moi tout" in low and "boîte" in low:
            tr = "Je prends tout, boîtes comprises."
        if "donnez moi tout" in low and "boite" in low:
            tr = "Je prends tout, boîtes comprises."
        corrected.append(block.model_copy(update={"translatedText": tr}))
    return corrected


def validate_full_page_blocks(
    blocks: list[TextBlock],
    source_lang: str,
    target_lang: str,
) -> None:
    """Verifie traduction complete et absence d'enchevetrement de bulles."""
    if not blocks:
        raise RuntimeError("Aucune bulle detectee par Cursor.")

    missing_translation: list[int] = []
    suspicious_same_text: list[int] = []
    for idx, block in enumerate(blocks, start=1):
        src = (block.originalText or "").strip()
        trg = (block.translatedText or "").strip()
        if not src or not trg:
            missing_translation.append(idx)
            continue
        # En cross-lang, on attend une transformation; meme texte peut signaler oubli.
        if source_lang != target_lang and src == trg:
            suspicious_same_text.append(idx)

    if missing_translation:
        preview = ", ".join(str(i) for i in missing_translation[:8])
        raise RuntimeError(
            f"Verification echec: traduction manquante sur bulle(s) {preview}."
        )

    max_allowed_iou = 0.18
    overlaps: list[tuple[int, int, float]] = []
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            iou = _bbox_iou(blocks[i].boundingBox, blocks[j].boundingBox)
            if iou > max_allowed_iou:
                overlaps.append((i + 1, j + 1, iou))

    if overlaps:
        b1, b2, iou = overlaps[0]
        raise RuntimeError(
            "Verification echec: bulles qui s'enchevetrent "
            f"(#{b1} et #{b2}, IOU={iou:.2f})."
        )

    if suspicious_same_text:
        logger.warning(
            "Verification: %d bulle(s) avec texte source==traduction (%s)",
            len(suspicious_same_text),
            ", ".join(str(i) for i in suspicious_same_text[:8]),
        )


def _looks_like_sfx(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 18:
        return False
    if re.fullmatch(r"[\u30a0-\u30ff\u3040-\u309fー…・！？\sっ゛゜]+", t):
        return True
    if re.search(
        r"(ゴロ|ドン|ガタ|にゃ|ニャ|わん|ワン|シーン|ぎゃ|ギャ|ずん|ズン)", t
    ):
        return True
    return False


def _lookup_sfx_lexicon(text: str, source_lang: str, target_lang: str) -> str | None:
    table = SFX_LEXICON.get((source_lang, target_lang), {})
    key = text.strip()
    if key in table:
        return table[key]
    for src, dst in table.items():
        if key.startswith(src) or src.startswith(key):
            return dst
    if "ゴロ" in key and target_lang == "fr":
        return "Prrr…"
    if ("にゃ" in key or "ニャ" in key) and target_lang == "fr":
        return "Miaou !"
    return None


def _clean_translation(text: str, target_lang: str) -> str:
    clean = text.strip()
    clean = re.sub(r"^[\"'«»]+|[\"'«»]+$", "", clean).strip()
    if target_lang in {"fr", "en", "es", "de", "pt", "it", "pl", "nl", "tr", "vi", "id"}:
        clean = re.sub(
            r"[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]+", "", clean
        ).strip()
    return clean


def _reject_bad_sfx_translation(
    source: str, translated: str, target_lang: str
) -> bool:
    if target_lang != "fr" or not _looks_like_sfx(source):
        return False
    low = translated.lower().strip(" .…!")
    return low in _LITERAL_SFX_MISTAKES_FR


def _rag_context_block(
    src: str, source_lang: str, target_lang: str
) -> tuple[str, str | None]:
    """Contexte RAG + traduction directe si dictionnaire manga."""
    direct = glossary_rag.try_direct_translation(src, source_lang, target_lang)
    hits = glossary_rag.retrieve_hits(src, source_lang, target_lang, limit=5)
    ctx = glossary_rag.format_rag_context(hits, source_lang, target_lang)
    return ctx, direct


def _translate_one_block(
    block: TextBlock,
    source_lang: str,
    target_lang: str,
    request_id: str,
    lang_map: dict[str, str],
) -> str:
    src = (block.originalText or "").strip()
    if not src:
        return ""

    rag_ctx, rag_direct = _rag_context_block(src, source_lang, target_lang)
    if rag_direct:
        return rag_direct

    lex = _lookup_sfx_lexicon(src, source_lang, target_lang)
    if lex:
        return lex

    is_sfx = _is_sfx_with_cursor(
        src,
        source_lang=source_lang,
        target_lang=target_lang,
        request_id=request_id,
    )
    tgt_label = lang_map.get(target_lang, target_lang)
    rag_section = f"\n\n{rag_ctx}\n" if rag_ctx else ""

    if is_sfx:
        prompt = (
            f"Onomatopee manga ({lang_map.get(source_lang, source_lang)}) :\n"
            f"« {src} »\n"
            f"→ onomatopee {tgt_label} courte (ex. ronronnement = Prrr, miaulement = Miaou) :"
            f"{rag_section}"
        )
        raw = _chat(prompt, f"{request_id}-sfx-{block.id}", system_prompt=SFX_SYSTEM_PROMPT)
        text = _clean_translation(raw.splitlines()[0], target_lang)
        if _reject_bad_sfx_translation(src, text, target_lang):
            text = ""
        if not text:
            text = _lookup_sfx_lexicon(src, source_lang, target_lang) or ""
        return text

    prompt = (
        f"Replique de dialogue manga ({lang_map.get(source_lang, source_lang)}) :\n"
        f"« {src} »\n"
        f"→ {tgt_label} (une seule phrase naturelle, sans numero ni commentaire) :"
        f"{rag_section}"
    )
    raw = _chat(prompt, f"{request_id}-d-{block.id}")
    line = raw.strip().splitlines()[0]
    line = re.sub(r"^\d+\s*[|:.\-)\]]\s*", "", line).strip()
    return _clean_translation(line, target_lang)


def _is_sfx_with_cursor(
    text: str,
    *,
    source_lang: str,
    target_lang: str,
    request_id: str,
) -> bool:
    """Laisse Cursor decider SFX vs dialogue; fallback regex local si besoin."""
    src = text.strip()
    if not src:
        return False

    # Heuristique ultra-courte locale (evite appel API inutile)
    if _looks_like_sfx(src) and len(src) <= 4:
        return True

    prompt = (
        f"Langue source: {source_lang}\n"
        f"Langue cible: {target_lang}\n"
        f"Texte manga:\n« {src} »\n\n"
        "Est-ce une onomatopee (effet sonore) ou un dialogue ?"
    )
    try:
        verdict = _chat(
            prompt,
            f"{request_id}-classify-sfx",
            system_prompt=SFX_CLASSIFIER_SYSTEM_PROMPT,
        ).strip().upper()
        if "SFX" in verdict:
            return True
        if "DIALOGUE" in verdict:
            return False
    except Exception:
        pass

    return _looks_like_sfx(src)


def count_bubbles_with_cursor(
    image_path: Path,
    *,
    session_id: str = "",
    page_index: int = 0,
) -> tuple[int, str | None]:
    """Compte les bulles via vision Cursor (tarification a l'upload)."""
    if not is_translator_available():
        raise RuntimeError("Cursor indisponible pour l'estimation des bulles.")

    from PIL import Image

    with Image.open(image_path) as img:
        width, height = img.size

    request_id = f"{session_id}-p{page_index}-{secrets.token_hex(4)}-count"
    prompt = (
        f"Dimensions image: {width}x{height}\n"
        "Compte toutes les zones de texte visibles sur la page jointe.\n"
        "Reponds avec SOURCE_LANG puis une ligne BUBBLE par zone."
    )
    raw = _chat(
        prompt,
        request_id,
        system_prompt=COUNT_PAGE_BUBBLES_SYSTEM,
        images=[image_path],
    )
    blocks, detected_lang, _ = _parse_full_page_response(
        raw,
        width=width,
        height=height,
        page_index=page_index,
    )
    return len(blocks), detected_lang


def _scale_blocks_to_image_size(
    blocks: list[TextBlock],
    *,
    from_width: int,
    from_height: int,
    to_width: int,
    to_height: int,
) -> list[TextBlock]:
    """Reprojette les coords Cursor si l'image envoyee etait redimensionnee."""
    if from_width <= 0 or from_height <= 0:
        return blocks
    if from_width == to_width and from_height == to_height:
        return blocks
    sx = to_width / from_width
    sy = to_height / from_height
    scaled: list[TextBlock] = []
    for block in blocks:
        bb = block.boundingBox
        scaled.append(
            block.model_copy(
                update={
                    "boundingBox": BoundingBox(
                        x_min=int(bb.x_min * sx),
                        y_min=int(bb.y_min * sy),
                        x_max=int(bb.x_max * sx),
                        y_max=int(bb.y_max * sy),
                    )
                }
            )
        )
    return scaled


def detect_and_translate_full_page_with_cursor(
    image_path: Path,
    source_lang: str,
    target_lang: str,
    *,
    session_id: str = "",
    page_index: int = 0,
) -> tuple[list[TextBlock], str | None, str]:
    """Vision full-page: detection, traduction, HTML/CSS bulles + coordonnees."""
    if not is_translator_available():
        status = get_translator_status()
        detail = status.get("error", "indisponible")
        raise RuntimeError(f"Traduction Cursor indisponible ({detail}).")

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow manquant pour lecture de la page.") from exc

    with Image.open(image_path) as img:
        width, height = img.size

    prepared_path = _prepare_cursor_image(image_path)
    with Image.open(prepared_path) as sent_img:
        sent_width, sent_height = sent_img.size

    tgt_label = LANG_NAMES_FOR_PROMPT.get(target_lang, target_lang)
    request_id = f"{session_id}-p{page_index}-{secrets.token_hex(4)}-fullpage"
    prompt = (
        "Detecte automatiquement la langue source de la page.\n"
        f"Langue cible: {tgt_label}\n"
        f"Dimensions image: {sent_width}x{sent_height} pixels\n"
        "Analyse la page jointe.\n"
        "IMPORTANT: une ligne BUBBLE par zone distincte (dialogue ET onomatopee separees).\n"
        "Aucune bbox ne doit depasser 20% de la surface ({:.0f} px2 max par zone).\n"
        "Reponds avec SOURCE_LANG, puis pour chaque zone: "
        "BUBBLE|ORDER|X_MIN|Y_MIN|X_MAX|Y_MAX|TEXTE_SOURCE|TRADUCTION puis "
        "HTML_B64|ORDER|<html en base64>.\n"
        "Les coordonnees doivent epouser la bulle (x_min,y_min = coin haut-gauche de la zone)."
    ).format(sent_width * sent_height * 0.20)
    raw = _chat(
        prompt,
        request_id,
        system_prompt=FULL_PAGE_VISION_SYSTEM_PROMPT,
        images=[prepared_path],
    )
    blocks, detected_lang, page_css = _parse_full_page_response(
        raw,
        width=sent_width,
        height=sent_height,
        page_index=page_index,
    )
    blocks = _scale_blocks_to_image_size(
        blocks,
        from_width=sent_width,
        from_height=sent_height,
        to_width=width,
        to_height=height,
    )
    if not blocks:
        raise RuntimeError("Aucune bulle exploitable detectee sur la page.")
    if not detected_lang:
        raise RuntimeError("Impossible de detecter la langue source de la page.")
    blocks = _apply_glossary_corrections(blocks, detected_lang, target_lang)
    validate_full_page_blocks(blocks, detected_lang, target_lang)
    return blocks, detected_lang, page_css


# Compatibilite API (historique)
def get_ollama_status() -> dict:
    return get_translator_status()


def is_ollama_available() -> bool:
    return is_translator_available()


def reset_ollama_probe() -> None:
    reset_translator_probe()
