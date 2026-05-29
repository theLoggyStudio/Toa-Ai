"""Détection de bulles (OpenCV) et OCR (Manga-OCR / EasyOCR)."""

import re
from pathlib import Path
from typing import List

import cv2
import numpy as np

from config import MAX_BLOCKS_PER_PAGE, is_ocr_deep_mode, is_ocr_fast_mode
from models import BoundingBox, TextBlock

_OCR_PLACEHOLDER_RE = re.compile(
    r"^\[page\s+\d+\s+bulle\s+\d+\]$|^\(dialogue manga\)$",
    re.IGNORECASE,
)

_manga_ocr = None
_easyocr_reader = None


def reset_ocr_engines() -> None:
    """Libère les moteurs OCR en mémoire entre deux tâches."""
    global _manga_ocr, _easyocr_reader
    _manga_ocr = None
    _easyocr_reader = None


def _get_manga_ocr():
    global _manga_ocr
    if _manga_ocr is None:
        try:
            from manga_ocr import MangaOcr

            _manga_ocr = MangaOcr()
        except Exception as exc:
            raise RuntimeError(f"manga-ocr indisponible: {exc}") from exc
    return _manga_ocr


def _get_easyocr():
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr

        _easyocr_reader = easyocr.Reader(["ko", "en"], gpu=False)
    return _easyocr_reader


def _iou(a: BoundingBox, b: BoundingBox) -> float:
    x1 = max(a.x_min, b.x_min)
    y1 = max(a.y_min, b.y_min)
    x2 = min(a.x_max, b.x_max)
    y2 = min(a.y_max, b.y_max)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if inter == 0:
        return 0.0
    area_a = (a.x_max - a.x_min) * (a.y_max - a.y_min)
    area_b = (b.x_max - b.x_min) * (b.y_max - b.y_min)
    return inter / (area_a + area_b - inter)


def _box_area(box: BoundingBox) -> int:
    return max(0, box.x_max - box.x_min) * max(0, box.y_max - box.y_min)


def is_placeholder_text(text: str) -> bool:
    return bool(_OCR_PLACEHOLDER_RE.match(text.strip()))


def _is_garbage_ocr(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if re.fullmatch(r"[\u3099\u309A\u309B\u309C\s]+", t):
        return True
    if len(t) == 1 and t in "゙゚、。・":
        return True
    if len(t) <= 2 and not re.search(r"[\u3040-\u30ff\u3400-\u9fff]{2}", t):
        if not re.fullmatch(r"[\u30a0-\u30ff\u3040-\u309fー！？…]+", t):
            return True
    return False


def has_readable_source(text: str, source_language: str) -> bool:
    t = text.strip()
    if not t or is_placeholder_text(t) or _is_garbage_ocr(t):
        return False
    if re.fullmatch(r"[\u30fb\uFF0E\u3002\.・…\s、]+", t):
        return False
    if source_language == "ja":
        return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", t))
    if source_language == "ko":
        return bool(re.search(r"[\uac00-\ud7af]", t))
    return len(t) >= 2


def ensure_ocr_ready(source_language: str) -> None:
    if is_ocr_fast_mode():
        return
    if source_language == "ja":
        try:
            import manga_ocr  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "manga-ocr n'est pas installé. Exécutez depuis backend : "
                "pip install -r requirements-ml.txt"
            ) from exc


def _dedupe_boxes(boxes: List[BoundingBox]) -> List[BoundingBox]:
    """Privilégie les petites zones texte plutôt qu'un grand rectangle englobant."""
    boxes.sort(key=_box_area)
    out: List[BoundingBox] = []
    for box in boxes:
        if any(_iou(box, kept) > 0.35 for kept in out):
            continue
        out = [k for k in out if not _is_mostly_inside(k, box)]
        out.append(box)
        if len(out) >= MAX_BLOCKS_PER_PAGE:
            break
    return out


def _union_boxes(boxes: List[BoundingBox]) -> BoundingBox:
    return BoundingBox(
        x_min=min(b.x_min for b in boxes),
        y_min=min(b.y_min for b in boxes),
        x_max=max(b.x_max for b in boxes),
        y_max=max(b.y_max for b in boxes),
    )


