"""Bulles auto-dimensionnées ancrées sur la bulle manga originale (OpenCV).

1) Localise l'intérieur blanc de la bulle dessinée (`locate_bubble_interior`,
   flood-fill OpenCV depuis le centre de la bbox Cursor).
2) Ancre la bulle HTML au centre de cette enveloppe.
3) La bulle générée ne dépasse JAMAIS : police réduite autant que nécessaire.
4) Le texte traduit est toujours en #4A3F35.
"""

from __future__ import annotations

import logging
import math

import cv2
import numpy as np

from models import BoundingBox

logger = logging.getLogger(__name__)

# Couleur unique du texte traduit (demandée).
TRANSLATED_TEXT_COLOR = "#4A3F35"
TRANSLATED_TEXT_RGB = (0x4A, 0x3F, 0x35)

# Ne jamais dépasser l'enveloppe localisée (marge intérieure pour le contour).
MAX_WIDTH_RATIO = 0.92
MAX_HEIGHT_RATIO = 0.92
INNER_PAD_PX = 4

BUBBLE_FIT_CSS = f"""
.toa-bubble-wrap {{
  position: absolute;
  transform: translate(-50%, -50%);
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  /* Cadre fixe = enveloppe de la bulle originale : rien ne déborde. */
  width: var(--toa-max-w, 240px);
  height: var(--toa-max-h, 160px);
  overflow: hidden;
}}
/* Ovale / ronde : clippe le contenu à la forme de la bulle manga. */
.toa-bubble-wrap--oval {{
  border-radius: 50%;
}}
.toa-bubble-wrap .toa-bubble {{
  /* Largeur/hauteur = enveloppe : wrapping + réduction de police. */
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  box-sizing: border-box;
  overflow: hidden;
  white-space: normal;
  word-break: break-word;
  overflow-wrap: anywhere;
  color: {TRANSLATED_TEXT_COLOR} !important;
}}
.toa-bubble-wrap .toa-bubble,
.toa-bubble-wrap .toa-bubble * {{
  color: {TRANSLATED_TEXT_COLOR} !important;
}}
.toa-bubble-wrap .toa-bubble--vertical {{
  width: auto;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
}}
.toa-bubble-wrap .toa-bubble--round {{
  border-radius: 48%;
  padding: 0.55em 0.9em;
}}
.toa-bubble-wrap .toa-bubble--sfx {{
  width: max-content;
  max-width: 100%;
  color: {TRANSLATED_TEXT_COLOR} !important;
}}
""".strip()

# Agrandit la police tant que le texte tient en hauteur ; sinon réduit
# (jamais de débordement hors de la bulle originale localisée).
BUBBLE_FIT_SCRIPT = """
document.querySelectorAll('.toa-bubble-wrap').forEach((wrap) => {
  const el = wrap.querySelector('.toa-bubble') || wrap.firstElementChild;
  if (!el) return;
  const isSfx = !!wrap.querySelector('.toa-bubble--sfx');
  const maxW = wrap.clientWidth || parseFloat(wrap.dataset.w) || 60;
  const maxH = wrap.clientHeight || parseFloat(wrap.dataset.h) || 40;
  const capSize = isSfx ? 46 : 30;
  let size = parseFloat(getComputedStyle(el).fontSize) || 16;
  const setSize = (s) => { size = s; el.style.fontSize = s.toFixed(1) + 'px'; };
  const overflows = () => {
    // scroll* détecte le vrai débordement même avec overflow:hidden.
    return el.scrollWidth > maxW + 1 || el.scrollHeight > maxH + 1
      || el.scrollWidth > el.clientWidth + 1
      || el.scrollHeight > el.clientHeight + 1;
  };

  // 1) Grandir tant que ça tient dans l'enveloppe.
  let guard = 60;
  while (guard-- > 0 && size < capSize) {
    setSize(size + 1);
    if (overflows()) { setSize(size - 1); break; }
  }

  // 2) Réduire tant que ça déborde — priorité absolue.
  guard = 140;
  while (guard-- > 0 && size > 6 && overflows()) setSize(size - 0.5);

  // 3) Recalage page (sécurité).
  const page = document.querySelector('.page');
  if (page) {
    const p = page.getBoundingClientRect();
    const r = wrap.getBoundingClientRect();
    let dx = 0, dy = 0;
    if (r.left < p.left + 2) dx = (p.left + 2) - r.left;
    else if (r.right > p.right - 2) dx = (p.right - 2) - r.right;
    if (r.top < p.top + 2) dy = (p.top + 2) - r.top;
    else if (r.bottom > p.bottom - 2) dy = (p.bottom - 2) - r.bottom;
    if (dx) wrap.style.marginLeft = dx.toFixed(1) + 'px';
    if (dy) wrap.style.marginTop = dy.toFixed(1) + 'px';
  }
});
window.__toaFitDone = true;
""".strip()


