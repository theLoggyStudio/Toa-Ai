"""Traduction via Cursor API avec prompt expert BD."""

import logging
import os
import re
import secrets
from pathlib import Path

from dotenv import load_dotenv

from config import CURSOR_API_KEY, CURSOR_MODEL, CURSOR_WORKSPACE_DIR
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

FULL_PAGE_VISION_SYSTEM_PROMPT = """Tu es un expert manga vision + traduction.
Tu recois UNE PAGE COMPLETE de manga/manhwa.

Taches (dans cet ordre):
1) DETECTER la langue source dominante de la page (code ISO court).
2) Reperer toutes les zones de texte lisibles (dialogues + onomatopees).
3) Determiner l'ordre de lecture.
4) Transcrire le texte source dans sa langue d'origine.
5) Traduire vers la langue cible.

Regles critiques:
- Premiere ligne obligatoire: SOURCE_LANG|CODE (ex: SOURCE_LANG|ja)
- Ne renvoie QUE les zones avec texte lisible.
- Coordonnees: pixels de l'image source (x_min,y_min,x_max,y_max), entiers.
- Chaque bbox doit couvrir la BULLE entiere (zone blanche), pas seulement l'encre.
- Interdit de faire chevaucher deux bulles.
- Format bulles (une ligne par bulle):
  ORDER|X_MIN|Y_MIN|X_MAX|Y_MAX|SOURCE|TRADUCTION|DIRECTION|BUBBLE_BG
- SOURCE dans la langue detectee, TRADUCTION dans la langue cible.
- Les onomatopees doivent etre localisees (pas de traduction litterale).
- Pas de markdown."""

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
    return os.getenv("CURSOR_WORKSPACE_DIR", CURSOR_WORKSPACE_DIR)


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
    isolated_prompt = (
        f"[Requete isolee {request_id} — ZERO historique]\n"
        f"{prompt}"
    )
    final_prompt = f"{system_prompt}\n\n{isolated_prompt}"

    try:
        message = UserMessage(text=final_prompt)
        if images:
            sdk_images = [SDKImage.from_file(str(p)) for p in images]
            message = UserMessage(text=final_prompt, images=sdk_images)
        def _run_with_model(model_id: str):
            return Agent.prompt(
                message,
                AgentOptions(
                    api_key=api_key,
                    model=model_id,
                    local=LocalAgentOptions(cwd=workspace),
                ),
            )

        try:
            result = _run_with_model(model)
        except Exception as first_exc:
            # Certains comptes n'acceptent pas "auto"; fallback vers "default".
            if model != "default":
                logger.warning(
                    "Cursor model '%s' refuse, fallback vers 'default': %s",
                    model,
                    first_exc,
                )
                result = _run_with_model("default")
            else:
                raise

        content = (result.result or "").strip()
        if not content:
            raise RuntimeError("Reponse Cursor vide.")
        return content
    except Exception as exc:
        raise RuntimeError(f"Erreur Cursor API: {exc}") from exc


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


