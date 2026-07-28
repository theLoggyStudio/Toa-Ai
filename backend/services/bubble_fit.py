"""Superposition stricte : texte clipé au polygone exact de la bulle manga.

Règles (aucune exception pour le dialogue) :
1) Localiser le contour exact de la bulle (OpenCV / detect_bubble_polygon).
2) Ne JAMAIS redessiner forme, bordure ni fond — la bulle originale reste.
3) Clipper le texte au polygone (CSS clip-path).
4) Seule variable autorisée : la taille de police (fit binaire).
"""

from __future__ import annotations

import logging
import math
from typing import Sequence

import cv2
import numpy as np

from models import BoundingBox

logger = logging.getLogger(__name__)

Point = tuple[int, int]

TRANSLATED_TEXT_COLOR = "#4A3F35"
TRANSLATED_TEXT_RGB = (0x4A, 0x3F, 0x35)

# Marge intérieure : le texte reste toujours un peu à l'écart du trait noir.
POLYGON_SHRINK = 0.82
INNER_PAD_PX = 7
TEXT_PAD_CSS = "7px 9px"

BUBBLE_FIT_CSS = f"""
.toa-bubble-wrap {{
  position: absolute;
  left: 0;
  top: 0;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  overflow: hidden;
  /* Pas de fond / bordure : on n'ajoute aucune forme. */
  background: transparent;
  border: none;
}}
.toa-bubble-wrap--poly {{
  /* clip-path fourni en inline (polygone exact de la bulle). */
}}
.toa-bubble-wrap .toa-bubble {{
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  box-sizing: border-box;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  background: transparent !important;
  border: none !important;
  border-radius: 0 !important;
  padding: {TEXT_PAD_CSS};
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
  color: {TRANSLATED_TEXT_COLOR} !important;
}}
.toa-bubble-wrap .toa-bubble,
.toa-bubble-wrap .toa-bubble * {{
  color: {TRANSLATED_TEXT_COLOR} !important;
  background: transparent !important;
  border: none !important;
}}
.toa-bubble-wrap .toa-bubble--sfx {{
  width: 100%;
  height: 100%;
  text-shadow: 1px 1px 0 #fff, -1px -1px 0 #fff;
}}
.toa-bubble-wrap .toa-bubble--vertical {{
  writing-mode: vertical-rl;
  text-orientation: mixed;
}}
""".strip()

# Seule la police change. Le wrap a déjà la taille/forme de la bulle originale.
BUBBLE_FIT_SCRIPT = """
document.querySelectorAll('.toa-bubble-wrap').forEach((wrap) => {
  const el = wrap.querySelector('.toa-bubble') || wrap.firstElementChild;
  if (!el) return;
  const isSfx = !!wrap.querySelector('.toa-bubble--sfx');
  const capSize = isSfx ? 42 : 28;
  let size = parseFloat(getComputedStyle(el).fontSize) || 14;
  const setSize = (s) => { size = s; el.style.fontSize = s.toFixed(1) + 'px'; };
  const overflows = () =>
    el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1;

  // Recherche : agrandir puis réduire — seule la police varie.
  let guard = 50;
  while (guard-- > 0 && size < capSize) {
    setSize(size + 1);
    if (overflows()) { setSize(size - 1); break; }
  }
  guard = 160;
  while (guard-- > 0 && size > 5 && overflows()) setSize(size - 0.5);
});
window.__toaFitDone = true;
""".strip()


def polygon_bbox(points: Sequence[Point]) -> BoundingBox:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return BoundingBox(
        x_min=min(xs), y_min=min(ys), x_max=max(xs) + 1, y_max=max(ys) + 1
    )


def polygon_centroid(points: Sequence[Point]) -> tuple[float, float]:
    if not points:
        return 0.0, 0.0
    area = 0.0
    cx = 0.0
    cy = 0.0
    n = len(points)
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    area *= 0.5
    if abs(area) < 1e-6:
        return (
            sum(p[0] for p in points) / n,
            sum(p[1] for p in points) / n,
        )
    cx /= 6.0 * area
    cy /= 6.0 * area
    return cx, cy