def locate_bubble_interior(
    bgr: np.ndarray,
    bb: BoundingBox,
    *,
    white_thresh: int = 242,
) -> BoundingBox:
    """Localise l'intérieur blanc de la bulle manga (flood-fill OpenCV).

    Part du centre de la bbox détectée et remplit les pixels clairs connectés.
    Si le flood-fill échoue, repli sur la bbox d'origine (légèrement resserrée).
    """
    h, w = bgr.shape[:2]
    x0 = max(0, min(w - 1, bb.x_min))
    y0 = max(0, min(h - 1, bb.y_min))
    x1 = max(x0 + 1, min(w, bb.x_max))
    y1 = max(y0 + 1, min(h, bb.y_max))
    seed_x = (x0 + x1) // 2
    seed_y = (y0 + y1) // 2

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if int(gray[seed_y, seed_x]) < white_thresh - 25:
        # Centre trop sombre (encre) : chercher un pixel blanc voisin dans la bbox.
        roi = gray[y0:y1, x0:x1]
        bright = np.argwhere(roi >= white_thresh)
        if bright.size == 0:
            return _inset_bbox(BoundingBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1), w, h)
        # Pixel blanc le plus proche du centre de la ROI.
        cy, cx = (y1 - y0) // 2, (x1 - x0) // 2
        dists = (bright[:, 0] - cy) ** 2 + (bright[:, 1] - cx) ** 2
        best = bright[int(np.argmin(dists))]
        seed_y, seed_x = int(y0 + best[0]), int(x0 + best[1])

    # Fenêtre de recherche autour de la bbox (évite de remplir toute la page).
    bw, bh = x1 - x0, y1 - y0
    # Fenêtre assez large pour retrouver une bulle plus grande que la bbox Cursor.
    pad = max(48, bw * 2, bh * 2, 120)
    rx0, ry0 = max(0, x0 - pad), max(0, y0 - pad)
    rx1, ry1 = min(w, x1 + pad), min(h, y1 + pad)
    local_seed = (seed_x - rx0, seed_y - ry0)
    if not (0 <= local_seed[0] < (rx1 - rx0) and 0 <= local_seed[1] < (ry1 - ry0)):
        return _inset_bbox(BoundingBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1), w, h)

    # 1) Seuil blanc, 2) flood-fill binaire depuis le seed = enveloppe exacte.
    roi_gray = gray[ry0:ry1, rx0:rx1]
    _, white = cv2.threshold(roi_gray, white_thresh, 255, cv2.THRESH_BINARY)
    white = cv2.morphologyEx(
        white,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
        iterations=2,
    )
    if white[local_seed[1], local_seed[0]] == 0:
        return _inset_bbox(BoundingBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1), w, h)

    mask = np.zeros((white.shape[0] + 2, white.shape[1] + 2), dtype=np.uint8)
    fill_img = white.copy()
    cv2.floodFill(
        fill_img,
        mask,
        local_seed,
        128,
        loDiff=0,
        upDiff=0,
        flags=4 | (255 << 8),
    )
    filled = (mask[1:-1, 1:-1] > 0).astype(np.uint8) * 255
    # Si le mask est vide, utiliser la composante connexe du seed.
    if int(np.count_nonzero(filled)) < 40:
        n, labels, stats, _ = cv2.connectedComponentsWithStats(white)
        label = int(labels[local_seed[1], local_seed[0]])
        if label <= 0:
            return _inset_bbox(BoundingBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1), w, h)
        sx, sy, sw, sh, area = stats[label]
        if area < 80:
            return _inset_bbox(BoundingBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1), w, h)
        located = BoundingBox(
            x_min=rx0 + sx,
            y_min=ry0 + sy,
            x_max=rx0 + sx + sw,
            y_max=ry0 + sy + sh,
        )
        return _inset_bbox(located, w, h)

    ys, xs = np.where(filled > 0)
    located = BoundingBox(
        x_min=int(rx0 + xs.min()),
        y_min=int(ry0 + ys.min()),
        x_max=int(rx0 + xs.max() + 1),
        y_max=int(ry0 + ys.max() + 1),
    )
    # Garde-fou page : un flood qui remplit le fond clair est rejeté.
    # On AUTORISE l'expansion depuis une bbox Cursor trop petite (cas fréquent)
    # tant que le résultat reste une bulle raisonnable (< 8 % de la page).
    loc_w = located.x_max - located.x_min
    loc_h = located.y_max - located.y_min
    loc_area = max(1, loc_w * loc_h)
    page_area = max(1, w * h)
    if loc_area > 0.15 * page_area or loc_w > w * 0.65 or loc_h > h * 0.65:
        logger.debug("Flood-fill trop large, repli sur bbox Cursor")
        return _inset_bbox(BoundingBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1), w, h)
    # Le centre localisé doit rester près du seed Cursor.
    loc_cx = (located.x_min + located.x_max) // 2
    loc_cy = (located.y_min + located.y_max) // 2
    seed_cx, seed_cy = (x0 + x1) // 2, (y0 + y1) // 2
    if abs(loc_cx - seed_cx) > max(bw, 40) * 1.8 or abs(loc_cy - seed_cy) > max(bh, 40) * 1.8:
        logger.debug("Flood-fill hors ancre Cursor, repli")
        return _inset_bbox(BoundingBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1), w, h)
    return _inset_bbox(located, w, h)


