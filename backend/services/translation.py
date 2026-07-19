"""Traduction via Cursor API avec prompt expert BD."""

import contextlib
import hashlib
import io
import json
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
- La bbox doit etre EXACTEMENT a l'endroit du texte dans le dessin.
  JAMAIS dans les marges noires, les bords ou les coins de la page.
- 0 <= x_min < x_max <= largeur image ; 0 <= y_min < y_max <= hauteur image.
  Verifie chaque coordonnee avant de repondre : une coordonnee hors image
  ou collee au bord droit/bas est une ERREUR.

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

_translator_checked: bool | None = None
_translator_available: bool = False

_env_mtime: float | None = None


def _reload_env_if_changed() -> None:
    """Relit .env uniquement s'il a changé (au lieu d'un load_dotenv par appel)."""
    global _env_mtime
    env_path = BASE_DIR / ".env"
    try:
        mtime = env_path.stat().st_mtime
    except OSError:
        return
    if mtime != _env_mtime:
        _env_mtime = mtime
        load_dotenv(override=True)


def _cursor_api_key() -> str:
    _reload_env_if_changed()
    return os.getenv("CURSOR_API_KEY", CURSOR_API_KEY)


def _cursor_model() -> str:
    _reload_env_if_changed()
    model = os.getenv("CURSOR_MODEL", CURSOR_MODEL).strip()
    if not model or model.lower() == "auto":
        return "default"
    return model


def _cursor_workspace_dir() -> str:
    _reload_env_if_changed()
    raw = os.getenv("CURSOR_WORKSPACE_DIR", CURSOR_WORKSPACE_DIR)
    return str(Path(raw).expanduser().resolve())


def _cursor_use_cloud() -> bool:
    _reload_env_if_changed()
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
    system_prompt: str,
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


# ---------------------------------------------------------------------------
# Cache de traduction par page : évite de re-payer Cursor pour une page déjà
# traduite (reprise après échec, re-upload du même scan, etc.).
# ---------------------------------------------------------------------------

_PAGE_CACHE_DIR = BASE_DIR / "data" / "cursor_cache" / "pages"


def _page_cache_path(image_path: Path, target_lang: str) -> Path:
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()[:40]
    return _PAGE_CACHE_DIR / f"{digest}_{target_lang}.json"


def _load_cached_page(
    image_path: Path,
    target_lang: str,
    page_index: int,
) -> tuple[list[TextBlock], str, str] | None:
    try:
        cache_file = _page_cache_path(image_path, target_lang)
        if not cache_file.exists():
            return None
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        blocks = [TextBlock(**raw) for raw in data["blocks"]]
        # Ré-identifie les blocs pour la page courante (l'id encode la page).
        blocks = [
            b.model_copy(update={"id": page_index * 1000 + i})
            for i, b in enumerate(blocks)
        ]
        return blocks, data["detectedLang"], data.get("pageCss", "")
    except Exception:
        return None


def _store_cached_page(
    image_path: Path,
    target_lang: str,
    blocks: list[TextBlock],
    detected_lang: str,
    page_css: str,
) -> None:
    try:
        _PAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "blocks": [b.model_dump() for b in blocks],
            "detectedLang": detected_lang,
            "pageCss": page_css,
        }
        _page_cache_path(image_path, target_lang).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        logger.debug("Cache page non écrit: %s", exc)


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

    cached = _load_cached_page(image_path, target_lang, page_index)
    if cached:
        logger.info("Page %s servie depuis le cache de traduction", image_path.name)
        return cached

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
    _store_cached_page(image_path, target_lang, blocks, detected_lang, page_css)
    return blocks, detected_lang, page_css