def _merge_nearby_boxes(boxes: List[BoundingBox]) -> List[BoundingBox]:
    """Fusionne les fragments d'onomatopée (ex. ゴ + ロ + ゴ)."""
    if len(boxes) < 2:
        return boxes
    boxes = sorted(boxes, key=lambda b: (b.x_min, b.y_min))
    merged: List[BoundingBox] = []
    for box in boxes:
        if not merged:
            merged.append(box)
            continue
        prev = merged[-1]
        gap_x = max(0, max(prev.x_min, box.x_min) - min(prev.x_max, box.x_max))
        gap_y = max(0, max(prev.y_min, box.y_min) - min(prev.y_max, box.y_max))
        pw, ph = prev.x_max - prev.x_min, prev.y_max - prev.y_min
        bw, bh = box.x_max - box.x_min, box.y_max - box.y_min
        x_ov = min(prev.x_max, box.x_max) - max(prev.x_min, box.x_min)
        y_ov = min(prev.y_max, box.y_max) - max(prev.y_min, box.y_min)
        prev_cx = (prev.x_min + prev.x_max) // 2
        box_cx = (box.x_min + box.x_max) // 2
        same_column = abs(prev_cx - box_cx) < 45
        vertical = (
            same_column
            and gap_y < 220
            and (x_ov >= 0.2 * min(pw, bw) or same_column)
        )
        horizontal = y_ov >= 0.35 * min(ph, bh) and gap_x < max(pw, bw) * 1.5 and gap_x < 90
        if vertical or horizontal:
            merged[-1] = _union_boxes([prev, box])
        else:
            merged.append(box)
    return merged


def _looks_like_vertical_sfx_box(box: BoundingBox) -> bool:
    bw = box.x_max - box.x_min
    bh = box.y_max - box.y_min
    return bh >= 35 and bw <= 90 and bh > bw * 1.15


def _expand_vertical_sfx_box(
    gray: np.ndarray, box: BoundingBox, h: int, w: int
) -> BoundingBox:
    """Étend une zone verticale courte (limité au voisinage de l'onomatopée)."""
    bw = box.x_max - box.x_min
    bh = box.y_max - box.y_min
    if bh < bw * 1.1 or bh < 18 or bh > 180 or bw > 70:
        return box
    if bh >= 55 or _box_area(box) >= 2200:
        return box
    pad_x = max(6, bw)
    y_margin = max(40, int(bh * 1.8))
    x0 = max(0, box.x_min - pad_x)
    x1 = min(w, box.x_max + pad_x)
    y0 = max(0, box.y_min - y_margin)
    y1 = min(h, box.y_max + y_margin)
    col = gray[y0:y1, x0:x1]
    if col.size == 0:
        return box
    _, bright = cv2.threshold(col, 178, 255, cv2.THRESH_BINARY)
    _, dark = cv2.threshold(col, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask = cv2.bitwise_or(bright, dark)
    row_density = np.count_nonzero(mask, axis=1) / max(1, mask.shape[1])
    active = np.where(row_density > 0.08)[0]
    if active.size == 0:
        return box
    new_y0 = y0 + max(0, int(active[0]) - 3)
    new_y1 = y0 + min(y1 - y0, int(active[-1]) + 4)
    if new_y1 - new_y0 < int(bh * 0.85):
        return box
    return BoundingBox(x_min=x0, y_min=new_y0, x_max=x1, y_max=new_y1)


def _from_vertical_ink_columns(gray: np.ndarray, h: int, w: int) -> List[BoundingBox]:
    """Colonnes d'encre verticales (onomatopées sur fond sombre)."""
    page_area = w * h
    top_h = int(h * 0.52)
    roi = gray[0:top_h, :]
    blurred = cv2.GaussianBlur(roi, (3, 3), 0)
    ink = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        15,
        8,
    )
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (4, 18))
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, v_kernel, iterations=1)
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink)
    boxes: List[BoundingBox] = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < page_area * 0.0002 or area > page_area * 0.02:
            continue
        if bh < 35 or bw > 85 or bw < 10:
            continue
        if bw / max(bh, 1) > 0.75:
            continue
        pad = 5
        boxes.append(
            BoundingBox(
                x_min=max(0, x - pad),
                y_min=max(0, y - pad),
                x_max=min(w, x + bw + pad),
                y_max=min(top_h, y + bh + pad),
            )
        )
    return boxes


def _ocr_crop_box(
    gray: np.ndarray, detection: BoundingBox, h: int, w: int
) -> BoundingBox:
    """Zone OCR : resserrée si possible, sinon la détection (évite les crops 30×40)."""
    tight = _tighten_box_to_text(gray, detection, h, w)
    if _box_area(tight) < max(1200, _box_area(detection) * 0.12):
        crop_box = detection
    else:
        crop_box = tight
    return _cap_box_size(crop_box, h, w, MAX_BOX_FRAC)