def _inset_bbox(bb: BoundingBox, page_w: int, page_h: int) -> BoundingBox:
    """Resserre légèrement pour rester à l'intérieur du contour noir."""
    pad = INNER_PAD_PX
    x0 = max(0, bb.x_min + pad)
    y0 = max(0, bb.y_min + pad)
    x1 = min(page_w, bb.x_max - pad)
    y1 = min(page_h, bb.y_max - pad)
    if x1 - x0 < 8:
        cx = (bb.x_min + bb.x_max) // 2
        x0, x1 = max(0, cx - 4), min(page_w, cx + 4)
    if y1 - y0 < 8:
        cy = (bb.y_min + bb.y_max) // 2
        y0, y1 = max(0, cy - 4), min(page_h, cy + 4)
    return BoundingBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1)


def estimate_font_size(
    text: str,
    box_w: int,
    box_h: int,
    *,
    is_sfx: bool = False,
) -> int:
    """Police de départ estimée pour que le texte tienne dans l'enveloppe."""
    length = max(1, len((text or "").strip()))
    if is_sfx:
        return max(12, min(40, int(box_h * 0.40)))
    area = max(1, box_w * box_h)
    size = int(math.sqrt(area / (length * 0.62 * 1.30)))
    return max(9, min(26, size))


def build_bubble_wrap(
    inner_html: str,
    *,
    box_x_min: int,
    box_y_min: int,
    box_w: int,
    box_h: int,
    page_width: int,
    font_size: int,
    is_oval: bool = False,
) -> str:
    """Div ancrée au centre de la bulle localisée ; taille max = enveloppe."""
    center_x = box_x_min + box_w // 2
    center_y = box_y_min + box_h // 2
    # L'enveloppe est déjà insetée par locate_bubble_interior.
    max_w = max(12, min(box_w, max(40, page_width - 16)))
    max_h = max(12, box_h)
    # Rectangle inscrit dans l'ellipse ≈ 0.70 de l'AABB pour rester dans le trait.
    if is_oval:
        max_w = max(12, int(max_w * 0.72))
        max_h = max(12, int(max_h * 0.72))
    style_vars = f"--toa-max-w:{max_w}px;--toa-max-h:{max_h}px;"
    wrap_cls = "toa-bubble-wrap toa-bubble-wrap--oval" if is_oval else "toa-bubble-wrap"
    return (
        f'<div class="{wrap_cls}" data-w="{max_w}" data-h="{max_h}" '
        f'style="left:{center_x}px;top:{center_y}px;{style_vars}">'
        f'<div style="font-size:{font_size}px;">{inner_html}</div></div>'
    )
