"""Superposition des bulles traduites sur la zone exacte du texte original."""

import logging
import os
import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from models import BoundingBox, TextBlock
from services.bubble_fit import TRANSLATED_TEXT_RGB

logger = logging.getLogger(__name__)

PROJECT_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


def _normalize_font_path(raw_path: str) -> str:
    """Accepte un path relatif (depuis backend/) ou absolu."""
    p = (raw_path or "").strip()
    if not p:
        return ""
    path_obj = Path(p)
    if path_obj.is_absolute():
        return str(path_obj)
    return str((Path(__file__).resolve().parent.parent / path_obj).resolve())


DEFAULT_DIALOGUE_FONTS = [
    _normalize_font_path(os.getenv("MANGA_DIALOGUE_FONT_PATH", "")),
    _normalize_font_path(os.getenv("MANGA_FONT_PATH", "")),
    str(PROJECT_FONT_DIR / "WildWordsRoman.ttf"),
    str(PROJECT_FONT_DIR / "AnimeAce.ttf"),
    "C:/Windows/Fonts/YUGOTHB.TTC",
    "C:/Windows/Fonts/YuGothB.ttc",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
DEFAULT_SFX_FONTS = [
    _normalize_font_path(os.getenv("MANGA_SFX_FONT_PATH", "")),
    _normalize_font_path(os.getenv("MANGA_FONT_PATH", "")),
    str(PROJECT_FONT_DIR / "CCWildWords.ttf"),
    str(PROJECT_FONT_DIR / "AnimeAce.ttf"),
    str(PROJECT_FONT_DIR / "WildWordsRoman.ttf"),
    "C:/Windows/Fonts/msgothic.ttc",
    "C:/Windows/Fonts/YUGOTHB.TTC",
    "C:/Windows/Fonts/YuGothB.ttc",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

TEXT_PAD = 3
# Bbox > 8 % de la page : pas d'effacement (evite de detruire un panneau entier).
MAX_BBOX_PAGE_RATIO = float(os.getenv("MAX_BBOX_PAGE_RATIO", "0.08"))
# Seuil luminosite pour considerer un pixel comme encre de texte (0-255, plus bas = plus strict).
INK_DARK_THRESHOLD = int(os.getenv("INK_DARK_THRESHOLD", "100"))
# Fond blanc derriere le texte (255 = opaque, masque le japonais sous la bulle).
TEXT_BG_ALPHA = max(0, min(255, int(os.getenv("TEXT_BG_ALPHA", "255"))))
TEXT_BG_FILL = (255, 255, 255, TEXT_BG_ALPHA)
MIN_FONT_SIZE = 7
MAX_FONT_SIZE = 26
FONT_SIZE_FACTOR = 2 / 3
BUBBLE_INSET_X = 0.07
BUBBLE_INSET_Y = 0.09


def _is_vertical_text_mode() -> bool:
    return os.getenv("IMMERSION_VERTICAL_TEXT", "false").lower() in (
        "true",
        "1",
        "yes",
    )


def _load_font(
    size: int,
    candidates: list[str],
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(MIN_FONT_SIZE, size)
    for path in candidates:
        if not path:
            continue
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _looks_like_sfx_text(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if len(t) <= 18 and re.fullmatch(
        r"[\u30a0-\u30ff\u3040-\u309fー…・！？\sっ゛゜]+", t
    ):
        return True
    if re.search(
        r"(ゴロ|ドン|ガタ|にゃ|ニャ|わん|ワン|シーン|バキ|ズキ|ドキ|ぎゃ|ギャ|ずん|ズン)",
        t,
    ):
        return True
    return False


_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uff66-\uff9f]")


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _is_tall_bbox(bb: BoundingBox) -> bool:
    bw = max(1, bb.x_max - bb.x_min)
    bh = max(1, bb.y_max - bb.y_min)
    return bh > bw * 1.35


def _is_round_bubble(base_bgr: np.ndarray, bb: BoundingBox) -> bool:
    """Heuristique : bulle ronde si ratio proche du carré et fond clair dominant."""
    h, w = base_bgr.shape[:2]
    bb = _clip_bbox(bb, w, h)
    bw = bb.x_max - bb.x_min
    bh = bb.y_max - bb.y_min
    if bw < 20 or bh < 20:
        return False
    ratio = bw / max(1, bh)
    if ratio < 0.88 or ratio > 1.12:
        return False
    roi = base_bgr[bb.y_min : bb.y_max, bb.x_min : bb.x_max]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    bright_ratio = float(np.count_nonzero(gray > 175)) / max(1, gray.size)
    return bright_ratio > 0.58


def _cap_expanded_bbox(
    original: BoundingBox,
    expanded: BoundingBox,
    width: int,
    height: int,
    *,
    max_page_ratio: float = 0.10,
    max_growth: float = 2.2,
) -> BoundingBox:
    """Limite l'expansion pour ne pas engloutir tout le panneau."""
    page_area = width * height
    if _box_area(expanded) > max_page_ratio * page_area:
        expanded = original
    ow = max(1, original.x_max - original.x_min)
    oh = max(1, original.y_max - original.y_min)
    ew = expanded.x_max - expanded.x_min
    eh = expanded.y_max - expanded.y_min
    if ew > ow * max_growth or eh > oh * max_growth:
        pad = max(6, min(18, ow // 5, oh // 5))
        return BoundingBox(
            x_min=max(0, original.x_min - pad),
            y_min=max(0, original.y_min - pad),
            x_max=min(width, original.x_max + pad),
            y_max=min(height, original.y_max + pad),
        )
    return expanded


def _bbox_center(bb: BoundingBox) -> tuple[int, int]:
    return (bb.x_min + bb.x_max) // 2, (bb.y_min + bb.y_max) // 2


def _center_bbox_on_anchor(
    anchor: BoundingBox,
    sized: BoundingBox,
    width: int,
    height: int,
    *,
    max_growth: float = 1.35,
) -> BoundingBox:
    """Garde le centre Cursor ; ajuste la taille sans decaler la bulle."""
    acx, acy = _bbox_center(anchor)
    aw = max(8, anchor.x_max - anchor.x_min)
    ah = max(8, anchor.y_max - anchor.y_min)
    sw = sized.x_max - sized.x_min
    sh = sized.y_max - sized.y_min
    fw = max(aw, min(sw, int(aw * max_growth)))
    fh = max(ah, min(sh, int(ah * max_growth)))
    return _clip_bbox(
        BoundingBox(
            x_min=acx - fw // 2,
            y_min=acy - fh // 2,
            x_max=acx - fw // 2 + fw,
            y_max=acy - fh // 2 + fh,
        ),
        width,
        height,
    )


def _union_bbox(a: BoundingBox, b: BoundingBox, width: int, height: int) -> BoundingBox:
    return _clip_bbox(
        BoundingBox(
            x_min=min(a.x_min, b.x_min),
            y_min=min(a.y_min, b.y_min),
            x_max=max(a.x_max, b.x_max),
            y_max=max(a.y_max, b.y_max),
        ),
        width,
        height,
    )


def _align_render_bbox(
    cursor_bb: BoundingBox,
    detected_bb: BoundingBox,
    width: int,
    height: int,
) -> BoundingBox:
    """Fusionne detection locale + coords Cursor, centre ancre sur Cursor."""
    capped = _cap_expanded_bbox(cursor_bb, detected_bb, width, height)
    centered = _center_bbox_on_anchor(cursor_bb, capped, width, height)
    return _union_bbox(cursor_bb, centered, width, height)


def detect_bubble_region(
    base_bgr: np.ndarray, bb: BoundingBox
) -> BoundingBox | None:
    """Trouve la bulle blanche englobante exacte ; None si introuvable."""
    h, w = base_bgr.shape[:2]
    gray = cv2.cvtColor(base_bgr, cv2.COLOR_BGR2GRAY)
    x0, y0, x1, y1 = bb.x_min, bb.y_min, bb.x_max, bb.y_max
    bw, bh = x1 - x0, y1 - y0
    if bw < 6 or bh < 6:
        return None

    pad = max(24, bw, bh)
    rx0 = max(0, x0 - pad)
    ry0 = max(0, y0 - pad)
    rx1 = min(w, x1 + pad)
    ry1 = min(h, y1 + pad)
    roi = gray[ry0:ry1, rx0:rx1]
    if roi.size == 0:
        return None

    _, white = cv2.threshold(roi, 172, 255, cv2.THRESH_BINARY)
    cx = max(0, min(roi.shape[1] - 1, (x0 + x1) // 2 - rx0))
    cy = max(0, min(roi.shape[0] - 1, (y0 + y1) // 2 - ry0))

    n, _, stats, _ = cv2.connectedComponentsWithStats(white)
    orig_area = max(1, bw * bh)
    max_bubble_area = min(0.10 * w * h, orig_area * 4)
    for i in range(1, n):
        sx, sy, sw, sh, area = stats[i]
        if area < 120 or area > max_bubble_area:
            continue
        if sx <= cx <= sx + sw and sy <= cy <= sy + sh:
            return BoundingBox(
                x_min=max(0, rx0 + sx - 2),
                y_min=max(0, ry0 + sy - 2),
                x_max=min(w, rx0 + sx + sw + 2),
                y_max=min(h, ry0 + sy + sh + 2),
            )
    return None


def _ink_density(base_bgr: np.ndarray, bb: BoundingBox) -> float:
    h, w = base_bgr.shape[:2]
    bb = _clip_bbox(bb, w, h)
    roi = base_bgr[bb.y_min : bb.y_max, bb.x_min : bb.x_max]
    if roi.size == 0:
        return 0.0
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    return float(np.count_nonzero(gray < INK_DARK_THRESHOLD)) / max(1, gray.size)


def _is_suspect_edge_bbox(bb: BoundingBox, width: int, height: int) -> bool:
    """Bande étroite collée au bord = coordonnées hors image clampées au bord."""
    bw = max(1, bb.x_max - bb.x_min)
    bh = max(1, bb.y_max - bb.y_min)
    touches = (
        bb.x_min <= 2
        or bb.y_min <= 2
        or bb.x_max >= width - 2
        or bb.y_max >= height - 2
    )
    narrow = bw < bh * 0.45 or bh < bw * 0.45
    return touches and narrow


def _rescue_edge_bbox(
    base_bgr: np.ndarray, bb: BoundingBox
) -> BoundingBox | None:
    """Cherche la bulle blanche réelle vers l'intérieur de la page."""
    h, w = base_bgr.shape[:2]
    bw = max(8, bb.x_max - bb.x_min)
    bh = max(8, bb.y_max - bb.y_min)
    grow_x = min(w // 3, bh)
    grow_y = min(h // 3, bw)
    search = _clip_bbox(
        BoundingBox(
            x_min=bb.x_min - grow_x,
            y_min=bb.y_min - grow_y,
            x_max=bb.x_max + grow_x,
            y_max=bb.y_max + grow_y,
        ),
        w,
        h,
    )
    return detect_bubble_region(base_bgr, search)


def detect_bubble_polygon(
    base_bgr: np.ndarray, bb: BoundingBox
) -> list[tuple[int, int]] | None:
    """Contour polygonal exact de la bulle blanche contenant le centre de la bbox."""
    h, w = base_bgr.shape[:2]
    bb = _clip_bbox(bb, w, h)
    x0, y0, x1, y1 = bb.x_min, bb.y_min, bb.x_max, bb.y_max
    bw, bh = x1 - x0, y1 - y0
    if bw < 6 or bh < 6:
        return None

    pad = max(24, bw // 2, bh // 2)
    rx0 = max(0, x0 - pad)
    ry0 = max(0, y0 - pad)
    rx1 = min(w, x1 + pad)
    ry1 = min(h, y1 + pad)
    gray = cv2.cvtColor(base_bgr[ry0:ry1, rx0:rx1], cv2.COLOR_BGR2GRAY)
    if gray.size == 0:
        return None

    _, white = cv2.threshold(gray, 172, 255, cv2.THRESH_BINARY)
    white = cv2.morphologyEx(
        white,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
        iterations=2,
    )

    cx = max(0, min(white.shape[1] - 1, (x0 + x1) // 2 - rx0))
    cy = max(0, min(white.shape[0] - 1, (y0 + y1) // 2 - ry0))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(white)
    orig_area = max(1, bw * bh)
    max_bubble_area = min(0.10 * w * h, orig_area * 4)

    label = int(labels[cy, cx]) if white[cy, cx] > 0 else 0
    if label > 0:
        area = stats[label, cv2.CC_STAT_AREA]
        if area < 120 or area > max_bubble_area:
            label = 0
    if label == 0:
        # Le centre tombe sur l'encre du texte : chercher la composante englobante.
        for i in range(1, n):
            sx, sy, sw, sh, area = stats[i]
            if area < 120 or area > max_bubble_area:
                continue
            if sx <= cx <= sx + sw and sy <= cy <= sy + sh:
                label = i
                break
    if label == 0:
        return None

    mask = (labels == label).astype(np.uint8) * 255
    contours, _ = cv2.findContours(
        mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 120:
        return None

    # Lissage léger : garde la forme exacte (ovale, pointe...) sans bruit pixel.
    eps = max(1.2, 0.0035 * cv2.arcLength(contour, True))
    approx = cv2.approxPolyDP(contour, eps, True)
    points = [(int(p[0][0]) + rx0, int(p[0][1]) + ry0) for p in approx]
    if len(points) < 3:
        return None
    return points


def _separate_overlapping_bboxes(
    bbs: list[BoundingBox],
    *,
    min_size: int = 14,
    gap: int = 2,
    max_iters: int = 6,
) -> list[BoundingBox]:
    """Garantit zero chevauchement : retrecit les bbox le long de l'axe le moins couteux."""
    bbs = list(bbs)
    for _ in range(max_iters):
        changed = False
        for i in range(len(bbs)):
            for j in range(i + 1, len(bbs)):
                a, b = bbs[i], bbs[j]
                ox = min(a.x_max, b.x_max) - max(a.x_min, b.x_min)
                oy = min(a.y_max, b.y_max) - max(a.y_min, b.y_min)
                if ox <= 0 or oy <= 0:
                    continue
                changed = True
                if ox <= oy:
                    need = ox + gap
                    if (a.x_min + a.x_max) <= (b.x_min + b.x_max):
                        li, ri = i, j
                    else:
                        li, ri = j, i
                    left, right = bbs[li], bbs[ri]
                    cut_l = min(
                        (need + 1) // 2,
                        max(0, (left.x_max - left.x_min) - min_size),
                    )
                    left = left.model_copy(update={"x_max": left.x_max - cut_l})
                    cut_r = min(
                        need - cut_l,
                        max(0, (right.x_max - right.x_min) - min_size),
                    )
                    right = right.model_copy(update={"x_min": right.x_min + cut_r})
                    bbs[li], bbs[ri] = left, right
                else:
                    need = oy + gap
                    if (a.y_min + a.y_max) <= (b.y_min + b.y_max):
                        ti, bi = i, j
                    else:
                        ti, bi = j, i
                    top, bottom = bbs[ti], bbs[bi]
                    cut_t = min(
                        (need + 1) // 2,
                        max(0, (top.y_max - top.y_min) - min_size),
                    )
                    top = top.model_copy(update={"y_max": top.y_max - cut_t})
                    cut_b = min(
                        need - cut_t,
                        max(0, (bottom.y_max - bottom.y_min) - min_size),
                    )
                    bottom = bottom.model_copy(
                        update={"y_min": bottom.y_min + cut_b}
                    )
                    bbs[ti], bbs[bi] = top, bottom
        if not changed:
            break
    return bbs


def _strict_ink_pixels(gray: np.ndarray) -> np.ndarray:
    """Masque binaire : uniquement l'encre tres sombre (pas les gris/trames)."""
    return (gray < INK_DARK_THRESHOLD).astype(np.uint8) * 255


def _shrink_oversized_bbox(
    bb: BoundingBox, width: int, height: int
) -> BoundingBox:
    """Réduit une bbox aberrante en gardant son centre."""
    page_area = width * height
    max_area = MAX_BBOX_PAGE_RATIO * page_area
    if _box_area(bb) <= max_area:
        return bb
    cx = (bb.x_min + bb.x_max) // 2
    cy = (bb.y_min + bb.y_max) // 2
    side = max(24, int(max_area**0.5))
    half = side // 2
    return BoundingBox(
        x_min=max(0, cx - half),
        y_min=max(0, cy - half),
        x_max=min(width, cx + half),
        y_max=min(height, cy + half),
    )


def _add_ink_mask(
    base_bgr: np.ndarray,
    bb: BoundingBox,
    mask: np.ndarray,
    *,
    dilate_iters: int = 1,
) -> None:
    h, w = base_bgr.shape[:2]
    bb = _clip_bbox(bb, w, h)
    x0, y0, x1, y1 = bb.x_min, bb.y_min, bb.x_max, bb.y_max
    if x1 <= x0 or y1 <= y0:
        return
    roi = base_bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    dark = _strict_ink_pixels(gray)
    if dilate_iters > 0:
        dark = cv2.dilate(
            dark,
            cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
            iterations=dilate_iters,
        )
    mask[y0:y1, x0:x1] = cv2.bitwise_or(mask[y0:y1, x0:x1], dark)


def _erase_dialogue_ink_white(bgr: np.ndarray, bb: BoundingBox) -> None:
    """Dans une bulle blanche : remplace uniquement l'encre par du blanc (sans inpaint)."""
    h, w = bgr.shape[:2]
    bb = _clip_bbox(bb, w, h)
    x0, y0, x1, y1 = bb.x_min, bb.y_min, bb.x_max, bb.y_max
    if x1 <= x0 or y1 <= y0:
        return
    roi = bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    ink = _strict_ink_pixels(gray)
    ink = cv2.dilate(
        ink,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
        iterations=1,
    )
    roi[ink > 0] = (255, 255, 255)
    bgr[y0:y1, x0:x1] = roi


def _prepend_render_tags(text: str, tags: str) -> str:
    clean = text or ""
    for tag in ("[[DIR:V]]", "[[DIR:H]]", "[[BG:SOLID]]", "[[BG:TRANSPARENT]]"):
        clean = clean.replace(tag, "")
    return f"{tags}{clean.strip()}"


def refine_blocks_for_render(
    image_path: Path,
    blocks: list[TextBlock],
) -> list[TextBlock]:
    """Affine les bbox (bulle blanche / encre) et ajoute les tags de rendu."""
    bgr = cv2.imread(str(image_path))
    if bgr is None:
        return blocks
    h, w = bgr.shape[:2]
    page_area = w * h
    refined: list[TextBlock] = []

    for block in blocks:
        cursor_bb = _clip_bbox(block.boundingBox, w, h)
        area = _box_area(cursor_bb)
        if area > MAX_BBOX_PAGE_RATIO * page_area:
            logger.warning(
                "Bbox tres large (%.0f%% page) bulle #%s — resserrement force",
                100 * area / max(1, page_area),
                block.id,
            )
            tight = _refine_bbox_to_text(bgr, cursor_bb)
            cursor_bb = _shrink_oversized_bbox(
                tight if _box_area(tight) < area else cursor_bb, w, h
            )

        is_sfx = _looks_like_sfx_text(block.originalText)

        # Bbox clampée au bord de page (coords détection hors image) :
        # tenter de retrouver la vraie bulle vers l'intérieur, sinon abandonner
        # la zone si elle tombe sur une marge noire ou une zone vide.
        if _is_suspect_edge_bbox(cursor_bb, w, h):
            rescued = _rescue_edge_bbox(bgr, cursor_bb)
            if rescued is not None:
                logger.info("Bbox au bord rescapée bulle #%s", block.id)
                cursor_bb = rescued
            else:
                density = _ink_density(bgr, cursor_bb)
                if density > 0.55 or density < 0.015:
                    logger.warning(
                        "Zone #%s abandonnée (bande au bord, densité encre %.2f)",
                        block.id,
                        density,
                    )
                    continue

        if is_sfx:
            ink_bb = _refine_bbox_to_text(bgr, cursor_bb)
            new_bb = _align_render_bbox(cursor_bb, ink_bb, w, h)
            clean_tr, _, _ = _extract_render_hints(block.translatedText)
            # Jamais de vertical pour une traduction en alphabet latin.
            dir_tag = "[[DIR:V]]" if _has_cjk(clean_tr) else "[[DIR:H]]"
            tags = f"[[BG:TRANSPARENT]]{dir_tag}"
        else:
            # Forme exacte de la bulle originale : composant blanc détecté,
            # sinon coords Cursor telles quelles (jamais d'agrandissement).
            bubble_bb = detect_bubble_region(bgr, cursor_bb)
            new_bb = bubble_bb if bubble_bb is not None else cursor_bb
            tags = "[[BG:SOLID]][[DIR:H]]"

        tr = _prepend_render_tags(block.translatedText, tags)
        refined.append(
            block.model_copy(
                update={"boundingBox": new_bb, "translatedText": tr},
            )
        )

    # Interdiction stricte de superposition entre bulles.
    separated = _separate_overlapping_bboxes([b.boundingBox for b in refined])
    refined = [
        blk.model_copy(update={"boundingBox": bb})
        for blk, bb in zip(refined, separated)
    ]
    return refined


def erase_text_regions(
    image_path: Path,
    blocks: list[TextBlock],
    output_path: Path,
) -> None:
    """Efface uniquement l'encre du texte source (inpaint cible, sans peindre la page)."""
    import shutil

    bgr = cv2.imread(str(image_path))
    if bgr is None:
        shutil.copy2(image_path, output_path)
        return

    h, w = bgr.shape[:2]
    page_area = w * h
    max_erase_area = MAX_BBOX_PAGE_RATIO * page_area
    inpaint_mask = np.zeros((h, w), dtype=np.uint8)

    for block in blocks:
        bb = _clip_bbox(block.boundingBox, w, h)
        if _box_area(bb) > max_erase_area:
            logger.warning(
                "Effacement ignore (bbox %.0f%% page) bulle #%s",
                100 * _box_area(bb) / max(1, page_area),
                block.id,
            )
            continue

        is_sfx = _looks_like_sfx_text(block.originalText)
        work_bb = _clip_bbox(block.boundingBox, w, h)
        if _box_area(work_bb) > max_erase_area:
            continue
        if is_sfx:
            _add_ink_mask(bgr, work_bb, inpaint_mask, dilate_iters=1)
        else:
            _erase_dialogue_ink_white(bgr, work_bb)

    if np.any(inpaint_mask):
        bgr = cv2.inpaint(bgr, inpaint_mask, 3, cv2.INPAINT_TELEA)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), bgr)


def _font_candidates_for_block(block: TextBlock) -> list[str]:
    if _looks_like_sfx_text(block.originalText):
        return DEFAULT_SFX_FONTS
    return DEFAULT_DIALOGUE_FONTS


def _clip_bbox(bb: BoundingBox, width: int, height: int) -> BoundingBox:
    return BoundingBox(
        x_min=max(0, min(bb.x_min, width - 1)),
        y_min=max(0, min(bb.y_min, height - 1)),
        x_max=max(1, min(bb.x_max, width)),
        y_max=max(1, min(bb.y_max, height)),
    )


def _box_area(bb: BoundingBox) -> int:
    return max(0, bb.x_max - bb.x_min) * max(0, bb.y_max - bb.y_min)


def _refine_bbox_to_text(base_bgr: np.ndarray, bb: BoundingBox) -> BoundingBox:
    """Resserre une bbox sur l'encre détectée pour éviter un rendu décalé."""
    h, w = base_bgr.shape[:2]
    x0, y0, x1, y1 = bb.x_min, bb.y_min, bb.x_max, bb.y_max
    bw = x1 - x0
    bh = y1 - y0
    if bw < 8 or bh < 8:
        return bb

    pad_x = max(4, bw // 6)
    pad_y = max(4, bh // 6)
    rx0 = max(0, x0 - pad_x)
    ry0 = max(0, y0 - pad_y)
    rx1 = min(w, x1 + pad_x)
    ry1 = min(h, y1 + pad_y)
    roi = base_bgr[ry0:ry1, rx0:rx1]
    if roi.size == 0:
        return bb

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, dark = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    _, bright = cv2.threshold(blur, 188, 255, cv2.THRESH_BINARY)
    text_mask = cv2.bitwise_or(dark, bright)
    text_mask = cv2.morphologyEx(
        text_mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
        iterations=1,
    )
    text_mask = cv2.morphologyEx(
        text_mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        iterations=1,
    )

    coords = cv2.findNonZero(text_mask)
    if coords is None:
        return bb

    tx, ty, tw, th = cv2.boundingRect(coords)
    if tw < 6 or th < 6:
        return bb

    refined = BoundingBox(
        x_min=max(0, rx0 + tx - 3),
        y_min=max(0, ry0 + ty - 3),
        x_max=min(w, rx0 + tx + tw + 3),
        y_max=min(h, ry0 + ty + th + 3),
    )

    # Garde-fou: ne pas remplacer par une bbox aberrante.
    old_area = max(1, _box_area(bb))
    new_area = _box_area(refined)
    if new_area < old_area * 0.08 or new_area > old_area * 3.2:
        return bb
    return refined


_LATIN_TARGETS = frozenset(
    {"fr", "en", "es", "de", "pt", "it", "pl", "nl", "tr", "vi", "id"}
)


def _sanitize_text(text: str, target_lang: str) -> str:
    t = text.strip()
    if "Mode test OCR" in t or "OCR_FAST_MODE" in t:
        return ""
    t = re.sub(r"^\(FR\)\s*", "", t)
    t = re.sub(r"^\(EN\)\s*", "", t)
    if target_lang in _LATIN_TARGETS:
        t = re.sub(r"[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]+", "", t).strip()
    if not t or re.search(r"[\uFFFD□]", t):
        return ""
    return t


def _extract_render_hints(text: str) -> tuple[str, str | None, bool | None]:
    """Lit les tags inseres par Cursor: [[DIR:V]] [[BG:TRANSPARENT]]."""
    clean = text or ""
    direction: str | None = None
    bubble_bg: bool | None = None
    if "[[DIR:V]]" in clean:
        direction = "vertical"
        clean = clean.replace("[[DIR:V]]", "")
    if "[[DIR:H]]" in clean:
        direction = "horizontal"
        clean = clean.replace("[[DIR:H]]", "")
    if "[[BG:TRANSPARENT]]" in clean:
        bubble_bg = False
        clean = clean.replace("[[BG:TRANSPARENT]]", "")
    if "[[BG:SOLID]]" in clean:
        bubble_bg = True
        clean = clean.replace("[[BG:SOLID]]", "")
    return clean.strip(), direction, bubble_bg


def _bubble_inner_metrics(box_w: int, box_h: int) -> tuple[int, int, int, int]:
    """Zone utile à l'intérieur de la bulle (centrage sur la bulle, pas l'encre)."""
    inset_x = max(TEXT_PAD, int(box_w * BUBBLE_INSET_X))
    inset_y = max(TEXT_PAD, int(box_h * BUBBLE_INSET_Y))
    usable_w = max(8, box_w - 2 * inset_x)
    usable_h = max(8, box_h - 2 * inset_y)
    return usable_w, usable_h, inset_x, inset_y


def _adaptive_font_cap(box_w: int, box_h: int) -> int:
    usable_w, usable_h, _, _ = _bubble_inner_metrics(box_w, box_h)
    short_side = min(usable_w, usable_h)
    area_scale = int((usable_w * usable_h) ** 0.5 * 0.085)
    side_scale = int(short_side * 0.18)
    cap = min(area_scale, side_scale, MAX_FONT_SIZE)
    return max(MIN_FONT_SIZE, int(cap * FONT_SIZE_FACTOR))


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    if max_width < 8:
        return [text[:1]] if text else []
    words = text.split()
    if not words:
        return [text] if text else []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        test = f"{current} {word}"
        if font.getbbox(test)[2] - font.getbbox(test)[0] <= max_width:
            current = test
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _layout_metrics(
    font: ImageFont.FreeTypeFont,
    lines: list[str],
    line_gap: int,
) -> tuple[int, int, list[int]]:
    line_heights = [
        font.getbbox(line)[3] - font.getbbox(line)[1] for line in lines
    ]
    if not line_heights:
        return 0, 0, []
    total_h = sum(line_heights) + line_gap * max(0, len(lines) - 1)
    max_w = max(font.getbbox(line)[2] - font.getbbox(line)[0] for line in lines)
    return total_h, max_w, line_heights


def _fit_text_to_box(
    text: str,
    box_w: int,
    box_h: int,
    font_candidates: list[str],
) -> tuple[
    ImageFont.FreeTypeFont | ImageFont.ImageFont,
    list[str],
    list[int],
    int,
] | None:
    """Cherche la plus grande police lisible qui tient dans la bulle."""
    usable_w, usable_h, _, _ = _bubble_inner_metrics(box_w, box_h)
    upper = min(_adaptive_font_cap(box_w, box_h), usable_h, max(MIN_FONT_SIZE, usable_h))

    for size in range(upper, MIN_FONT_SIZE - 1, -1):
        font = _load_font(size, font_candidates)
        line_gap = max(1, size // 10)
        lines = _wrap_text(text, font, usable_w)
        if not lines:
            continue
        total_h, max_w, line_heights = _layout_metrics(font, lines, line_gap)
        if total_h <= usable_h and max_w <= usable_w:
            return font, lines, line_heights, line_gap

    font = _load_font(MIN_FONT_SIZE, font_candidates)
    line_gap = 1
    lines = _wrap_text(text, font, usable_w)
    if not lines:
        return None
    _, _, line_heights = _layout_metrics(font, lines, line_gap)
    return font, lines, line_heights, line_gap


def _tight_text_rect(
    bb: BoundingBox,
    *,
    text_w: int,
    text_h: int,
    pad_x: int = 8,
    pad_y: int = 6,
) -> tuple[int, int, int, int]:
    """Bulle blanche resserrée autour du texte, centrée dans la zone d'origine."""
    box_w = bb.x_max - bb.x_min
    box_h = bb.y_max - bb.y_min
    tw = min(box_w, text_w + pad_x * 2)
    th = min(box_h, text_h + pad_y * 2)
    cx = (bb.x_min + bb.x_max) // 2
    cy = (bb.y_min + bb.y_max) // 2
    return (cx - tw // 2, cy - th // 2, cx - tw // 2 + tw, cy - th // 2 + th)


def _draw_bubble_overlay(
    draw: ImageDraw.ImageDraw,
    bb: BoundingBox,
    text: str,
    font_candidates: list[str],
    draw_background: bool = False,
    polygon: list[tuple[int, int]] | None = None,
) -> None:
    """Texte centré dans la bulle; fond = polygone exact de la bulle si dispo."""
    x0, y0, x1, y1 = bb.x_min, bb.y_min, bb.x_max, bb.y_max
    box_w = x1 - x0
    box_h = y1 - y0
    if box_w < 12 or box_h < 10:
        return

    use_polygon = (
        draw_background and polygon is not None and len(polygon) >= 3
    )
    if use_polygon:
        # Remplissage de la forme réelle de la bulle ; le contour noir
        # d'origine reste visible autour du polygone.
        draw.polygon(polygon, fill=TEXT_BG_FILL)
        # Marge supplémentaire : les bords incurvés rognent la zone utile.
        sx = max(2, int(box_w * 0.08))
        sy = max(2, int(box_h * 0.08))
        x0, y0, x1, y1 = x0 + sx, y0 + sy, x1 - sx, y1 - sy
        box_w, box_h = x1 - x0, y1 - y0
        if box_w < 8 or box_h < 8:
            return

    fitted = _fit_text_to_box(text, box_w, box_h, font_candidates)
    if not fitted:
        return
    font, lines, line_heights, line_gap = fitted
    total_h, max_w, _ = _layout_metrics(font, lines, line_gap)

    if draw_background and not use_polygon:
        bx0, by0, bx1, by1 = _tight_text_rect(
            bb, text_w=max_w, text_h=total_h
        )
        bw, bh = bx1 - bx0, by1 - by0
        draw.rounded_rectangle(
            (bx0, by0, bx1, by1),
            radius=min(8, bw // 6, bh // 6),
            fill=TEXT_BG_FILL,
            outline=(0, 0, 0, min(255, TEXT_BG_ALPHA + 40)),
            width=1,
        )
        cx = (bx0 + bx1) // 2
        cy = (by0 + by1) // 2
        y = cy - total_h // 2
        for line, lh in zip(lines, line_heights):
            lw = font.getbbox(line)[2] - font.getbbox(line)[0]
            draw.text(
                (cx - lw // 2, y), line, fill=TRANSLATED_TEXT_RGB, font=font
            )
            y += lh + line_gap
        return

    _, _, inset_x, inset_y = _bubble_inner_metrics(box_w, box_h)
    y = y0 + inset_y + max(0, (box_h - 2 * inset_y - total_h) // 2)
    for line, lh in zip(lines, line_heights):
        lw = font.getbbox(line)[2] - font.getbbox(line)[0]
        tx = x0 + inset_x + max(0, (box_w - 2 * inset_x - lw) // 2)
        draw.text((tx, y), line, fill=TRANSLATED_TEXT_RGB, font=font)
        y += lh + line_gap


def _fit_vertical_text_to_box(
    text: str,
    box_w: int,
    box_h: int,
    font_candidates: list[str],
) -> tuple[
    ImageFont.FreeTypeFont | ImageFont.ImageFont,
    list[str],
    int,
    int,
    int,
    int,
] | None:
    """Ajuste un rendu vertical: caracteres empiles haut -> bas."""
    usable_w, usable_h, _, _ = _bubble_inner_metrics(box_w, box_h)
    chars = list(text.strip())
    if not chars:
        return None

    upper = min(_adaptive_font_cap(box_w, box_h), usable_h, max(MIN_FONT_SIZE, usable_h))
    for size in range(upper, MIN_FONT_SIZE - 1, -1):
        font = _load_font(size, font_candidates)
        char_gap = max(1, size // 12)
        col_gap = max(2, size // 4)
        sample_h = font.getbbox("M")[3] - font.getbbox("M")[1]
        if sample_h <= 0:
            continue
        rows = max(1, (usable_h + char_gap) // (sample_h + char_gap))
        if rows <= 0:
            continue
        cols_count = (len(chars) + rows - 1) // rows
        cols: list[str] = []
        max_char_w = 0
        for i in range(cols_count):
            chunk = "".join(chars[i * rows : (i + 1) * rows])
            if chunk:
                cols.append(chunk)
                for ch in chunk:
                    cw = font.getbbox(ch)[2] - font.getbbox(ch)[0]
                    max_char_w = max(max_char_w, cw)
        if not cols:
            continue
        total_w = len(cols) * max_char_w + max(0, len(cols) - 1) * col_gap
        if total_w <= usable_w:
            return font, cols, sample_h, max_char_w, char_gap, col_gap
    return None


def _draw_bubble_overlay_vertical(
    draw: ImageDraw.ImageDraw,
    bb: BoundingBox,
    text: str,
    font_candidates: list[str],
    draw_background: bool = False,
    polygon: list[tuple[int, int]] | None = None,
) -> None:
    x0, y0, x1, y1 = bb.x_min, bb.y_min, bb.x_max, bb.y_max
    box_w = x1 - x0
    box_h = y1 - y0
    if box_w < 12 or box_h < 10:
        return

    use_polygon = (
        draw_background and polygon is not None and len(polygon) >= 3
    )
    if use_polygon:
        draw.polygon(polygon, fill=TEXT_BG_FILL)
        sx = max(2, int(box_w * 0.08))
        sy = max(2, int(box_h * 0.08))
        x0, y0, x1, y1 = x0 + sx, y0 + sy, x1 - sx, y1 - sy
        box_w, box_h = x1 - x0, y1 - y0
        if box_w < 8 or box_h < 8:
            return
        draw_background = False

    fitted = _fit_vertical_text_to_box(text, box_w, box_h, font_candidates)
    if not fitted:
        _draw_bubble_overlay(
            draw,
            BoundingBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1),
            text,
            font_candidates,
            draw_background=draw_background,
        )
        return
    font, cols, char_h, char_w, char_gap, col_gap = fitted

    col_h_total = max(
        len(col) * char_h + max(0, len(col) - 1) * char_gap for col in cols
    )
    total_w = len(cols) * char_w + max(0, len(cols) - 1) * col_gap
    if draw_background:
        tight = BoundingBox(x_min=x0, y_min=y0, x_max=x1, y_max=y1)
        bx0, by0, bx1, by1 = _tight_text_rect(
            tight, text_w=total_w, text_h=col_h_total, pad_x=10, pad_y=8
        )
        draw.rounded_rectangle(
            (bx0, by0, bx1, by1),
            radius=min(8, (bx1 - bx0) // 6, (by1 - by0) // 6),
            fill=TEXT_BG_FILL,
            outline=(0, 0, 0, min(255, TEXT_BG_ALPHA + 40)),
            width=1,
        )
        start_x = bx0 + max(0, ((bx1 - bx0) - total_w) // 2)
        base_cy = (by0 + by1) // 2 - col_h_total // 2
        for col_i, col in enumerate(cols):
            cx = start_x + col_i * (char_w + col_gap)
            cy = base_cy
            for ch in col:
                cw = font.getbbox(ch)[2] - font.getbbox(ch)[0]
                tx = cx + max(0, (char_w - cw) // 2)
                draw.text((tx, cy), ch, fill=TRANSLATED_TEXT_RGB, font=font)
                cy += char_h + char_gap
        return

    _, _, inset_x, inset_y = _bubble_inner_metrics(box_w, box_h)
    start_x = x0 + inset_x + max(0, (box_w - 2 * inset_x - total_w) // 2)
    for col_i, col in enumerate(cols):
        cx = start_x + col_i * (char_w + col_gap)
        col_h = len(col) * char_h + max(0, len(col) - 1) * char_gap
        cy = y0 + inset_y + max(0, (box_h - 2 * inset_y - col_h) // 2)
        for ch in col:
            cw = font.getbbox(ch)[2] - font.getbbox(ch)[0]
            tx = cx + max(0, (char_w - cw) // 2)
            draw.text((tx, cy), ch, fill=TRANSLATED_TEXT_RGB, font=font)
            cy += char_h + char_gap


def inpaint_and_render(
    image_path: Path,
    blocks: list[TextBlock],
    output_path: Path,
    target_language: str = "fr",
) -> None:
    """Efface le texte source puis superpose la traduction."""
    blocks = refine_blocks_for_render(image_path, blocks)

    # Forme exacte des bulles (polygones) détectée sur l'image originale.
    polygons: dict[int, list[tuple[int, int]]] = {}
    base_bgr = cv2.imread(str(image_path))
    if base_bgr is not None:
        for block in blocks:
            if _looks_like_sfx_text(block.originalText):
                continue
            poly = detect_bubble_polygon(base_bgr, block.boundingBox)
            if poly:
                polygons[block.id] = poly

    cleaned = output_path.with_name(f".{output_path.stem}_clean.png")
    try:
        erase_text_regions(image_path, blocks, cleaned)
        source = cleaned
    except Exception:
        source = image_path

    pil = Image.open(source).convert("RGBA")
    img_w, img_h = pil.size

    clipped_blocks = [
        block.model_copy(
            update={"boundingBox": _clip_bbox(block.boundingBox, img_w, img_h)}
        )
        for block in blocks
    ]

    to_render: list[TextBlock] = []
    for block in clipped_blocks:
        hinted_text, _, _ = _extract_render_hints(block.translatedText)
        clean = _sanitize_text(hinted_text, target_language)
        if clean:
            to_render.append(block.model_copy(update={"translatedText": clean}))

    overlay = Image.new("RGBA", pil.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    vertical_mode = _is_vertical_text_mode()
    for block in to_render:
        font_candidates = _font_candidates_for_block(block)
        hinted_text, dir_hint, bg_hint = _extract_render_hints(block.translatedText)
        use_vertical = (
            True
            if dir_hint == "vertical"
            else False
            if dir_hint == "horizontal"
            else vertical_mode
        )
        # Texte traduit en alphabet latin : toujours horizontal.
        if target_language in _LATIN_TARGETS and not _has_cjk(hinted_text):
            use_vertical = False
        draw_bg = bg_hint is not False
        if use_vertical:
            _draw_bubble_overlay_vertical(
                draw,
                block.boundingBox,
                hinted_text,
                font_candidates,
                draw_background=draw_bg,
                polygon=polygons.get(block.id),
            )
        else:
            _draw_bubble_overlay(
                draw,
                block.boundingBox,
                hinted_text,
                font_candidates,
                draw_background=draw_bg,
                polygon=polygons.get(block.id),
            )

    composed = Image.alpha_composite(pil, overlay).convert("RGB")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    composed.save(output_path, format="PNG")


def assess_render_quality(
    rendered_image_path: Path,
    blocks: list[TextBlock],
) -> set[int]:
    """Contrôle qualité visuel rapide sur lisibilité des bulles rendues."""
    img = cv2.imread(str(rendered_image_path))
    if img is None:
        return set()
    h, w = img.shape[:2]
    low_visibility_ids: set[int] = set()
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    for block in blocks:
        bb = _clip_bbox(block.boundingBox, w, h)
        roi = gray[bb.y_min : bb.y_max, bb.x_min : bb.x_max]
        if roi.size == 0:
            low_visibility_ids.add(block.id)
            continue
        contrast = float(roi.std())
        _, ink = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        ink_density = float(np.count_nonzero(ink)) / max(1, ink.size)
        # Trop peu de contraste ou presque pas de traits visibles = lecture fragile.
        if contrast < 18.0 or ink_density < 0.01 or ink_density > 0.9:
            low_visibility_ids.add(block.id)
    return low_visibility_ids
