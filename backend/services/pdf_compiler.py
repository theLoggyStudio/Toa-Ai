"""Compilation PDF avec filigrane officiel Toa AI (Filigrame.png)."""

import logging
from io import BytesIO
from pathlib import Path
from typing import List

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
FILIGRAME_PATH = BASE_DIR.parent / "frontend" / "src" / "assets" / "Filigrame.png"
FILIGRAME_FALLBACK = BASE_DIR / "assets" / "Filigrame.png"

# Visible mais lisible (0.0 = invisible, 1.0 = opaque)
WATERMARK_OPACITY = 0.75
# Echelle du filigrane (1.0 = taille actuelle, 0.5 = moitie)
WATERMARK_SCALE = 0.5


def _resolve_filigrame_path() -> Path | None:
    for path in (FILIGRAME_PATH, FILIGRAME_FALLBACK):
        if path.exists():
            return path
    logger.warning("Filigrame.png introuvable — filigrane texte de secours")
    return None


def _text_watermark_layer(size: tuple[int, int]) -> Image.Image:
    from PIL import ImageDraw, ImageFont

    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    label = "Toa AI"
    font_size = max(12, int((min(size) // 12) * WATERMARK_SCALE))
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    padding = max(10, int(40 * WATERMARK_SCALE))
    tile = Image.new("RGBA", (tw + padding, th + padding), (0, 0, 0, 0))
    tile_draw = ImageDraw.Draw(tile)
    faint = int(255 * WATERMARK_OPACITY)
    offset = max(5, int(20 * WATERMARK_SCALE))
    tile_draw.text((offset, offset), label, fill=(74, 63, 53, faint), font=font)
    rotated = tile.rotate(35, expand=True, resample=Image.Resampling.NEAREST)
    for y in range(-rotated.height, size[1] + rotated.height, rotated.height // 2):
        for x in range(-rotated.width, size[0] + rotated.width, rotated.width // 2):
            layer.paste(rotated, (x, y), rotated)
    return layer


def _watermark_rgba_for_size(size: tuple[int, int], filigrame: Path) -> Image.Image:
    """Un seul filigrane centre (pas de couverture plein ecran)."""
    with Image.open(filigrame) as base_wm:
        wm = base_wm.convert("RGBA")
    iw, ih = size
    # Largeur cible ~35 % du cote court, puis facteur WATERMARK_SCALE (0.5 = moitie)
    target_w = max(48, int(min(iw, ih) * 0.35 * WATERMARK_SCALE))
    scale = target_w / max(wm.width, 1)
    nw = max(1, int(wm.width * scale))
    nh = max(1, int(wm.height * scale))
    wm = wm.resize((nw, nh), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", (iw, ih), (0, 0, 0, 0))
    left = (iw - nw) // 2
    top = (ih - nh) // 2
    layer.paste(wm, (left, top), wm)
    r, g, b, a = layer.split()
    a = a.point(lambda p: int(p * WATERMARK_OPACITY))
    return Image.merge("RGBA", (r, g, b, a))


def _apply_watermark(image_path: Path) -> Image.Image:
    with Image.open(image_path) as source_img:
        img = source_img.convert("RGBA")
    filigrame = _resolve_filigrame_path()

    if filigrame:
        wm = _watermark_rgba_for_size(img.size, filigrame)
        img = Image.alpha_composite(img, wm)
    else:
        img = Image.alpha_composite(img, _text_watermark_layer(img.size))

    return img.convert("RGB")


def compile_pdf(image_paths: List[Path], output_pdf: Path) -> None:
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output_pdf))

    for img_path in sorted(image_paths):
        watermarked = _apply_watermark(img_path)
        png_buffer: BytesIO | None = None
        try:
            png_buffer = BytesIO()
            watermarked.save(png_buffer, "PNG")
            png_buffer.seek(0)
            reader = ImageReader(png_buffer)
            iw, ih = watermarked.size
            # Une page PDF par image, sans bandes blanches (format natif)
            c.setPageSize((iw, ih))
            c.drawImage(reader, 0, 0, width=iw, height=ih)
            c.showPage()
        finally:
            watermarked.close()
            if png_buffer is not None:
                png_buffer.close()

    c.save()


def merge_pdfs(pdf_paths: List[Path], output_pdf: Path) -> None:
    """Fusionne des PDF partiels dans l'ordre."""
    from pypdf import PdfReader, PdfWriter

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for pdf_path in pdf_paths:
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF partiel introuvable: {pdf_path}")
        reader = PdfReader(str(pdf_path))
        for page in reader.pages:
            writer.add_page(page)
    with output_pdf.open("wb") as handle:
        writer.write(handle)
    logger.info("PDF fusionne: %s (%s parties)", output_pdf.name, len(pdf_paths))