def _is_mostly_inside(inner: BoundingBox, outer: BoundingBox) -> bool:
    """Évite qu'une zone géante écrase des bulles plus petites."""
    if _box_area(inner) >= _box_area(outer):
        return False
    x1 = max(inner.x_min, outer.x_min)
    y1 = max(inner.y_min, outer.y_min)
    x2 = min(inner.x_max, outer.x_max)
    y2 = min(inner.y_max, outer.y_max)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    return inter >= _box_area(inner) * 0.85


def _tighten_box_to_text(
    gray: np.ndarray, box: BoundingBox, h: int, w: int
) -> BoundingBox:
    """Réduit la bbox à l'empreinte du texte (pas toute la bulle blanche)."""
    x0, y0, x1, y1 = box.x_min, box.y_min, box.x_max, box.y_max
    crop = gray[y0:y1, x0:x1]
    if crop.size == 0:
        return box

    _, text_mask = cv2.threshold(
        crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    text_mask = cv2.morphologyEx(text_mask, cv2.MORPH_OPEN, kernel, iterations=1)
    coords = cv2.findNonZero(text_mask)
    if coords is None:
        return box

    tx, ty, tw, th = cv2.boundingRect(coords)
    pad = 5
    return BoundingBox(
        x_min=max(0, x0 + tx - pad),
        y_min=max(0, y0 + ty - pad),
        x_max=min(w, x0 + tx + tw + pad),
        y_max=min(h, y0 + ty + th + pad),
    )


MAX_BOX_FRAC = 0.045


def _cap_box_size(box: BoundingBox, h: int, w: int, max_frac: float) -> BoundingBox:
    """Limite la surface (pas chaque côté) pour garder les bulles verticales."""
    max_area = (w * h) * max_frac
    area = _box_area(box)
    if area <= max_area:
        return box
    scale = (max_area / area) ** 0.5
    bw = max(14, int((box.x_max - box.x_min) * scale))
    bh = max(12, int((box.y_max - box.y_min) * scale))
    cx = (box.x_min + box.x_max) // 2
    cy = (box.y_min + box.y_max) // 2
    return BoundingBox(
        x_min=max(0, cx - bw // 2),
        y_min=max(0, cy - bh // 2),
        x_max=min(w, cx + bw // 2),
        y_max=min(h, cy + bh // 2),
    )


def _split_oversized_bubble(
    gray: np.ndarray, box: BoundingBox, h: int, w: int
) -> List[BoundingBox]:
    """Si une zone englobe plusieurs textes, tente de les séparer."""
    page_area = h * w
    if _box_area(box) < page_area * 0.03:
        return [box]

    x0, y0, x1, y1 = box.x_min, box.y_min, box.x_max, box.y_max
    crop = gray[y0:y1, x0:x1]
    if crop.size == 0:
        return [box]

    _, ink = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel, iterations=1)
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink)
    sub: List[BoundingBox] = []
    min_sub = page_area * 0.00012
    for i in range(1, n):
        sx, sy, sw, sh, sarea = stats[i]
        if sarea < min_sub or sarea > page_area * MAX_BOX_FRAC or sw < 10 or sh < 10:
            continue
        pad = 4
        sub.append(
            BoundingBox(
                x_min=max(0, x0 + sx - pad),
                y_min=max(0, y0 + sy - pad),
                x_max=min(x1, x0 + sx + sw + pad),
                y_max=min(y1, y0 + sy + sh + pad),
            )
        )
    return sub if len(sub) > 1 else [box]


def _ink_density(crop: np.ndarray) -> float:
    _, mask = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return float(np.count_nonzero(mask)) / max(1, mask.size)


def _filter_text_regions(
    boxes: List[BoundingBox],
    gray: np.ndarray,
    h: int,
    w: int,
    *,
    require_white_bubble: bool,
) -> List[BoundingBox]:
    """Filtre les zones texte — bulles blanches OU encre (onomatopées)."""
    page_area = w * h
    kept: List[BoundingBox] = []
    for box in boxes:
        crop = gray[box.y_min : box.y_max, box.x_min : box.x_max]
        if crop.size == 0:
            continue
        area = _box_area(box)
        if area < page_area * 0.00012 or area > page_area * MAX_BOX_FRAC:
            continue
        bw = box.x_max - box.x_min
        bh = box.y_max - box.y_min
        if bw < 14 or bh < 12:
            continue
        aspect = bw / max(bh, 1)
        if aspect > 14 or aspect < 0.06:
            continue

        mean_val = float(crop.mean())
        is_bright_vertical = (
            mean_val > 175 and bh >= 28 and bw <= 90 and bh > bw * 1.15
        )
        if not is_bright_vertical:
            density = _ink_density(crop)
            if density < 0.015 or density > 0.82:
                continue

        if require_white_bubble and mean_val < 145:
            continue

        kept.append(box)
    return kept


def _from_ink_clusters(gray: np.ndarray, h: int, w: int) -> List[BoundingBox]:
    """Détecte chaque cluster d'encre (bulles, onomatopées, répliques)."""
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, ink = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (4, 4))
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel, iterations=1)
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink)
    page_area = w * h
    boxes: List[BoundingBox] = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < page_area * 0.00012 or area > page_area * MAX_BOX_FRAC:
            continue
        if bw < 14 or bh < 12:
            continue
        pad = 5
        boxes.append(
            BoundingBox(
                x_min=max(0, x - pad),
                y_min=max(0, y - pad),
                x_max=min(w, x + bw + pad),
                y_max=min(h, y + bh + pad),
            )
        )
    return boxes


