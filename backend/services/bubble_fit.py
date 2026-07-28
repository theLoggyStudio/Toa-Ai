"""Superposition stricte : fond blanc CSS + texte traduit prioritaire.

Règles :
0) PRIORITÉ ABSOLUE : lisibilité du texte traduit (contraste, taille, z-index).
1) Localiser la bulle (OpenCV) pour ancrer la zone.
2) Ne JAMAIS modifier le dessin (pas d'effacement / inpaint).
3) Fond blanc CSS (ellipse ou clip-path) sous le texte.
4) Seule variable de fit : la taille de police (plancher de lisibilité).
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

# Contraste max sur fond blanc manga.
TRANSLATED_TEXT_COLOR = "#111111"
TRANSLATED_TEXT_RGB = (0x11, 0x11, 0x11)

TEXT_HALO_CSS = (
    "0 0 2px #fff, 1px 0 0 #fff, -1px 0 0 #fff, 0 1px 0 #fff, 0 -1px 0 #fff"
)

POLYGON_SHRINK = 0.85
INNER_PAD_PX = 6
TEXT_PAD_CSS = "5px 7px"
MIN_READABLE_PX = 9

BUBBLE_FIT_CSS = f"""
.toa-bubble-wrap {{
  position: absolute;
  left: 0;
  top: 0;
  z-index: 1000 !important;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  overflow: hidden;
  background: transparent !important;
  border: none;
}}
.toa-bubble-bg {{
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background: #ffffff !important;
  border: none;
  opacity: 1 !important;
}}
.toa-bubble-wrap--ellipse .toa-bubble-bg {{
  border-radius: 50%;
}}
.toa-bubble-wrap--poly .toa-bubble-bg {{
  border-radius: 0;
}}
.toa-bubble-wrap--sfx .toa-bubble-bg {{
  display: none !important;
  background: transparent !important;
}}
.toa-bubble-wrap .toa-bubble-fit {{
  position: relative;
  z-index: 2;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}}
.toa-bubble-wrap .toa-bubble {{
  position: relative;
  z-index: 3 !important;
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
  border-radius: 0;
  padding: {TEXT_PAD_CSS};
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
  color: {TRANSLATED_TEXT_COLOR} !important;
  font-weight: 800 !important;
  opacity: 1 !important;
  text-shadow: {TEXT_HALO_CSS};
}}
.toa-bubble-wrap .toa-bubble,
.toa-bubble-wrap .toa-bubble * {{
  color: {TRANSLATED_TEXT_COLOR} !important;
  border: none !important;
  background: transparent !important;
  opacity: 1 !important;
  font-weight: 800 !important;
  text-shadow: {TEXT_HALO_CSS};
}}
.toa-bubble-wrap .toa-bubble p {{
  background: transparent !important;
  margin: 0;
  opacity: 1 !important;
}}
.toa-bubble-wrap .toa-bubble--sfx {{
  width: max-content;
  height: max-content;
  max-width: 100%;
  max-height: 100%;
  background: transparent !important;
  border-radius: 0;
  font-weight: 900 !important;
  text-shadow:
    2px 0 0 #fff, -2px 0 0 #fff, 0 2px 0 #fff, 0 -2px 0 #fff,
    1px 1px 0 #fff, -1px -1px 0 #fff, 1px -1px 0 #fff, -1px 1px 0 #fff;
}}
.toa-bubble-wrap .toa-bubble--vertical {{
  writing-mode: vertical-rl;
  text-orientation: mixed;
}}
""".strip()

BUBBLE_FIT_SCRIPT = f"""
document.querySelectorAll('.toa-bubble-wrap').forEach((wrap) => {{
  const el = wrap.querySelector('.toa-bubble') || wrap.querySelector('.toa-bubble-fit')?.firstElementChild;
  if (!el) return;
  const isSfx = wrap.classList.contains('toa-bubble-wrap--sfx')
    || !!wrap.querySelector('.toa-bubble--sfx');
  const maxW = wrap.clientWidth || parseFloat(wrap.dataset.w) || 60;
  const maxH = wrap.clientHeight || parseFloat(wrap.dataset.h) || 40;
  const capSize = isSfx ? 44 : 30;
  const minSize = {MIN_READABLE_PX};
  let size = parseFloat(getComputedStyle(el).fontSize) || 14;
  const setSize = (s) => {{ size = s; el.style.fontSize = s.toFixed(1) + 'px'; }};
  const overflows = () => {{
    if (isSfx) {{
      const r = el.getBoundingClientRect();
      return r.width > maxW + 1 || r.height > maxH + 1;
    }}
    return el.scrollWidth > el.clientWidth + 1
      || el.scrollHeight > el.clientHeight + 1;
  }};

  let guard = 50;
  while (guard-- > 0 && size < capSize) {{
    setSize(size + 1);
    if (overflows()) {{ setSize(size - 1); break; }}
  }}
  guard = 160;
  while (guard-- > 0 && size > minSize && overflows()) setSize(size - 0.5);
  if (size < minSize) setSize(minSize);
}});
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
        return max(12, min(40, int(box_h * 0.42)))
    area = max(1, box_w * box_h)
    size = int(math.sqrt(area / (length * 0.55 * 1.20)))
    return max(MIN_READABLE_PX, min(28, size))


