"""Superposition stricte : fond blanc CSS + texte traduit prioritaire.

Règles :
0) PRIORITÉ ABSOLUE : lisibilité du texte traduit (contraste, taille, z-index).
1) Localiser la bulle (OpenCV) pour ancrer la zone.
2) Ne JAMAIS modifier le dessin (pas d'effacement / inpaint).
3) Fond blanc CSS (ellipse ou clip-path) sous le texte.
4) Marges réduites selon le contenu ; taille max = police originale estimée.
5) Au plus 3 mots par ligne ; un mot de 6+ lettres est seul sur sa ligne.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Sequence

import cv2
import numpy as np

from models import BoundingBox

logger = logging.getLogger(__name__)

Point = tuple[int, int]

TRANSLATED_TEXT_COLOR = "#4A3F35"
TRANSLATED_TEXT_RGB = (0x4A, 0x3F, 0x35)

TEXT_HALO_CSS = (
    "0 0 2px #fff, 1px 0 0 #fff, -1px 0 0 #fff, 0 1px 0 #fff, 0 -1px 0 #fff"
)

POLYGON_SHRINK = 0.90
INNER_PAD_PX = 3
TEXT_PAD_CSS = "2px 4px"
MIN_READABLE_PX = 9
MIN_PAD_PX = 2
MAX_PAD_PX = 5
MAX_WORDS_PER_LINE = 3
# Un mot de 6 lettres ou plus occupe une ligne à lui seul.
LONG_WORD_MIN_LETTERS = 6


def _word_letter_count(word: str) -> int:
    """Nombre de lettres (ponctuation ignorée)."""
    return len(re.findall(r"[A-Za-zÀ-ÿ]", word or ""))


def _is_long_word(word: str, *, min_letters: int = LONG_WORD_MIN_LETTERS) -> bool:
    return _word_letter_count(word) >= min_letters


def wrap_max_words_per_line(
    text: str,
    *,
    max_words: int = MAX_WORDS_PER_LINE,
    long_word_letters: int = LONG_WORD_MIN_LETTERS,
) -> list[str]:
    """Découpe le texte :
    - au plus `max_words` mots courts par ligne ;
    - un mot ≥ long_word_letters lettres est seul sur sa ligne.
    """
    words = re.findall(r"\S+", (text or "").strip())
    if not words:
        return []
    limit = max(1, int(max_words))
    lines: list[str] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        if current:
            lines.append(" ".join(current))
            current = []

    for word in words:
        if _is_long_word(word, min_letters=long_word_letters):
            flush()
            lines.append(word)
            continue
        if len(current) >= limit:
            flush()
        current.append(word)
    flush()
    return lines


def format_lines_html(text: str, *, max_words: int = MAX_WORDS_PER_LINE) -> str:
    """HTML échappé avec <br/> entre les lignes (max 3 mots / longs seuls)."""
    import html as html_mod

    lines = wrap_max_words_per_line(text, max_words=max_words)
    return "<br/>".join(html_mod.escape(line) for line in lines)

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
  background: rgba(255, 255, 255, 0.5) !important;
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
  padding: var(--toa-pad, {TEXT_PAD_CSS});
  white-space: normal;
  /* Jamais couper un mot : wrap aux espaces seulement ; le fit réduit la police. */
  word-break: normal;
  overflow-wrap: normal;
  hyphens: none;
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
  word-break: normal !important;
  overflow-wrap: normal !important;
  hyphens: none !important;
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

# Fit : plafond = data-max-font (police originale), plancher = lisibilité.
BUBBLE_FIT_SCRIPT = f"""
document.querySelectorAll('.toa-bubble-wrap').forEach((wrap) => {{
  const el = wrap.querySelector('.toa-bubble') || wrap.querySelector('.toa-bubble-fit')?.firstElementChild;
  if (!el) return;
  const isSfx = wrap.classList.contains('toa-bubble-wrap--sfx')
    || !!wrap.querySelector('.toa-bubble--sfx');
  const maxW = wrap.clientWidth || parseFloat(wrap.dataset.w) || 60;
  const maxH = wrap.clientHeight || parseFloat(wrap.dataset.h) || 40;
  const originalCap = parseFloat(wrap.dataset.maxFont) || (isSfx ? 36 : 24);
  const capSize = Math.max({MIN_READABLE_PX}, originalCap);
  const minSize = {MIN_READABLE_PX};
  let size = parseFloat(getComputedStyle(el).fontSize) || Math.min(14, capSize);
  if (size > capSize) size = capSize;
  const setSize = (s) => {{ size = s; el.style.fontSize = s.toFixed(1) + 'px'; }};
  const overflows = () => {{
    // Déborde = trop grand pour la bulle → on réduit la police (jamais couper un mot).
    if (isSfx) {{
      const r = el.getBoundingClientRect();
      return r.width > maxW + 1 || r.height > maxH + 1;
    }}
    if (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1) {{
      return true;
    }}
    // Mot trop long pour la largeur (sans césure) → forcer une police plus petite.
    const words = (el.innerText || '').split(/\\s+/).filter(Boolean);
    if (!words.length) return false;
    const probe = document.createElement('span');
    probe.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;font:' + getComputedStyle(el).font;
    document.body.appendChild(probe);
    let tooWide = false;
    for (const w of words) {{
      probe.textContent = w;
      if (probe.offsetWidth > maxW - 4) {{ tooWide = true; break; }}
    }}
    probe.remove();
    return tooWide;
  }};

  setSize(size);
  let guard = 50;
  while (guard-- > 0 && size < capSize) {{
    setSize(size + 1);
    if (overflows()) {{ setSize(size - 1); break; }}
  }}
  guard = 160;
  while (guard-- > 0 && size > minSize && overflows()) setSize(size - 0.5);
  if (size < minSize) setSize(minSize);
  if (size > capSize) setSize(capSize);
}});
window.__toaFitDone = true;
""".strip()


def content_inner_pad(text: str, box_w: int, box_h: int) -> int:
    """Marge intérieure réduite selon la densité du contenu (court = plus serré)."""
    length = max(1, len((text or "").strip()))
    area = max(1, box_w * box_h)
    density = length / (area / 400.0)
    if density < 0.35 or length <= 12:
        return MIN_PAD_PX
    if density < 0.9 or length <= 40:
        return 3
    if length <= 80:
        return 4
    return MAX_PAD_PX


def estimate_original_font_size(
    original_text: str,
    box_w: int,
    box_h: int,
    *,
    is_sfx: bool = False,
) -> int:
    """Taille approximative de la police source d'après la bbox + texte original."""
    raw = (original_text or "").strip()
    bw = max(8, box_w)
    bh = max(8, box_h)
    if not raw:
        return max(MIN_READABLE_PX, min(28, int(bh * 0.35)))

    explicit = [ln for ln in re.split(r"[\n\r]+", raw) if ln.strip()]
    n_lines = max(1, len(explicit)) if len(explicit) > 1 else 1
    if n_lines == 1:
        if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", raw):
            char_w = max(8.0, bw / max(1, min(len(raw), 14)))
            n_lines = max(1, min(len(raw), int(round(bh / max(8.0, char_w * 0.9)))))
        else:
            cpl = max(4, int(bw / 9))
            n_lines = max(1, min(8, (len(raw) + cpl - 1) // cpl))

    line_h = bh / max(1, n_lines)
    size = int(round(line_h * (0.88 if not is_sfx else 0.95)))
    if is_sfx:
        return max(MIN_READABLE_PX, min(48, size))
    return max(MIN_READABLE_PX, min(36, size))


def estimate_font_size(
    text: str,
    box_w: int,
    box_h: int,
    *,
    is_sfx: bool = False,
    original_text: str = "",
    original_box_w: int | None = None,
    original_box_h: int | None = None,
) -> int:
    """Taille cible, plafonnée par la police originale estimée."""
    length = max(1, len((text or "").strip()))
    ow = original_box_w if original_box_w is not None else box_w
    oh = original_box_h if original_box_h is not None else box_h
    original_cap = estimate_original_font_size(
        original_text or text,
        ow,
        oh,
        is_sfx=is_sfx,
    )

    if is_sfx:
        size = max(12, min(original_cap, int(box_h * 0.42)))
        return max(MIN_READABLE_PX, min(original_cap, size))

    area = max(1, box_w * box_h)
    size = int(math.sqrt(area / (length * 0.55 * 1.20)))
    return max(MIN_READABLE_PX, min(original_cap, size))


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
    """Écarte les boîtes qui se superposent (priorité : ne jamais empiler 2 textes)."""
    if len(boxes) < 2:
        return list(boxes)

    out = [
        BoundingBox(x_min=b.x_min, y_min=b.y_min, x_max=b.x_max, y_max=b.y_max)
        for b in boxes
    ]

    for _ in range(max_iters):
        moved = False
        order = sorted(range(len(out)), key=lambda i: (out[i].y_min, out[i].x_min))
        for ai in range(len(order)):
            for bi in range(ai + 1, len(order)):
                i, j = order[ai], order[bi]
                a, b = out[i], out[j]
                if not boxes_overlap(a, b, gap=gap):
                    continue
                a_cx = (a.x_min + a.x_max) / 2
                b_cx = (b.x_min + b.x_max) / 2
                a_cy = (a.y_min + a.y_max) / 2
                b_cy = (b.y_min + b.y_max) / 2
                prefer_x = abs(b_cx - a_cx) >= abs(b_cy - a_cy) * 0.55

                if prefer_x:
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
    inner_pad: int | None = None,
    max_font_size: int | None = None,
    content_text: str = "",
) -> str:
    """Ancre la zone + fond blanc CSS ; marges selon contenu ; plafond police originale."""
    pad = (
        inner_pad
        if inner_pad is not None
        else content_inner_pad(content_text, box_w, box_h)
    )
    pad = max(MIN_PAD_PX, min(MAX_PAD_PX, pad))
    max_font = max_font_size if max_font_size is not None else font_size
    max_font = max(MIN_READABLE_PX, int(max_font))
    font_size = min(font_size, max_font)

    bg = "" if is_sfx else '<div class="toa-bubble-bg" aria-hidden="true"></div>'
    pad_css = f"{pad}px {pad + 1}px"
    fit_open = (
        f'<div class="toa-bubble-fit" style="font-size:{font_size}px;">'
    )
    fit_close = "</div>"
    # Padding dynamique sur le wrap (écrase le défaut CSS selon le contenu).
    pad_style = f"--toa-pad:{pad_css};"

    if is_sfx:
        x0 = box_x_min + pad
        y0 = box_y_min + pad
        max_w = max(12, min(box_w - 2 * pad, max(40, page_width - 16)))
        max_h = max(12, box_h - 2 * pad)
        style = (
            f"left:{x0}px;top:{y0}px;width:{max_w}px;height:{max_h}px;{pad_style}"
        )
        return (
            f'<div class="toa-bubble-wrap toa-bubble-wrap--sfx" '
            f'data-w="{max_w}" data-h="{max_h}" data-max-font="{max_font}" '
            f'style="{style}">'
            f"{fit_open}{inner_html}{fit_close}</div>"
        )

    if polygon and len(polygon) >= 3:
        text_poly = shrink_polygon(polygon)
        bb = polygon_bbox(text_poly)
        half = max(1, pad // 2)
        x0 = max(0, bb.x_min + half)
        y0 = max(0, bb.y_min + half)
        x1 = max(x0 + 12, bb.x_max - half)
        y1 = max(y0 + 12, bb.y_max - half)
        w = x1 - x0
        h = y1 - y0
        clip = polygon_to_clip_path(text_poly, origin_x=x0, origin_y=y0)
        style = (
            f"left:{x0}px;top:{y0}px;width:{w}px;height:{h}px;"
            f"clip-path:{clip};-webkit-clip-path:{clip};{pad_style}"
        )
        return (
            f'<div class="toa-bubble-wrap toa-bubble-wrap--poly" '
            f'data-w="{w}" data-h="{h}" data-max-font="{max_font}" '
            f'style="{style}">'
            f"{bg}{fit_open}{inner_html}{fit_close}</div>"
        )

    x0 = box_x_min + pad
    y0 = box_y_min + pad
    max_w = max(12, min(box_w - 2 * pad, max(40, page_width - 16)))
    max_h = max(12, box_h - 2 * pad)
    style = f"left:{x0}px;top:{y0}px;width:{max_w}px;height:{max_h}px;{pad_style}"
    return (
        f'<div class="toa-bubble-wrap toa-bubble-wrap--ellipse" '
        f'data-w="{max_w}" data-h="{max_h}" data-max-font="{max_font}" '
        f'style="{style}">'
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
