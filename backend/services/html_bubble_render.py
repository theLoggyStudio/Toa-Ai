"""Composition scan + bulles HTML/CSS positionnées (coordonnées Cursor)."""

from __future__ import annotations

import html
import logging
import re
import shutil
from pathlib import Path

from models import TextBlock
from services.rendering import (
    _extract_render_hints,
    _is_round_bubble,
    _looks_like_sfx_text,
    erase_text_regions,
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
.bubble-layer {{ position: absolute; inset: 0; z-index: 1; pointer-events: none; }}
.toa-bubble {{
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  background: rgb(255, 255, 255);
  border: 1px solid rgba(40, 28, 22, 0.45);
  border-radius: 10px;
  padding: 5px 7px;
  color: #1a1410;
  font-family: "ToaManga", "Yu Gothic", "Segoe UI", sans-serif;
  font-weight: 700;
  line-height: 1.22;
  word-wrap: break-word;
}}
.toa-bubble--round {{ border-radius: 50%; }}
.toa-bubble--sfx {{
  background: transparent;
  border: none;
  font-family: "ToaSFX", Impact, "Arial Black", sans-serif;
  font-weight: 900;
  letter-spacing: 0.02em;
  color: #111;
  text-shadow: 1px 1px 0 #fff, -1px -1px 0 #fff;
}}
.toa-bubble--vertical {{
  writing-mode: vertical-rl;
  text-orientation: mixed;
}}
.toa-bubble p {{ margin: 0; }}
""".strip()


def _strip_render_tags(text: str) -> str:
    t = text or ""
    for tag in ("[[DIR:V]]", "[[DIR:H]]", "[[BG:SOLID]]", "[[BG:TRANSPARENT]]"):
        t = t.replace(tag, "")
    return t.strip()


def _sanitize_page_css(page_css: str) -> str:
    """Ignore le CSS Cursor semi-transparent ; garde nos styles par defaut."""
    css = (page_css or "").strip()
    if not css:
        return DEFAULT_PAGE_CSS
    css = re.sub(
        r"rgba\s*\(\s*255\s*,\s*255\s*,\s*255\s*,\s*0\.\d+\s*\)",
        "rgb(255, 255, 255)",
        css,
        flags=re.IGNORECASE,
    )
    if "ToaManga" not in css and "font-family" not in css:
        return f"{DEFAULT_PAGE_CSS}\n{css}"
    return css


def _bubble_classes(block: TextBlock, scan_path: Path | None = None) -> str:
    classes = ["toa-bubble"]
    _, dir_hint, bg_hint = _extract_render_hints(block.translatedText)
    is_sfx = bg_hint is False or _looks_like_sfx_text(block.originalText)
    if is_sfx:
        classes.append("toa-bubble--sfx")
        classes.append("toa-bubble--vertical")
    elif dir_hint == "vertical":
        classes.append("toa-bubble--vertical")
    if scan_path and not is_sfx:
        try:
            import cv2

            bgr = cv2.imread(str(scan_path))
            if bgr is not None and _is_round_bubble(bgr, block.boundingBox):
                classes.append("toa-bubble--round")
        except Exception:
            pass
    return " ".join(classes)


def _fallback_bubble_html(translated: str, block: TextBlock, scan_path: Path | None) -> str:
    safe = html.escape(_strip_render_tags(translated))
    cls = _bubble_classes(block, scan_path)
    return f'<div class="{cls}"><p>{safe}</p></div>'


def _bubble_inner_html(
    block: TextBlock,
    scan_path: Path | None = None,
) -> str:
    raw = (block.bubbleHtml or "").strip()
    if raw:
        if "class=" in raw:
            return raw
        cls = _bubble_classes(block, scan_path)
        return f'<div class="{cls}">{raw}</div>'
    tr = _strip_render_tags(block.translatedText)
    return _fallback_bubble_html(tr, block, scan_path) if tr else ""


def build_overlay_html(
    scan_filename: str,
    blocks: list[TextBlock],
    *,
    width: int,
    height: int,
    page_css: str = "",
    scan_path: Path | None = None,
) -> str:
    """scan_filename : chemin relatif vers le PNG/JPEG à côté du fichier HTML."""
    css = _sanitize_page_css(page_css)
    bubble_layers: list[str] = []

    for block in blocks:
        inner = _bubble_inner_html(block, scan_path)
        if not inner:
            continue
        bb = block.boundingBox
        left, top = bb.x_min, bb.y_min
        box_w = max(8, bb.x_max - bb.x_min)
        box_h = max(8, bb.y_max - bb.y_min)
        is_sfx = _looks_like_sfx_text(block.originalText)
        font_size = (
            f"clamp(11px, {max(12, int(box_h * 0.14))}px, 42px)"
            if is_sfx
            else f"clamp(9px, {max(10, int(min(box_w, box_h) * 0.11))}px, 24px)"
        )
        bubble_layers.append(
            f'<div class="toa-bubble-wrap" style="left:{left}px;top:{top}px;'
            f'width:{box_w}px;height:{box_h}px;position:absolute;">'
            f'<div style="width:100%;height:100%;font-size:{font_size};">{inner}</div></div>'
        )

    layers_html = "\n".join(bubble_layers)
    safe_scan = html.escape(scan_filename, quote=True)
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8"/>
<style>{css}
.toa-bubble-wrap {{ position: absolute; pointer-events: none; overflow: visible; }}
.toa-bubble-wrap .toa-bubble {{
  width: 100%; height: 100%;
  box-sizing: border-box;
}}
</style>
</head>
<body style="width:{width}px;height:{height}px;margin:0;">
<div class="page" style="position:relative;width:{width}px;height:{height}px;">
<img class="page-scan" src="{safe_scan}" width="{width}" height="{height}" alt=""/>
<div class="bubble-layer">
{layers_html}
</div>
</div>
</body>
</html>"""


def _screenshot_html(html_path: Path, output_path: Path, width: int, height: int) -> None:
    from playwright.sync_api import sync_playwright

    uri = html_path.resolve().as_uri()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
            )
            page.goto(uri, wait_until="load")
            page.wait_for_function(
                """() => {
                    const img = document.querySelector('img.page-scan');
                    return img && img.complete && img.naturalWidth > 0;
                }""",
                timeout=30_000,
            )
            page.wait_for_timeout(200)
            page.screenshot(path=str(output_path), full_page=False)
        finally:
            browser.close()


def render_page_html_overlays(
    scan_path: Path,
    blocks: list[TextBlock],
    output_path: Path,
    *,
    page_css: str = "",
) -> None:
    """Efface le texte source puis place les bulles HTML/CSS aux coordonnees Cursor."""
    from PIL import Image

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(scan_path) as img:
        width, height = img.size

    if not blocks:
        shutil.copy2(scan_path, output_path)
        return

    blocks = refine_blocks_for_render(scan_path, blocks)

    html_path = output_path.with_suffix(".overlay.html")
    scan_asset = html_path.with_name(f"{html_path.stem}_scan.png")

    erase_text_regions(scan_path, blocks, scan_asset)

    html_doc = build_overlay_html(
        scan_asset.name,
        blocks,
        width=width,
        height=height,
        page_css=page_css,
        scan_path=scan_asset,
    )
    html_path.write_text(html_doc, encoding="utf-8")

    try:
        _screenshot_html(html_path, output_path, width, height)
        logger.info("Page composée via HTML/CSS: %s", output_path.name)
    except Exception as exc:
        logger.warning("Playwright indisponible (%s), repli PIL", exc)
        from services import rendering

        rendering.inpaint_and_render(scan_path, blocks, output_path)