def _box_area(bb: BoundingBox) -> int:
    return max(0, bb.x_max - bb.x_min) * max(0, bb.y_max - bb.y_min)


def boxes_iou(a: BoundingBox, b: BoundingBox) -> float:
    ix0 = max(a.x_min, b.x_min)
    iy0 = max(a.y_min, b.y_min)
    ix1 = min(a.x_max, b.x_max)
    iy1 = min(a.y_max, b.y_max)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    if inter <= 0:
        return 0.0
    union = _box_area(a) + _box_area(b) - inter
    return float(inter) / max(1, union)


def boxes_overlap(a: BoundingBox, b: BoundingBox, *, gap: int = 6) -> bool:
    """True si les boîtes se touchent ou se chevauchent (avec marge gap)."""
    return not (
        a.x_max + gap <= b.x_min
        or b.x_max + gap <= a.x_min
        or a.y_max + gap <= b.y_min
        or b.y_max + gap <= a.y_min
    )


def _shift_box(
    bb: BoundingBox,
    *,
    dx: int = 0,
    dy: int = 0,
    page_w: int,
    page_h: int,
) -> BoundingBox:
    w = bb.x_max - bb.x_min
    h = bb.y_max - bb.y_min
    x0 = max(0, min(bb.x_min + dx, max(0, page_w - w)))
    y0 = max(0, min(bb.y_min + dy, max(0, page_h - h)))
    return BoundingBox(x_min=x0, y_min=y0, x_max=x0 + w, y_max=y0 + h)


def separate_overlapping_boxes(
    boxes: list[BoundingBox],
    *,
    page_w: int,
    page_h: int,
    gap: int = 10,
    max_iters: int = 40,
) -> list[BoundingBox]:
    """Écarte les boîtes qui se superposent (priorité : ne jamais empiler 2 textes).

    Stratégie : pour chaque paire en conflit, pousser la boîte de droite / du bas
    juste assez pour respecter `gap` pixels entre elles.
    """
    if len(boxes) < 2:
        return list(boxes)

    out = [
        BoundingBox(x_min=b.x_min, y_min=b.y_min, x_max=b.x_max, y_max=b.y_max)
        for b in boxes
    ]

    for _ in range(max_iters):
        moved = False
        # Traiter de gauche à droite / haut en bas : ancrer la 1re, déplacer la 2e.
        order = sorted(range(len(out)), key=lambda i: (out[i].y_min, out[i].x_min))
        for ai in range(len(order)):
            for bi in range(ai + 1, len(order)):
                i, j = order[ai], order[bi]
                a, b = out[i], out[j]
                if not boxes_overlap(a, b, gap=gap):
                    continue
                # Direction préférée : horizontal si plus « côte à côte », sinon vertical.
                a_cx = (a.x_min + a.x_max) / 2
                b_cx = (b.x_min + b.x_max) / 2
                a_cy = (a.y_min + a.y_max) / 2
                b_cy = (b.y_min + b.y_max) / 2
                prefer_x = abs(b_cx - a_cx) >= abs(b_cy - a_cy) * 0.55

                if prefer_x:
                    # Pousser b à droite de a (ou a à gauche de b si b est déjà à gauche).
                    if b_cx >= a_cx:
                        need = (a.x_max + gap) - b.x_min
                        if need > 0:
                            out[j] = _shift_box(b, dx=need, page_w=page_w, page_h=page_h)
                            moved = True
                    else:
                        need = (b.x_max + gap) - a.x_min
                        if need > 0:
                            out[i] = _shift_box(a, dx=need, page_w=page_w, page_h=page_h)
                            moved = True
                else:
                    if b_cy >= a_cy:
                        need = (a.y_max + gap) - b.y_min
                        if need > 0:
                            out[j] = _shift_box(b, dy=need, page_w=page_w, page_h=page_h)
                            moved = True
                    else:
                        need = (b.y_max + gap) - a.y_min
                        if need > 0:
                            out[i] = _shift_box(a, dy=need, page_w=page_w, page_h=page_h)
                            moved = True
        if not moved:
            break
    return out