def _from_bright_vertical_sfx(gray: np.ndarray, h: int, w: int) -> List[BoundingBox]:
    """Onomatopées blanches verticales (contour clair, sans bulle)."""
    _, bright = cv2.threshold(gray, 184, 255, cv2.THRESH_BINARY)
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 14))
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, v_kernel, iterations=1)
    n, _, stats, _ = cv2.connectedComponentsWithStats(bright)
    page_area = w * h
    boxes: List[BoundingBox] = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < page_area * 0.00015 or area > page_area * 0.03:
            continue
        if bh < 28 or bw > 95 or bw < 8:
            continue
        if bw / max(bh, 1) > 0.8:
            continue
        pad = 4
        boxes.append(
            BoundingBox(
                x_min=max(0, x - pad),
                y_min=max(0, y - pad),
                x_max=min(w, x + bw + pad),
                y_max=min(h, y + bh + pad),
            )
        )
    return boxes


def _sort_reading_order(boxes: List[BoundingBox]) -> List[BoundingBox]:
    return sorted(boxes, key=lambda b: (b.y_min, b.x_min))


def _from_connected_components(
    gray: np.ndarray, h: int, w: int, thresh_val: int
) -> List[BoundingBox]:
    _, mask = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    min_area = (w * h) * 0.0006
    max_area = (w * h) * 0.28
    boxes: List[BoundingBox] = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_area or area > max_area:
            continue
        if bw < 22 or bh < 14:
            continue
        aspect = bw / max(bh, 1)
        if aspect > 8 or aspect < 0.2:
            continue
        crop = gray[y : y + bh, x : x + bw]
        if crop.size == 0 or float(crop.mean()) < 150:
            continue
        pad = 3
        boxes.append(
            BoundingBox(
                x_min=max(0, x - pad),
                y_min=max(0, y - pad),
                x_max=min(w, x + bw + pad),
                y_max=min(h, y + bh + pad),
            )
        )
    return boxes


def _from_contours_inv(gray: np.ndarray, h: int, w: int) -> List[BoundingBox]:
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blurred, 200, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = (w * h) * 0.0006
    max_area = (w * h) * 0.28
    boxes: List[BoundingBox] = []
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        area = bw * bh
        if area < min_area or area > max_area:
            continue
        if bw < 22 or bh < 14:
            continue
        crop = gray[y : y + bh, x : x + bw]
        if crop.size == 0 or float(crop.mean()) < 140:
            continue
        pad = 3
        boxes.append(
            BoundingBox(
                x_min=max(0, x - pad),
                y_min=max(0, y - pad),
                x_max=min(w, x + bw + pad),
                y_max=min(h, y + bh + pad),
            )
        )
    return boxes


def detect_bubbles(image_path: Path) -> List[BoundingBox]:
    img = cv2.imread(str(image_path))
    if img is None:
        return []
    h, w = img.shape[:2]

    if is_ocr_fast_mode():
        return []

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    candidates: List[BoundingBox] = []
    candidates.extend(_from_ink_clusters(gray, h, w))
    candidates.extend(_from_vertical_ink_columns(gray, h, w))
    candidates.extend(_from_bright_vertical_sfx(gray, h, w))
    thresholds = [185, 200, 215]
    if is_ocr_deep_mode():
        thresholds.extend([170, 230])
    for thresh in thresholds:
        candidates.extend(_from_connected_components(gray, h, w, thresh))

    boxes = _dedupe_boxes(candidates)
    boxes = _filter_text_regions(boxes, gray, h, w, require_white_bubble=False)

    expanded: List[BoundingBox] = []
    for box in boxes:
        expanded.extend(_split_oversized_bubble(gray, box, h, w))
    boxes = _dedupe_boxes(expanded)
    boxes = _filter_text_regions(boxes, gray, h, w, require_white_bubble=False)
    boxes = _merge_nearby_boxes(boxes)
    boxes = _dedupe_boxes(boxes)

    return _sort_reading_order(boxes)[:MAX_BLOCKS_PER_PAGE]


