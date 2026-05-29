"""Composition visuelle du bandeau Chibie sous chaque page."""

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
        f"Aucun sprite Chibie d'état trouvé dans {CHIBIE_ASSETS_DIR}"
    )


def _wrap_comment(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
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


def _draw_speech_bubble(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont,
) -> None:
    x0, y0, x1, y1 = box
    max_w = max(60, x1 - x0 - 16)
    lines = _wrap_comment(text, font, max_w)
    line_heights = [
        font.getbbox(line)[3] - font.getbbox(line)[1] for line in lines
    ]
    total_h = sum(line_heights) + max(0, len(lines) - 1) * 3
    text_w = max((font.getbbox(line)[2] - font.getbbox(line)[0]) for line in lines)
    pad_x, pad_y = 10, 8
    bubble_w = min(x1 - x0, text_w + pad_x * 2)
    bubble_h = min(y1 - y0, total_h + pad_y * 2)
    bx0 = x0 + max(0, (x1 - x0 - bubble_w) // 2)
    by0 = y0 + max(0, (y1 - y0 - bubble_h) // 2)
    bx1, by1 = bx0 + bubble_w, by0 + bubble_h
    draw.rounded_rectangle(
        (bx0, by0, bx1, by1),
        radius=10,
        fill=COFFEE_BUBBLE,
        outline=COFFEE_BORDER,
        width=BORDER_W,
    )
    y = by0 + pad_y
    for line, lh in zip(lines, line_heights):
        lw = font.getbbox(line)[2] - font.getbbox(line)[0]
        tx = bx0 + pad_x + max(0, (bubble_w - pad_x * 2 - lw) // 2)
        draw.text((tx, y), line, fill=(35, 28, 22), font=font)
        y += lh + 3


def append_chibie_footer(
    page_image_path: Path,
    output_path: Path,
    *,
    mood: str,
    comment: str,
) -> None:
    """Ajoute le bandeau Chibie sous la page scan."""
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

    font_size = max(10, min(14, int((bubble_x1 - bubble_x0) / 28)))
    font = _load_font(font_size)
    _draw_speech_bubble(
        draw,
        (bubble_x0, bubble_y0, bubble_x1, bubble_y1),
        comment,
        font,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)


def render_debrief_page(
    output_path: Path,
    *,
    width: int,
    mood: str,
    comment: str,
) -> None:
    """Page PDF finale dédiée au debrief Chibie (sans titre)."""
    footer_h = max(150, int(width * 0.16))
    canvas_h = footer_h
    canvas = Image.new("RGB", (width, canvas_h), COFFEE_BG)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(
        (0, 0, width - 1, canvas_h - 1),
        outline=COFFEE_BORDER,
        width=BORDER_W,
    )

    pad = 16
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
    font_size = max(10, min(15, int((bubble_x1 - bubble_x0) / 26)))
    _draw_speech_bubble(
        draw,
        (bubble_x0, bubble_y0, bubble_x1, bubble_y1),
        comment,
        _load_font(font_size),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG", optimize=True)
