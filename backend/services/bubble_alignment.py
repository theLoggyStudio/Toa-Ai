"""Aligne les traductions sur les coordonnées détectées localement (bulles + onomatopées)."""

from __future__ import annotations

import math
import re
from pathlib import Path

from models import BoundingBox, TextBlock
from services.ocr import run_ocr_on_page


def _center(bb: BoundingBox) -> tuple[float, float]:
    return ((bb.x_min + bb.x_max) / 2.0, (bb.y_min + bb.y_max) / 2.0)


def _iou(a: BoundingBox, b: BoundingBox) -> float:
    x1 = max(a.x_min, b.x_min)
    y1 = max(a.y_min, b.y_min)
    x2 = min(a.x_max, b.x_max)
    y2 = min(a.y_max, b.y_max)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if inter <= 0:
        return 0.0
    area_a = max(1, (a.x_max - a.x_min) * (a.y_max - a.y_min))
    area_b = max(1, (b.x_max - b.x_min) * (b.y_max - b.y_min))
    return inter / (area_a + area_b - inter)


def _normalize_text(text: str) -> str:
    t = re.sub(r"\s+", "", (text or "").strip())
    return re.sub(r"[\u3099\u309A\u309B\u309C]", "", t)


def _text_similarity(a: str, b: str) -> float:
    na, nb = _normalize_text(a), _normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.75
    overlap = len(set(na) & set(nb))
    return overlap / max(len(set(na)), len(set(nb)), 1)


def _match_score(
    translated: TextBlock,
    detected: TextBlock,
    *,
    img_w: int,
    img_h: int,
) -> float:
    iou = _iou(translated.boundingBox, detected.boundingBox)
    tcx, tcy = _center(translated.boundingBox)
    dcx, dcy = _center(detected.boundingBox)
    dist = math.hypot(tcx - dcx, tcy - dcy)
    max_dist = max(img_w, img_h) * 0.22
    dist_score = max(0.0, 1.0 - dist / max_dist)
    text_score = _text_similarity(
        translated.originalText, detected.originalText
    )
    return iou * 4.0 + dist_score * 2.5 + text_score * 2.0


def _pad_bbox(bb: BoundingBox, *, pad: int = 6) -> BoundingBox:
    return BoundingBox(
        x_min=bb.x_min - pad,
        y_min=bb.y_min - pad,
        x_max=bb.x_max + pad,
        y_max=bb.y_max + pad,
    )


def _ensure_white_bg_tag(translated_text: str) -> str:
    txt = translated_text or ""
    txt = txt.replace("[[BG:TRANSPARENT]]", "")
    if "[[BG:SOLID]]" not in txt:
        txt = f"[[BG:SOLID]]{txt}"
    return txt


def align_blocks_to_page_detections(
    image_path: Path,
    translated_blocks: list[TextBlock],
    source_language: str,
    page_index: int,
) -> list[TextBlock]:
    """
    Remplace les bbox vision par les zones détectées sur l'image (pixels réels).
    Dialogues et onomatopées inclus.
    """
    if not translated_blocks:
        return translated_blocks

    try:
        from PIL import Image

        with Image.open(image_path) as img:
            img_w, img_h = img.size
    except Exception:
        img_w, img_h = 10_000, 10_000

    detected = run_ocr_on_page(image_path, source_language, page_index)
    if not detected:
        return [
            b.model_copy(
                update={
                    "boundingBox": _pad_bbox(b.boundingBox),
                    "translatedText": _ensure_white_bg_tag(b.translatedText),
                }
            )
            for b in translated_blocks
        ]

    used: set[int] = set()
    ordered_translated = sorted(
        translated_blocks,
        key=lambda b: (b.boundingBox.y_min, b.boundingBox.x_min),
    )
    aligned: list[TextBlock] = []

    for tblock in ordered_translated:
        best_idx = -1
        best_score = 0.35
        for idx, dblock in enumerate(detected):
            if idx in used:
                continue
            score = _match_score(tblock, dblock, img_w=img_w, img_h=img_h)
            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx >= 0:
            used.add(best_idx)
            dblock = detected[best_idx]
            aligned.append(
                tblock.model_copy(
                    update={
                        "boundingBox": _pad_bbox(dblock.boundingBox),
                        "originalText": dblock.originalText or tblock.originalText,
                        "translatedText": _ensure_white_bg_tag(tblock.translatedText),
                    }
                )
            )
        else:
            aligned.append(
                tblock.model_copy(
                    update={
                        "boundingBox": _pad_bbox(tblock.boundingBox),
                        "translatedText": _ensure_white_bg_tag(tblock.translatedText),
                    }
                )
            )

    return sorted(aligned, key=lambda b: (b.boundingBox.y_min, b.boundingBox.x_min))
