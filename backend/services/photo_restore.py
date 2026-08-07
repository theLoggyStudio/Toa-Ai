"""Restauration photo Fresco (OpenCV) selon options : tears / color / hd."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from config import normalize_restore_options


def _fix_tears_and_damage(bgr: np.ndarray) -> np.ndarray:
    """Corrige l'image et tente de combler rayures / déchirures."""
    den = cv2.fastNlMeansDenoisingColored(bgr, None, 6, 6, 7, 21)

    gray = cv2.cvtColor(den, cv2.COLOR_BGR2GRAY)
    # Zones sombres fines (rayures / plis) + contraste local faible
    dark = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 12
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(dark, cv2.MORPH_OPEN, kernel, iterations=1)
    # Limiter l'inpainting aux traits fins (pas les grandes ombres)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    # Écarter les grandes régions (seuillage par surface)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    clean = np.zeros_like(mask)
    h, w = gray.shape
    max_area = max(40, int(h * w * 0.01))
    for i in range(1, num):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if 8 <= area <= max_area:
            clean[labels == i] = 255

    if clean.any():
        den = cv2.inpaint(den, clean, 3, cv2.INPAINT_TELEA)

    lab = cv2.cvtColor(den, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    lab2 = cv2.merge([clahe.apply(l), a, b])
    return cv2.cvtColor(lab2, cv2.COLOR_LAB2BGR)


def _enhance_color(bgr: np.ndarray) -> np.ndarray:
    """Restaure / booste les couleurs (y compris photos très fades)."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    sat = hsv[:, :, 1]
    mean_sat = float(sat.mean())
    # Photo quasi N&B → saturation plus agressive
    boost = 1.55 if mean_sat < 25 else 1.28
    hsv[:, :, 1] = np.clip(sat * boost, 0, 255)
    # Légère chaleur
    hsv[:, :, 0] = (hsv[:, :, 0] + 2) % 180
    out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    return out


def _render_hd(bgr: np.ndarray) -> np.ndarray:
    """Upscale ×2 + netteté pour un rendu HD."""
    h, w = bgr.shape[:2]
    # Plafond raisonnable pour éviter les images énormes
    max_side = 4096
    scale = 2.0
    if max(h, w) * scale > max_side:
        scale = max_side / float(max(h, w))
    if scale > 1.01:
        bgr = cv2.resize(
            bgr,
            (int(round(w * scale)), int(round(h * scale))),
            interpolation=cv2.INTER_LANCZOS4,
        )
    blur = cv2.GaussianBlur(bgr, (0, 0), 1.15)
    return cv2.addWeighted(bgr, 1.5, blur, -0.5, 0)


def restore_photo(
    input_path: Path,
    output_path: Path,
    options: list[str] | None = None,
) -> Path:
    """Applique les options choisies (tears / color / hd) puis écrit le fichier."""
    bgr = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"Impossible de lire l'image : {input_path}")

    opts = set(normalize_restore_options(options))
    out = bgr

    if "tears" in opts:
        out = _fix_tears_and_damage(out)
    if "color" in opts:
        out = _enhance_color(out)
    if "hd" in opts:
        out = _render_hd(out)

    # Si seules color/hd : léger denoise de base pour éviter le bruit
    if "tears" not in opts:
        out = cv2.fastNlMeansDenoisingColored(out, None, 3, 3, 7, 21)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = output_path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        cv2.imwrite(str(output_path), out, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    else:
        cv2.imwrite(str(output_path), out)

    return output_path


def image_dimensions(path: Path) -> tuple[int, int]:
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Impossible de lire l'image : {path}")
    h, w = img.shape[:2]
    return int(w), int(h)