def extract_text(
    image_path: Path,
    bbox: BoundingBox,
    source_language: str,
    page_index: int = 0,
    block_index: int = 0,
) -> str:
    if is_ocr_fast_mode():
        return f"[page {page_index + 1} bulle {block_index + 1}]"

    img = cv2.imread(str(image_path))
    if img is None:
        return ""
    crop = img[bbox.y_min : bbox.y_max, bbox.x_min : bbox.x_max]
    if crop.size == 0:
        return ""

    text = _extract_text_once(crop, source_language).strip()
    if has_readable_source(text, source_language) or not is_ocr_deep_mode():
        return text

    # Deuxième passe : variantes de prétraitement pour rattraper les textes discrets.
    for variant in _build_ocr_variants(crop):
        text = _extract_text_once(variant, source_language).strip()
        if has_readable_source(text, source_language):
            return text
    return text


def _extract_text_once(crop: np.ndarray, source_language: str) -> str:
    if source_language == "ja":
        from PIL import Image

        mocr = _get_manga_ocr()
        pil = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        return mocr(pil).strip()
    reader = _get_easyocr()
    results = reader.readtext(crop)
    return " ".join(r[1] for r in results).strip()


def _build_ocr_variants(crop: np.ndarray) -> List[np.ndarray]:
    variants: List[np.ndarray] = []
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    # Upscale léger pour petites polices.
    upscaled = cv2.resize(
        gray,
        None,
        fx=1.7,
        fy=1.7,
        interpolation=cv2.INTER_CUBIC,
    )
    variants.append(cv2.cvtColor(upscaled, cv2.COLOR_GRAY2BGR))

    # Renforcement du contraste local.
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    contrasted = clahe.apply(gray)
    variants.append(cv2.cvtColor(contrasted, cv2.COLOR_GRAY2BGR))

    # Binarisation adaptive pour textes faibles sur fond bruité.
    adaptive = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        21,
        7,
    )
    variants.append(cv2.cvtColor(adaptive, cv2.COLOR_GRAY2BGR))

    # Inversion pour certains textes clairs sur fond sombre.
    variants.append(cv2.cvtColor(cv2.bitwise_not(adaptive), cv2.COLOR_GRAY2BGR))
    return variants


def run_ocr_on_page(
    image_path: Path, source_language: str, page_index: int
) -> List[TextBlock]:
    ensure_ocr_ready(source_language)
    img = cv2.imread(str(image_path))
    if img is None:
        return []
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]

    boxes = detect_bubbles(image_path)
    candidates: List[TextBlock] = []
    for bbox in boxes:
        work_box = _expand_vertical_sfx_box(gray, bbox, h, w)
        ocr_box = _ocr_crop_box(gray, work_box, h, w)
        if _looks_like_vertical_sfx_box(work_box) and _box_area(ocr_box) < _box_area(work_box) * 0.5:
            ocr_box = work_box
        render_box = _cap_box_size(bbox, h, w, MAX_BOX_FRAC)
        if _box_area(render_box) < 80:
            render_box = ocr_box
        if _box_area(ocr_box) < 80:
            continue
        text = extract_text(
            image_path, ocr_box, source_language, page_index, len(candidates)
        ).strip()
        if not has_readable_source(text, source_language):
            continue
        candidates.append(
            TextBlock(
                id=page_index * 1000 + len(candidates),
                boundingBox=render_box,
                originalText=text,
            )
        )

    return _dedupe_text_blocks(_sort_reading_order_blocks(candidates))


def _dedupe_text_blocks(blocks: List[TextBlock]) -> List[TextBlock]:
    """Supprime les doublons OCR (même zone ou texte identique)."""
    kept: List[TextBlock] = []
    for block in blocks:
        dup = False
        for other in kept:
            if block.originalText == other.originalText and _iou(
                block.boundingBox, other.boundingBox
            ) > 0.25:
                dup = True
                break
            if _iou(block.boundingBox, other.boundingBox) > 0.55:
                if len(block.originalText) <= len(other.originalText):
                    dup = True
                    break
                kept.remove(other)
        if not dup:
            kept.append(block)
    return kept


def _sort_reading_order_blocks(blocks: List[TextBlock]) -> List[TextBlock]:
    return sorted(
        blocks,
        key=lambda b: (b.boundingBox.y_min, b.boundingBox.x_min),
    )
