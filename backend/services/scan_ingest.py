"""Normalisation des scans uploadés (PNG, JPEG → pages PNG)."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

SUPPORTED_UPLOAD_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})


def _image_to_png(src: Path, dest: Path) -> None:
    with Image.open(src) as img:
        img.convert("RGB").save(dest, format="PNG", optimize=True)


def normalize_upload_dir(upload_dir: Path) -> list[Path]:
    """Convertit raw_* et fichiers restants en page_0000.png, page_0001.png, …"""
    upload_dir.mkdir(parents=True, exist_ok=True)

    existing_pages = sorted(upload_dir.glob("page_*.png"))

    sources = sorted(
        p
        for p in upload_dir.iterdir()
        if p.is_file()
        and (
            p.name.startswith("raw_")
            or (
                p.suffix.lower() in SUPPORTED_UPLOAD_SUFFIXES
                and not p.name.startswith("page_")
            )
        )
    )

    if not sources:
        return existing_pages

    for old in existing_pages:
        old.unlink(missing_ok=True)

    page_paths: list[Path] = []
    for page_idx, src in enumerate(sources):
        suffix = src.suffix.lower()
        dest = upload_dir / f"page_{page_idx:04d}.png"
        if suffix == ".png":
            if src.resolve() != dest.resolve():
                src.replace(dest)
        else:
            _image_to_png(src, dest)
            src.unlink(missing_ok=True)
        page_paths.append(dest)

    return page_paths


def list_page_images(upload_dir: Path) -> list[Path]:
    """Pages prêtes pour le pipeline (sans effacer une conversion déjà faite)."""
    existing = sorted(upload_dir.glob("page_*.png"))
    if existing:
        return existing
    return normalize_upload_dir(upload_dir)