def _parse_full_page_response(
    raw: str,
    *,
    width: int,
    height: int,
    page_index: int,
) -> tuple[list[TextBlock], str | None]:
    blocks: list[TextBlock] = []
    detected_lang: str | None = None
    for line in raw.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.upper().startswith("SOURCE_LANG|"):
            detected_lang = normalize_lang_code(stripped.split("|", 1)[1])
            continue
        parts = stripped.split("|")
        if len(parts) not in (7, 9):
            continue
        try:
            order = int(parts[0])
            x_min = max(0, min(width - 1, int(parts[1])))
            y_min = max(0, min(height - 1, int(parts[2])))
            x_max = max(0, min(width, int(parts[3])))
            y_max = max(0, min(height, int(parts[4])))
        except ValueError:
            continue
        if x_max <= x_min or y_max <= y_min:
            continue
        src = parts[5].strip()
        trg = parts[6].strip()
        if not src or not trg:
            continue
        if len(parts) == 9:
            direction = parts[7].strip().upper()
            bubble_bg = parts[8].strip().upper()
            if direction in {"V", "VERTICAL"}:
                trg = f"[[DIR:V]]{trg}"
            elif direction in {"H", "HORIZONTAL"}:
                trg = f"[[DIR:H]]{trg}"
            if bubble_bg in {"TRANSPARENT", "NONE", "NO_BG"}:
                trg = f"[[BG:TRANSPARENT]]{trg}"
            elif bubble_bg in {"SOLID", "OPAQUE", "WHITE"}:
                trg = f"[[BG:SOLID]]{trg}"
        blocks.append(
            TextBlock(
                id=page_index * 1000 + max(0, order - 1),
                boundingBox=BoundingBox(
                    x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max
                ),
                originalText=src,
                translatedText=trg,
            )
        )
    return blocks, detected_lang


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
    if not t or len(t) > 12:
        return False
    if re.fullmatch(r"[\u30a0-\u30ff\u3040-\u309fー…・！？\s]+", t):
        return True
    if re.search(r"(ゴロ|ドン|ガタ|にゃ|ニャ|わん|ワン|シーン)", t):
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


def refine_ocr_blocks_with_cursor(
    blocks: list[TextBlock],
    source_lang: str,
    *,
    session_id: str = "",
    page_index: int = 0,
) -> list[TextBlock]:
    """Relecture OCR par Cursor: texte corrige + ordre de lecture revalide."""
    if not blocks:
        return blocks
    if not is_translator_available():
        return blocks

    nonce = secrets.token_hex(4)
    request_id = f"{session_id}-p{page_index}-{nonce}-ocr-review"
    lines = []
    for b in blocks:
        bb = b.boundingBox
        lines.append(
            f"{b.id}|x={bb.x_min},y={bb.y_min},w={bb.x_max - bb.x_min},h={bb.y_max - bb.y_min}|{b.originalText}"
        )
    prompt = (
        f"Langue source: {source_lang}\n"
        f"Nombre de bulles: {len(blocks)}\n"
        "Bulles OCR (ID|BBOX|TEXTE):\n"
        + "\n".join(lines)
    )

    try:
        raw = _chat(
            prompt,
            request_id,
            system_prompt=OCR_REVIEW_SYSTEM_PROMPT,
        )
        corrected_text, order_by_id = _parse_ocr_review_response(raw)
    except Exception:
        return blocks

    updated: list[TextBlock] = []
    for b in blocks:
        new_text = corrected_text.get(b.id, b.originalText).strip()
        if not new_text:
            new_text = b.originalText
        updated.append(b.model_copy(update={"originalText": new_text}))

    if len(order_by_id) == len(updated):
        unique_orders = set(order_by_id.values())
        if len(unique_orders) == len(updated):
            updated.sort(key=lambda b: order_by_id.get(b.id, 10**9))
            return updated
    return updated


def detect_and_translate_full_page_with_cursor(
    image_path: Path,
    source_lang: str,
    target_lang: str,
    *,
    session_id: str = "",
    page_index: int = 0,
) -> tuple[list[TextBlock], str | None]:
    """Vision full-page: detection langue, zones et traduction."""
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

    src_label = LANG_NAMES_FOR_PROMPT.get(source_lang, source_lang)
    tgt_label = LANG_NAMES_FOR_PROMPT.get(target_lang, target_lang)
    request_id = f"{session_id}-p{page_index}-{secrets.token_hex(4)}-fullpage"
    source_instruction = (
        "Detecte automatiquement la langue source de la page."
        if source_lang == "auto"
        else f"Langue source attendue: {src_label} (verifie et corrige si besoin)."
    )
    prompt = (
        f"{source_instruction}\n"
        f"Langue cible: {tgt_label}\n"
        f"Dimensions image: {width}x{height}\n"
        "Analyse la page jointe.\n"
        "Commence par SOURCE_LANG|CODE puis une ligne par bulle.\n"
        "DIRECTION: VERTICAL ou HORIZONTAL.\n"
        "BUBBLE_BG: SOLID (fond blanc)."
    )
    raw = _chat(
        prompt,
        request_id,
        system_prompt=FULL_PAGE_VISION_SYSTEM_PROMPT,
        images=[image_path],
    )
    blocks, detected_lang = _parse_full_page_response(
        raw,
        width=width,
        height=height,
        page_index=page_index,
    )
    if not blocks:
        raise RuntimeError(
            "Aucune bulle exploitable detectee sur la page."
        )
    effective_source = detected_lang if source_lang == "auto" else source_lang
    if source_lang == "auto" and not detected_lang:
        raise RuntimeError("Impossible de detecter la langue source de la page.")
    validate_full_page_blocks(blocks, effective_source, target_lang)
    return blocks, detected_lang


