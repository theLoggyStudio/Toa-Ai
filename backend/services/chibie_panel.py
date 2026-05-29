"""Composition visuelle du bandeau Toa sous chaque page."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent.parent
CHIBIE_ASSETS_DIR = BASE_DIR.parent / "frontend" / "src" / "assets" / "Chibie"
# Jamais Chibie.png — uniquement les sprites d'état (joie.png, pensif.png, …).
CHIBIE_MOOD_FILES = (
    "pensif",
    "joie",
    "rire",
    "exiter",
    "surprise",
    "inquiet",
    "peur",
    "tristesse",
    "colere",
    "confus",
    "degout",
    "fier",
    "soulager",
    "fatiguer",
    "timide",
)

# Thème café (bordures + fond)
COFFEE_BORDER = (107, 68, 35)
COFFEE_BG = (235, 224, 205)
COFFEE_BUBBLE = (255, 252, 245)

FOOTER_MIN_H = 118
FOOTER_RATIO = 0.11
CHIBIE_MIN_HEIGHT = 88
CHIBIE_MAX_HEIGHT = 108
BORDER_W = 3


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        os.getenv("MANGA_DIALOGUE_FONT_PATH", ""),
        str(BASE_DIR / "assets" / "fonts" / "WildWordsRoman.ttf"),
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if not path:
            continue
        p = Path(path)
        if not p.is_absolute():
            p = BASE_DIR / path
        try:
            return ImageFont.truetype(str(p), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _resolve_chibie_image(mood: str) -> Path:
    mood = (mood or "pensif").lower()
    if mood == "chibie":
        mood = "pensif"
    for name in (mood, *(m for m in CHIBIE_MOOD_FILES if m != mood)):
        path = CHIBIE_ASSETS_DIR / f"{name}.png"
        if path.exists():
            return path
    raise FileNotFoundError(
        f"Aucun sprite Toa d'état trouvé dans {CHIBIE_ASSETS_DIR}"
    )


def _line_width(font: ImageFont.ImageFont, line: str) -> int:
    bbox = font.getbbox(line)
    return bbox[2] - bbox[0]


def _line_height(font: ImageFont.ImageFont, line: str) -> int:
    bbox = font.getbbox(line)
    return max(bbox[3] - bbox[1], int(getattr(font, "size", 12) * 1.1))


def _wrap_comment(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [text] if text else []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        test = f"{current} {word}"
        if _line_width(font, test) <= max_width:
            current = test
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _layout_comment(
    text: str,
    inner_w: int,
    inner_h: int,
    *,
    max_font: int,
    min_font: int = 8,
    line_gap: int = 3,
) -> tuple[ImageFont.ImageFont, list[str], int, int]:
    """Choisit police + lignes pour tenir dans inner_w × inner_h."""
    pad_x, pad_y = 10, 8
    text_w = max(40, inner_w - pad_x * 2)
    text_h = max(20, inner_h - pad_y * 2)

    for size in range(max_font, min_font - 1, -1):
        font = _load_font(size)
        lines = _wrap_comment(text, font, text_w)
        if not lines:
            return font, [], pad_x * 2, pad_y * 2
        heights = [_line_height(font, ln) for ln in lines]
        total_h = sum(heights) + max(0, len(lines) - 1) * line_gap
        if total_h <= text_h:
            max_line_w = max(_line_width(font, ln) for ln in lines)
            return font, lines, max_line_w + pad_x * 2, total_h + pad_y * 2

    font = _load_font(min_font)
    lines = _wrap_comment(text, font, text_w)
    visible: list[str] = []
    used = 0
    for line in lines:
        lh = _line_height(font, line)
        gap = line_gap if visible else 0
        if used + gap + lh > text_h:
            break
        visible.append(line)
        used += gap + lh

    if visible and len(visible) < len(lines):
        last = visible[-1]
        ellipsis = "…"
        while last and _line_width(font, last + ellipsis) > text_w:
            last = last[:-1]
        visible[-1] = (last.rstrip(".,;:") + ellipsis) if last else ellipsis

    heights = [_line_height(font, ln) for ln in visible]
    total_h = sum(heights) + max(0, len(visible) - 1) * gap
    max_line_w = max((_line_width(font, ln) for ln in visible), default=0)
    return font, visible, max_line_w + pad_x * 2, total_h + pad_y * 2


def _draw_speech_bubble(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont | None = None,
    *,
    max_font: int | None = None,
) -> None:
    x0, y0, x1, y1 = box
    inner_w = max(60, x1 - x0)
    inner_h = max(40, y1 - y0)
    pad_x, pad_y = 10, 8
    line_gap = 3

    hint = max_font or (font.size if font and hasattr(font, "size") else 14)
    fit_font, lines, bubble_w, bubble_h = _layout_comment(
        text,
        inner_w,
        inner_h,
        max_font=hint,
    )
    if not lines:
        return

    bubble_w = min(inner_w, bubble_w)
    bubble_h = min(inner_h, bubble_h)
    bx0 = x0 + max(0, (inner_w - bubble_w) // 2)
    by0 = y0 + max(0, (inner_h - bubble_h) // 2)
    bx1, by1 = bx0 + bubble_w, by0 + bubble_h

    draw.rounded_rectangle(
        (bx0, by0, bx1, by1),
        radius=10,
        fill=COFFEE_BUBBLE,
        outline=COFFEE_BORDER,
        width=BORDER_W,
    )

    text_area_h = bubble_h - pad_y * 2
    y = by0 + pad_y
    for line in lines:
        lh = _line_height(fit_font, line)
        if y + lh > by0 + pad_y + text_area_h + 1:
            break
        lw = _line_width(fit_font, line)
        tx = bx0 + pad_x + max(0, (bubble_w - pad_x * 2 - lw) // 2)
        draw.text((tx, y), line, fill=(35, 28, 22), font=fit_font)
        y += lh + line_gap


def append_chibie_footer(
    page_image_path: Path,
    output_path: Path,
    *,
    mood: str,
    comment: str,
) -> None:
    """Ajoute le bandeau Toa sous la page scan."""
    with Image.open(page_image_path) as src:
        page = src.convert("RGB")
    pw, ph = page.size
    footer_h = max(FOOTER_MIN_H, int(pw * FOOTER_RATIO))
    canvas = Image.new("RGB", (pw, ph + footer_h), COFFEE_BG)
    canvas.paste(page, (0, 0))

    draw = ImageDraw.Draw(canvas)
    footer_top = ph
    draw.rectangle(
        (0, footer_top, pw, ph + footer_h),
        fill=COFFEE_BG,
        outline=COFFEE_BORDER,
        width=BORDER_W,
    )
    draw.line([(0, footer_top), (pw, footer_top)], fill=COFFEE_BORDER, width=BORDER_W)

    chibie_path = _resolve_chibie_image(mood)
    pad = 10
    target_h = min(CHIBIE_MAX_HEIGHT, footer_h - pad * 2, CHIBIE_MIN_HEIGHT)
    with Image.open(chibie_path) as ch_src:
        ch = ch_src.convert("RGBA")
    scale = target_h / max(ch.height, 1)
    target_w = max(1, int(ch.width * scale))
    ch = ch.resize((target_w, target_h), Image.Resampling.LANCZOS)

    chibie_x = pad + BORDER_W
    chibie_y = footer_top + (footer_h - target_h) // 2
    canvas.paste(ch, (chibie_x, chibie_y), ch)

    bubble_x0 = chibie_x + target_w + pad
    bubble_y0 = footer_top + pad
    bubble_x1 = pw - pad - BORDER_W
    bubble_y1 = ph + footer_h - pad
    if bubble_x1 - bubble_x0 < 120:
        bubble_x0 = pad
        bubble_x1 = pw - pad

    max_font = max(10, min(14, int((bubble_x1 - bubble_x0) / 28)))
    _draw_speech_bubble(
        draw,
        (bubble_x0, bubble_y0, bubble_x1, bubble_y1),
        comment,
        max_font=max_font,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)


def _estimate_debrief_height(
    width: int,
    comment: str,
    chibie_w: int,
    pad: int,
) -> int:
    """Hauteur minimale pour que le debrief tienne dans la bulle."""
    bubble_x0 = pad + BORDER_W + chibie_w + pad
    bubble_x1 = width - pad - BORDER_W
    inner_w = max(60, bubble_x1 - bubble_x0)
    inner_h = 400
    max_font = max(10, min(15, int(inner_w / 26)))
    _, _, _, bubble_h = _layout_comment(
        comment, inner_w, inner_h, max_font=max_font
    )
    return max(150, int(width * 0.16), bubble_h + pad * 2 + BORDER_W * 2)


def render_debrief_page(
    output_path: Path,
    *,
    width: int,
    mood: str,
    comment: str,
) -> None:
    """Page PDF finale dédiée au debrief de Toa (sans titre)."""
    pad = 16
    chibie_path = _resolve_chibie_image(mood)
    probe_h = min(CHIBIE_MAX_HEIGHT + 8, 120)
    with Image.open(chibie_path) as ch_src:
        ch_w = max(1, int(ch_src.width * probe_h / max(ch_src.height, 1)))
    footer_h = _estimate_debrief_height(width, comment, ch_w, pad)
    canvas_h = footer_h
    canvas = Image.new("RGB", (width, canvas_h), COFFEE_BG)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(
        (0, 0, width - 1, canvas_h - 1),
        outline=COFFEE_BORDER,
        width=BORDER_W,
    )

    chibie_path = _resolve_chibie_image(mood)
    target_h = min(CHIBIE_MAX_HEIGHT + 8, footer_h - pad * 2, 120)
    with Image.open(chibie_path) as ch_src:
        ch = ch_src.convert("RGBA")
    scale = target_h / max(ch.height, 1)
    target_w = max(1, int(ch.width * scale))
    ch = ch.resize((target_w, target_h), Image.Resampling.LANCZOS)

    footer_top = 0
    chibie_x = pad + BORDER_W
    chibie_y = footer_top + (footer_h - target_h) // 2
    canvas.paste(ch, (chibie_x, chibie_y), ch)

    bubble_x0 = chibie_x + target_w + pad
    bubble_y0 = footer_top + pad
    bubble_x1 = width - pad - BORDER_W
    bubble_y1 = canvas_h - pad
    max_font = max(10, min(15, int((bubble_x1 - bubble_x0) / 26)))
    _draw_speech_bubble(
        draw,
        (bubble_x0, bubble_y0, bubble_x1, bubble_y1),
        comment,
        max_font=max_font,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)
