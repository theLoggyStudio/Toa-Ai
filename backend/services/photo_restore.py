"""Restauration photo Éclat (OpenCV) : contraste, denoise, saturation, netteté."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def restore_photo(input_path: Path, output_path: Path) -> Path:
    """Améliore une image dégradée et écrit le résultat PNG/JPEG selon le suffixe."""
    bgr = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"Impossible de lire l'image : {input_path}")

    # Denoise léger (préserve les détails)
    den = cv2.fastNlMeansDenoisingColored(bgr, None, 6, 6, 7, 21)

    # CLAHE sur canal L (contraste local)
    lab = cv2.cvtColor(den, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    l2 = clahe.apply(l)
    lab2 = cv2.merge([l2, a, b])
    enhanced = cv2.cvtColor(lab2, cv2.COLOR_LAB2BGR)

    # Saturation légèrement boostée
    hsv = cv2.cvtColor(enhanced, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.18, 0, 255)
    enhanced = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    # Unsharp mask
    blur = cv2.GaussianBlur(enhanced, (0, 0), 1.2)
    sharp = cv2.addWeighted(enhanced, 1.45, blur, -0.45, 0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = output_path.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        cv2.imwrite(str(output_path), sharp, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    else:
        cv2.imwrite(str(output_path), sharp)

    return output_path


def image_dimensions(path: Path) -> tuple[int, int]:
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"Impossible de lire l'image : {path}")
    h, w = img.shape[:2]
    return int(w), int(h)