def resolve_placement_boxes(
    seed_boxes: list[BoundingBox],
    *,
    page_w: int,
    page_h: int,
    use_polygon_flags: list[bool] | None = None,
) -> tuple[list[BoundingBox], list[bool]]:
    """Sépare les placements ; si conflit fort, abandonne le polygone (ovale AABB)."""
    n = len(seed_boxes)
    keep_poly = list(use_polygon_flags) if use_polygon_flags is not None else [True] * n
    if n < 2:
        return list(seed_boxes), keep_poly

    # Si deux polygones/bboxes se chevauchent beaucoup → AABB séparés sans clip polygone.
    for i in range(n):
        for j in range(i + 1, n):
            if boxes_iou(seed_boxes[i], seed_boxes[j]) >= 0.12 or boxes_overlap(
                seed_boxes[i], seed_boxes[j], gap=4
            ):
                keep_poly[i] = False
                keep_poly[j] = False

    separated = separate_overlapping_boxes(
        seed_boxes, page_w=page_w, page_h=page_h, gap=10
    )
    # Tout déplacement invalide le clip polygone d'origine.
    for i in range(n):
        if (
            separated[i].x_min != seed_boxes[i].x_min
            or separated[i].y_min != seed_boxes[i].y_min
        ):
            keep_poly[i] = False
    return separated, keep_poly


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
    is_sfx: bool = False,
) -> str:
    """Ancre la zone + superpose un fond blanc CSS (ovale ou clip polygone)."""
    bg = "" if is_sfx else '<div class="toa-bubble-bg" aria-hidden="true"></div>'
    fit_open = f'<div class="toa-bubble-fit" style="font-size:{font_size}px;">'
    fit_close = "</div>"

    if is_sfx:
        pad = INNER_PAD_PX
        x0 = box_x_min + pad
        y0 = box_y_min + pad
        max_w = max(12, min(box_w - 2 * pad, max(40, page_width - 16)))
        max_h = max(12, box_h - 2 * pad)
        style = f"left:{x0}px;top:{y0}px;width:{max_w}px;height:{max_h}px;"
        return (
            f'<div class="toa-bubble-wrap toa-bubble-wrap--sfx" '
            f'data-w="{max_w}" data-h="{max_h}" style="{style}">'
            f"{fit_open}{inner_html}{fit_close}</div>"
        )

    if polygon and len(polygon) >= 3:
        text_poly = shrink_polygon(polygon)
        bb = polygon_bbox(text_poly)
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
            f"{bg}{fit_open}{inner_html}{fit_close}</div>"
        )

    # Repli dialogue sans polygone : ovale CSS (border-radius 50%).
    pad = INNER_PAD_PX
    x0 = box_x_min + pad
    y0 = box_y_min + pad
    max_w = max(12, min(box_w - 2 * pad, max(40, page_width - 16)))
    max_h = max(12, box_h - 2 * pad)
    style = f"left:{x0}px;top:{y0}px;width:{max_w}px;height:{max_h}px;"
    return (
        f'<div class="toa-bubble-wrap toa-bubble-wrap--ellipse" '
        f'data-w="{max_w}" data-h="{max_h}" style="{style}">'
        f"{bg}{fit_open}{inner_html}{fit_close}</div>"
    )


def locate_bubble_interior(
    bgr: np.ndarray,
    bb: BoundingBox,
    *,
    white_thresh: int = 242,
) -> BoundingBox:
    """AABB de la bulle (repli) — préférer detect_bubble_polygon pour la forme."""
    del white_thresh
    from services.rendering import detect_bubble_polygon

    poly = detect_bubble_polygon(bgr, bb)
    if poly and len(poly) >= 3:
        return polygon_bbox(shrink_polygon(poly))
    h, w = bgr.shape[:2]
    return BoundingBox(
        x_min=max(0, bb.x_min + INNER_PAD_PX),
        y_min=max(0, bb.y_min + INNER_PAD_PX),
        x_max=min(w, bb.x_max - INNER_PAD_PX),
        y_max=min(h, bb.y_max - INNER_PAD_PX),
    )