def _default_fr_lines(count: int, seed: str) -> list[str]:
    seed_val = sum(ord(c) for c in seed)
    samples = [
        "Tu veux vraiment nous rejoindre ?",
        "Bien sur !",
        "Alors montre-moi ce que tu sais faire.",
        "C'est notre guilde !",
        "Quoi ?!",
        "D'accord, je t'ecoute.",
        "Hein ?",
        "Ecoute bien…",
        "On y va !",
        "Pas question d'abandonner.",
        "Attends une seconde !",
        "Je ne comprends pas.",
        "C'est impossible !",
        "Laisse-moi t'expliquer.",
    ]
    return [samples[(seed_val + i * 7) % len(samples)] for i in range(count)]


def translate_blocks(
    blocks: list[TextBlock],
    source_lang: str,
    target_lang: str,
    session_id: str = "",
    page_index: int = 0,
) -> list[TextBlock]:
    if not blocks:
        return blocks

    to_translate = list(blocks)
    page_nonce = secrets.token_hex(4)
    request_id = f"{session_id}-p{page_index}-{page_nonce}"

    if not is_translator_available():
        status = get_translator_status()
        detail = status.get("error", "serveur arrete")
        raise RuntimeError(
            f"Traduction Cursor indisponible ({detail})."
        )

    if any(_is_ocr_placeholder(b.originalText) for b in blocks):
        raise RuntimeError(
            "Texte OCR invalide (mode test ou manga-ocr manquant). "
            "Mettez OCR_FAST_MODE=false et installez requirements-ml.txt."
        )

    lang_map = {"ja": "japonais", "ko": "coreen", "fr": "francais", "en": "anglais"}
    translated_map: dict[int, str] = {}

    for block in to_translate:
        text = _translate_one_block(
            block, source_lang, target_lang, request_id, lang_map
        )
        if not text:
            text = _lookup_sfx_lexicon(
                block.originalText, source_lang, target_lang
            ) or _fallback_translate(block.originalText, target_lang)
        translated_map[block.id] = text

    result: list[TextBlock] = []
    for b in blocks:
        raw_text = translated_map.get(b.id, "").strip()
        clean = raw_text
        if target_lang == "fr":
            clean = re.sub(
                r"[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]+", "", clean
            ).strip()
        src_len = len(b.originalText.strip())
        if target_lang == "fr" and src_len >= 6 and len(clean) <= 4:
            raise RuntimeError(
                f"Traduction trop courte (« {clean} ») pour « {b.originalText} ». "
                "Verifiez l'OCR (mauvaise zone) ou utilisez un modele plus capable "
                "(CURSOR_MODEL=auto ou un modele Cursor plus fort)."
            )
        if not clean:
            raise RuntimeError(
                f"Traduction vide pour la bulle : « {b.originalText[:40]}… »"
            )
        result.append(b.model_copy(update={"translatedText": clean}))
    return result


# Compatibilite avec le reste du code (anciens noms)
def get_ollama_status() -> dict:
    return get_translator_status()


def is_ollama_available() -> bool:
    return is_translator_available()


def reset_ollama_probe() -> None:
    reset_translator_probe()
