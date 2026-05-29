"""Superposition des bulles traduites sur la zone exacte du texte original."""

import os
import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from models import BoundingBox, TextBlock

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
    if len(t) <= 8 and re.fullmatch(r"[\u30a0-\u30ff\u3040-\u309fー…・！？\s]+", t):
        return True
    if re.search(r"(ゴロ|ドン|ガタ|にゃ|ニャ|わん|ワン|シーン|バキ|ズキ|ドキ)", t):
        return True
    return False


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


def expand_bbox_to_bubble_region(base_bgr: np.ndarray, bb: BoundingBox) -> BoundingBox:
    """Étend la bbox jusqu'à la bulle blanche (ou zone claire) englobante."""
    h, w = base_bgr.shape[:2]
    gray = cv2.cvtColor(base_bgr, cv2.COLOR_BGR2GRAY)
    x0, y0, x1, y1 = bb.x_min, bb.y_min, bb.x_max, bb.y_max
    bw, bh = x1 - x0, y1 - y0
    if bw < 6 or bh < 6:
        return bb

    pad = max(24, bw, bh)
    rx0 = max(0, x0 - pad)
    ry0 = max(0, y0 - pad)
    rx1 = min(w, x1 + pad)
    ry1 = min(h, y1 + pad)
    roi = gray[ry0:ry1, rx0:rx1]
    if roi.size == 0:
        return bb

    _, white = cv2.threshold(roi, 172, 255, cv2.THRESH_BINARY)
    cx = (x0 + x1) // 2 - rx0
    cy = (y0 + y1) // 2 - ry0
    cx = max(0, min(roi.shape[1] - 1, cx))
    cy = max(0, min(roi.shape[0] - 1, cy))

    n, _, stats, _ = cv2.connectedComponentsWithStats(white)
    for i in range(1, n):
        sx, sy, sw, sh, area = stats[i]
        if area < 120:
            continue
        if sx <= cx <= sx + sw and sy <= cy <= sy + sh:
            return BoundingBox(
                x_min=max(0, rx0 + sx - 3),
                y_min=max(0, ry0 + sy - 3),
                x_max=min(w, rx0 + sx + sw + 3),
                y_max=min(h, ry0 + sy + sh + 3),
            )

    grow = max(8, min(40, bw // 4, bh // 4))
    return BoundingBox(
        x_min=max(0, x0 - grow),
        y_min=max(0, y0 - grow),
        x_max=min(w, x1 + grow),
        y_max=min(h, y1 + grow),
    )


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
) -> None:
    """Texte centré dans la bulle; fond blanc ajusté au contenu."""
    x0, y0, x1, y1 = bb.x_min, bb.y_min, bb.x_max, bb.y_max
    box_w = x1 - x0
    box_h = y1 - y0
    if box_w < 12 or box_h < 10:
        return

    fitted = _fit_text_to_box(text, box_w, box_h, font_candidates)
    if not fitted:
        return
    font, lines, line_heights, line_gap = fitted
    total_h, max_w, _ = _layout_metrics(font, lines, line_gap)

    if draw_background:
        bx0, by0, bx1, by1 = _tight_text_rect(
            bb, text_w=max_w, text_h=total_h
        )
        bw, bh = bx1 - bx0, by1 - by0
        draw.rounded_rectangle(
            (bx0, by0, bx1, by1),
            radius=min(8, bw // 6, bh // 6),
            fill=(255, 255, 255, 255),
            outline=(0, 0, 0),
            width=1,
        )
        cx = (bx0 + bx1) // 2
        cy = (by0 + by1) // 2
        y = cy - total_h // 2
        for line, lh in zip(lines, line_heights):
            lw = font.getbbox(line)[2] - font.getbbox(line)[0]
            draw.text((cx - lw // 2, y), line, fill=(0, 0, 0), font=font)
            y += lh + line_gap
        return

    _, _, inset_x, inset_y = _bubble_inner_metrics(box_w, box_h)
    y = y0 + inset_y + max(0, (box_h - 2 * inset_y - total_h) // 2)
    for line, lh in zip(lines, line_heights):
        lw = font.getbbox(line)[2] - font.getbbox(line)[0]
        tx = x0 + inset_x + max(0, (box_w - 2 * inset_x - lw) // 2)
        draw.text((tx, y), line, fill=(0, 0, 0), font=font)
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
) -> None:
    x0, y0, x1, y1 = bb.x_min, bb.y_min, bb.x_max, bb.y_max
    box_w = x1 - x0
    box_h = y1 - y0
    if box_w < 12 or box_h < 10:
        return

    fitted = _fit_vertical_text_to_box(text, box_w, box_h, font_candidates)
    if not fitted:
        _draw_bubble_overlay(
            draw,
            bb,
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
            fill=(255, 255, 255, 255),
            outline=(0, 0, 0),
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
                draw.text((tx, cy), ch, fill=(0, 0, 0), font=font)
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
            draw.text((tx, cy), ch, fill=(0, 0, 0), font=font)
            cy += char_h + char_gap


def inpaint_and_render(
    image_path: Path,
    blocks: list[TextBlock],
    output_path: Path,
    target_language: str = "fr",
) -> None:
    """Superpose la traduction sur l'image originale (sans inpaint ni flou)."""
    pil = Image.open(image_path).convert("RGBA")
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
        draw_bg = True
        if use_vertical:
            _draw_bubble_overlay_vertical(
                draw,
                block.boundingBox,
                hinted_text,
                font_candidates,
                draw_background=draw_bg,
            )
        else:
            _draw_bubble_overlay(
                draw,
                block.boundingBox,
                hinted_text,
                font_candidates,
                draw_background=draw_bg,
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
