"""Composition scan + texte traduit clipé au polygone exact de chaque bulle."""

from __future__ import annotations

import html
import logging
import re
import shutil
import threading
from pathlib import Path

from models import BoundingBox, TextBlock
from services.bubble_fit import (
    BUBBLE_FIT_CSS,
    BUBBLE_FIT_SCRIPT,
    TEXT_PAD_CSS,
    TRANSLATED_TEXT_COLOR,
    boxes_iou,
    boxes_overlap,
    build_bubble_wrap,
    content_inner_pad,
    estimate_font_size,
    estimate_original_font_size,
    format_lines_html,
    polygon_bbox,
    resolve_placement_boxes,
    shrink_polygon,
)
from services.rendering import (
    _extract_render_hints,
    detect_bubble_polygon,
    is_sfx_block,
    refine_blocks_for_render,
)

logger = logging.getLogger(__name__)

_WIN_FONTS = Path("C:/Windows/Fonts")


def _font_file_uri(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.resolve().as_uri()


def _build_font_faces_css() -> str:
    dialogue = _font_file_uri(_WIN_FONTS / "YuGothB.ttc")
    sfx = _font_file_uri(_WIN_FONTS / "impact.ttf")
    faces: list[str] = []
    if dialogue:
        faces.append(
            f'@font-face {{ font-family: "ToaManga"; src: url("{dialogue}"); }}'
        )
    if sfx:
        faces.append(
            f'@font-face {{ font-family: "ToaSFX"; src: url("{sfx}"); }}'
        )
    return "\n".join(faces)


DEFAULT_PAGE_CSS = f"""
{_build_font_faces_css()}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ overflow: hidden; }}
.page-scan {{
  position: absolute;
  left: 0;
  top: 0;
  width: 100%;
  height: 100%;
  object-fit: fill;
  z-index: 0;
}}
/* Texte traduit au-dessus de tout (scan, art, autres calques). */
.bubble-layer {{
  position: absolute;
  inset: 0;
  z-index: 1000 !important;
  isolation: isolate;
  pointer-events: none;
}}
.toa-bubble {{
  position: relative;
  z-index: 1000 !important;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  background: transparent !important;
  border: none !important;
  border-radius: 0;
  padding: {TEXT_PAD_CSS};
  color: {TRANSLATED_TEXT_COLOR} !important;
  font-family: "ToaManga", "Yu Gothic", "Segoe UI", sans-serif;
  font-weight: 800 !important;
  line-height: 1.25;
  word-break: normal;
  overflow-wrap: normal;
  hyphens: none;
  opacity: 1 !important;
  text-shadow: 0 0 2px #fff, 1px 0 0 #fff, -1px 0 0 #fff, 0 1px 0 #fff, 0 -1px 0 #fff;
}}
.toa-bubble, .toa-bubble * {{
  color: {TRANSLATED_TEXT_COLOR} !important;
  border: none !important;
  background: transparent !important;
  opacity: 1 !important;
  font-weight: 800 !important;
  word-break: normal !important;
  overflow-wrap: normal !important;
}}
.toa-bubble p {{
  margin: 0;
  background: transparent !important;
  color: {TRANSLATED_TEXT_COLOR} !important;
}}
.toa-bubble--sfx {{
  width: max-content;
  height: max-content;
  background: transparent !important;
  border-radius: 0;
  font-family: "ToaSFX", Impact, "Arial Black", sans-serif;
  font-weight: 900 !important;
  letter-spacing: 0.02em;
  text-shadow:
    2px 0 0 #fff, -2px 0 0 #fff, 0 2px 0 #fff, 0 -2px 0 #fff,
    1px 1px 0 #fff, -1px -1px 0 #fff, 1px -1px 0 #fff, -1px 1px 0 #fff;
}}
.toa-bubble--vertical {{
  writing-mode: vertical-rl;
  text-orientation: mixed;
}}
""".strip()


def _strip_render_tags(text: str) -> str:
    t = text or ""
    for tag in ("[[DIR:V]]", "[[DIR:H]]", "[[BG:SOLID]]", "[[BG:TRANSPARENT]]"):
        t = t.replace(tag, "")
    return t.strip()


def _sanitize_page_css(page_css: str) -> str:
    css = (page_css or "").strip()
    if not css:
        return DEFAULT_PAGE_CSS
    css = re.sub(
        r"rgba\s*\(\s*255\s*,\s*255\s*,\s*255\s*,\s*0\.\d+\s*\)",
        "transparent",
        css,
        flags=re.IGNORECASE,
    )
    if "ToaManga" not in css and "font-family" not in css:
        return f"{DEFAULT_PAGE_CSS}\n{css}"
    return css


def _bubble_classes(block: TextBlock) -> str:
    classes = ["toa-bubble"]
    _, dir_hint, _ = _extract_render_hints(block.translatedText)
    if is_sfx_block(block):
        classes.append("toa-bubble--sfx")
    if dir_hint == "vertical":
        classes.append("toa-bubble--vertical")
    return " ".join(classes)


def _fallback_bubble_html(translated: str, block: TextBlock) -> str:
    # Max 3 mots / ligne — retour à la ligne forcé.
    body = format_lines_html(_strip_render_tags(translated))
    cls = _bubble_classes(block)
    return f'<div class="{cls}"><p>{body}</p></div>'


def _force_text_only(fragment: str, *, is_sfx: bool = False) -> str:
    """Supprime formes/couleurs Cursor — fond toujours transparent (blanc = CSS)."""
    del is_sfx
    cleaned = fragment or ""
    cleaned = re.sub(
        r"color\s*:\s*[^;\"']+;?",
        f"color: {TRANSLATED_TEXT_COLOR};",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"background(?:-color)?\s*:\s*[^;\"']+;?",
        "background: transparent;",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"border(?:-[a-z]+)?\s*:\s*[^;\"']+;?",
        "border: none;",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"border-radius\s*:\s*[^;\"']+;?",
        "border-radius: 0;",
        cleaned,
        flags=re.IGNORECASE,
    )
    # Conserve la lisibilité : retire filtres colorés, pas le contraste CSS.
    cleaned = re.sub(
        r"(?:filter|box-shadow|outline)\s*:\s*[^;\"']+;?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"opacity\s*:\s*[^;\"']+;?",
        "opacity: 1;",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"""(?:fill|stroke)\s*=\s*['"][^'"]*['"]""",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def _ensure_bubble_class(raw: str, block: TextBlock) -> str:
    """Garantit toa-bubble / toa-bubble--sfx sur le fragment Cursor."""
    cls = _bubble_classes(block)
    if not raw:
        return ""
    if re.search(r'class\s*=\s*["\'][^"\']*toa-bubble', raw, flags=re.IGNORECASE):
        if is_sfx_block(block) and "toa-bubble--sfx" not in raw:
            return re.sub(
                r'(class\s*=\s*["\'][^"\']*toa-bubble)',
                r'\1 toa-bubble--sfx',
                raw,
                count=1,
                flags=re.IGNORECASE,
            )
        return raw
    if "class=" in raw:
        return raw
    return f'<div class="{cls}">{raw}</div>'


def _bubble_inner_html(block: TextBlock) -> str:
    """Toujours depuis la traduction, max 3 mots / ligne (ignore le HTML Cursor)."""
    tr = _strip_render_tags(block.translatedText)
    return _fallback_bubble_html(tr, block) if tr else ""


def collect_bubble_polygons(
    scan_bgr,
    blocks: list[TextBlock],
) -> dict[int, list[tuple[int, int]]]:
    """Polygone exact par bulle de dialogue (None pour SFX)."""
    if scan_bgr is None:
        return {}
    out: dict[int, list[tuple[int, int]]] = {}
    for block in blocks:
        if is_sfx_block(block):
            continue
        try:
            poly = detect_bubble_polygon(scan_bgr, block.boundingBox)
            if poly and len(poly) >= 3:
                out[block.id] = poly
        except Exception as exc:
            logger.debug("Polygone bulle #%s: %s", block.id, exc)
    return out


def build_overlay_html(
    scan_filename: str,
    blocks: list[TextBlock],
    *,
    width: int,
    height: int,
    page_css: str = "",
    scan_path: Path | None = None,
    locate_on: Path | None = None,
    polygons: dict[int, list[tuple[int, int]]] | None = None,
) -> str:
    """Texte + fond CSS ; jamais deux bulles traduites superposées."""
    css = _sanitize_page_css(page_css)
    bubble_layers: list[str] = []

    locate_path = locate_on or scan_path
    if polygons is None and locate_path is not None:
        try:
            import cv2

            scan_bgr = cv2.imread(str(locate_path))
            polygons = collect_bubble_polygons(scan_bgr, blocks)
        except Exception:
            polygons = {}
    polygons = polygons or {}

    # 1) Calculer les boîtes candidates (polygone ou bbox Cursor).
    candidates: list[tuple[TextBlock, BoundingBox, list | None, bool]] = []
    for block in blocks:
        inner = _bubble_inner_html(block)
        if not inner:
            continue
        is_sfx = is_sfx_block(block)
        poly = None if is_sfx else polygons.get(block.id)
        if poly:
            bb = polygon_bbox(shrink_polygon(poly))
        else:
            bb = block.boundingBox
        candidates.append((block, bb, poly, is_sfx))

    if not candidates:
        layers_html = ""
    else:
        # Si deux polygones se marchent dessus, revenir aux bbox Cursor (plus serrées).
        seed_boxes = [c[1] for c in candidates]
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                if boxes_iou(seed_boxes[i], seed_boxes[j]) >= 0.08 or boxes_overlap(
                    seed_boxes[i], seed_boxes[j], gap=2
                ):
                    bi, bj = candidates[i][0], candidates[j][0]
                    seed_boxes[i] = bi.boundingBox
                    seed_boxes[j] = bj.boundingBox
                    candidates[i] = (bi, seed_boxes[i], None, candidates[i][3])
                    candidates[j] = (bj, seed_boxes[j], None, candidates[j][3])

        seed_boxes = [c[1] for c in candidates]
        want_poly = [c[2] is not None and not c[3] for c in candidates]
        placed, keep_poly = resolve_placement_boxes(
            seed_boxes,
            page_w=width,
            page_h=height,
            use_polygon_flags=want_poly,
        )

        for idx, (block, _seed, poly, is_sfx) in enumerate(candidates):
            bb = placed[idx]
            box_w = max(8, bb.x_max - bb.x_min)
            box_h = max(8, bb.y_max - bb.y_min)
            # Cap police = taille originale (bbox Cursor source).
            src_bb = block.boundingBox
            src_w = max(8, src_bb.x_max - src_bb.x_min)
            src_h = max(8, src_bb.y_max - src_bb.y_min)
            translated = _strip_render_tags(block.translatedText)
            font_size = estimate_font_size(
                translated,
                box_w,
                box_h,
                is_sfx=is_sfx,
                original_text=block.originalText or "",
                original_box_w=src_w,
                original_box_h=src_h,
            )
            max_font = estimate_original_font_size(
                block.originalText or translated,
                src_w,
                src_h,
                is_sfx=is_sfx,
            )
            pad = content_inner_pad(translated, box_w, box_h)
            use_poly = (
                poly if (keep_poly[idx] and poly is not None and not is_sfx) else None
            )
            bubble_layers.append(
                build_bubble_wrap(
                    _bubble_inner_html(block),
                    box_x_min=bb.x_min,
                    box_y_min=bb.y_min,
                    box_w=box_w,
                    box_h=box_h,
                    page_width=width,
                    font_size=font_size,
                    polygon=use_poly,
                    is_sfx=is_sfx,
                    inner_pad=pad,
                    max_font_size=max_font,
                    content_text=translated,
                )
            )
        layers_html = "\n".join(bubble_layers)

    safe_scan = html.escape(scan_filename, quote=True)
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8"/>
<style>{css}
{BUBBLE_FIT_CSS}
</style>
</head>
<body style="width:{width}px;height:{height}px;margin:0;">
<div class="page" style="position:relative;width:{width}px;height:{height}px;">
<img class="page-scan" src="{safe_scan}" width="{width}" height="{height}" alt=""/>
<div class="bubble-layer" style="z-index:1000 !important;">
{layers_html}
</div>
</div>
<script>
{BUBBLE_FIT_SCRIPT}
</script>
</body>
</html>"""


_playwright_state = threading.local()


def _get_thread_browser():
    state = getattr(_playwright_state, "state", None)
    browser = state["browser"] if state else None
    if browser is not None and browser.is_connected():
        return browser
    close_thread_browser()
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    _playwright_state.state = {"pw": pw, "browser": browser}
    return browser


def close_thread_browser() -> None:
    state = getattr(_playwright_state, "state", None)
    if not state:
        return
    _playwright_state.state = None
    try:
        state["browser"].close()
    except Exception:
        pass
    try:
        state["pw"].stop()
    except Exception:
        pass


def _screenshot_html(html_path: Path, output_path: Path, width: int, height: int) -> None:
    uri = html_path.resolve().as_uri()

    def _capture() -> None:
        browser = _get_thread_browser()
        page = browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
        )
        try:
            page.goto(uri, wait_until="load")
            page.wait_for_function(
                """() => {
                    const img = document.querySelector('img.page-scan');
                    return img && img.complete && img.naturalWidth > 0
                        && window.__toaFitDone === true;
                }""",
                timeout=30_000,
            )
            page.wait_for_timeout(200)
            page.screenshot(path=str(output_path), full_page=False)
        finally:
            page.close()

    try:
        _capture()
    except Exception:
        close_thread_browser()
        _capture()


def render_page_html_overlays(
    scan_path: Path,
    blocks: list[TextBlock],
    output_path: Path,
    *,
    page_css: str = "",
) -> None:
    """Superposition pure : scan intact + texte traduit par-dessus (aucun effacement)."""
    from PIL import Image
    import cv2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(scan_path) as img:
        width, height = img.size

    if not blocks:
        shutil.copy2(scan_path, output_path)
        return

    blocks = refine_blocks_for_render(scan_path, blocks)

    original_bgr = cv2.imread(str(scan_path))
    polygons = collect_bubble_polygons(original_bgr, blocks)

    html_path = output_path.with_suffix(".overlay.html")
    scan_asset = html_path.with_name(f"{html_path.stem}_scan.png")
    # Ne jamais modifier le dessin : copie bit-à-bit du scan original.
    shutil.copy2(scan_path, scan_asset)

    html_doc = build_overlay_html(
        scan_asset.name,
        blocks,
        width=width,
        height=height,
        page_css=page_css,
        scan_path=scan_asset,
        locate_on=scan_path,
        polygons=polygons,
    )
    html_path.write_text(html_doc, encoding="utf-8")

    try:
        _screenshot_html(html_path, output_path, width, height)
        logger.info(
            "Page composée (superposition seule, %s bulles): %s",
            len(polygons),
            output_path.name,
        )
    except Exception as exc:
        logger.warning("Playwright indisponible (%s), repli PIL", exc)
        from services import rendering

        rendering.inpaint_and_render(scan_path, blocks, output_path)