def shrink_polygon(
    points: Sequence[Point],
    *,
    factor: float = POLYGON_SHRINK,
) -> list[Point]:
    """Resserre le polygone vers son centroïde (marge intérieure du trait)."""
    if len(points) < 3:
        return list(points)
    cx, cy = polygon_centroid(points)
    out: list[Point] = []
    for x, y in points:
        nx = int(round(cx + (x - cx) * factor))
        ny = int(round(cy + (y - cy) * factor))
        out.append((nx, ny))
    return out


def polygon_to_clip_path(
    points: Sequence[Point],
    *,
    origin_x: int,
    origin_y: int,
) -> str:
    """clip-path CSS avec coords relatives au wrap (coin haut-gauche AABB)."""
    parts = [f"{x - origin_x}px {y - origin_y}px" for x, y in points]
    return "polygon(" + ", ".join(parts) + ")"


def estimate_font_size(
    text: str,
    box_w: int,
    box_h: int,
    *,
    is_sfx: bool = False,
) -> int:
    length = max(1, len((text or "").strip()))
    if is_sfx:
        return max(11, min(36, int(box_h * 0.38)))
    area = max(1, box_w * box_h)
    size = int(math.sqrt(area / (length * 0.62 * 1.30)))
    return max(8, min(24, size))


def build_bubble_wrap(
    inner_html: str,
    *,
    box_x_min: int,
    box_y_min: int,
    box_w: int,
    box_h: int,
    page_width: int,
    font_size: int,
    polygon: Sequence[Point] | None = None,
) -> str:
    """Zone texte = AABB de la bulle ; forme = clip-path polygone exact.

    Aucune nouvelle bulle n'est dessinée : fond transparent + clip uniquement.
    Une petite marge intérieure est toujours appliquée.
    """
    if polygon and len(polygon) >= 3:
        text_poly = shrink_polygon(polygon)
        bb = polygon_bbox(text_poly)
        # Marge pixel supplémentaire autour du polygone déjà resserré.
        x0 = max(0, bb.x_min + INNER_PAD_PX // 2)
        y0 = max(0, bb.y_min + INNER_PAD_PX // 2)
        x1 = max(x0 + 12, bb.x_max - INNER_PAD_PX // 2)
        y1 = max(y0 + 12, bb.y_max - INNER_PAD_PX // 2)
        w = x1 - x0
        h = y1 - y0
        clip = polygon_to_clip_path(text_poly, origin_x=x0, origin_y=y0)
        style = (
            f"left:{x0}px;top:{y0}px;width:{w}px;height:{h}px;"
            f"clip-path:{clip};-webkit-clip-path:{clip};"
        )
        return (
            f'<div class="toa-bubble-wrap toa-bubble-wrap--poly" '
            f'data-w="{w}" data-h="{h}" style="{style}">'
            f'<div style="font-size:{font_size}px;width:100%;height:100%;">'
            f"{inner_html}</div></div>"
        )

    # Repli SFX / sans polygone : AABB avec petite marge intérieure.
    pad = INNER_PAD_PX
    x0 = box_x_min + pad
    y0 = box_y_min + pad
    max_w = max(12, min(box_w - 2 * pad, max(40, page_width - 16)))
    max_h = max(12, box_h - 2 * pad)
    style = (
        f"left:{x0}px;top:{y0}px;"
        f"width:{max_w}px;height:{max_h}px;"
    )
    return (
        f'<div class="toa-bubble-wrap" data-w="{max_w}" data-h="{max_h}" '
        f'style="{style}">'
        f'<div style="font-size:{font_size}px;width:100%;height:100%;">'
        f"{inner_html}</div></div>"
    )


def locate_bubble_interior(
    bgr: np.ndarray,
    bb: BoundingBox,
    *,
    white_thresh: int = 242,
) -> BoundingBox:
    """AABB de la bulle (repli) — préférer detect_bubble_polygon pour la forme."""
    from services.rendering import detect_bubble_polygon

    poly = detect_bubble_polygon(bgr, bb)
    if poly and len(poly) >= 3:
        return polygon_bbox(shrink_polygon(poly))
    # Repli AABB inset
    h, w = bgr.shape[:2]
    return BoundingBox(
        x_min=max(0, bb.x_min + INNER_PAD_PX),
        y_min=max(0, bb.y_min + INNER_PAD_PX),
        x_max=min(w, bb.x_max - INNER_PAD_PX),
        y_max=min(h, bb.y_max - INNER_PAD_PX),
    )
