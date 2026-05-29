"""Pipeline IA complet : OCR → Traduction → Inpainting → PDF."""

import logging
from pathlib import Path

from PIL import Image

from config import OUTPUT_DIR, PRICE_PER_BUBBLE_CFA, is_ocr_fast_mode
from languages import normalize_lang_code, resolve_ocr_language
from models import TextBlock
from services import rendering, translation
from services.bubble_alignment import align_blocks_to_page_detections
from services.translation import reset_translator_probe
from services.chibie_commentary import (
    build_page_digest,
    generate_debrief_commentary,
    generate_page_commentary,
)
from services.chibie_scan_research import (
    ChibieScanContext,
    research_initial_scan_context,
    research_page_from_blocks,
)
from services.chibie_panel import append_chibie_footer, render_debrief_page
from services.pdf_compiler import compile_pdf
from services.storage import get_upload_dir, update_task
from services.transformation_report import (
    append_page,
    build_page_entry,
    init_report,
    log_disk_report,
)

logger = logging.getLogger(__name__)

# Réactions Toa (bandeau + debrief) uniquement à partir de ce nombre de pages.
MIN_PAGES_FOR_TOA = 3


def _set_progress(task_id: str, percent: int, message: str) -> None:
    update_task(
        task_id,
        progressPercent=min(99, max(0, percent)),
        progressMessage=message,
    )


def run_translation_pipeline(
    task_id: str,
    source_language: str,
    target_language: str,
) -> None:
    try:
        _run_pipeline(task_id, source_language, target_language)
    except Exception as exc:
        logger.exception("Pipeline échoué pour %s", task_id)
        update_task(
            task_id,
            status="failed",
            progressPercent=0,
            progressMessage=None,
            errorMessage=str(exc)[:500],
        )


def _run_pipeline(
    task_id: str,
    source_language: str,
    target_language: str,
) -> None:
    upload_dir = get_upload_dir(task_id)
    processed_dir = OUTPUT_DIR / task_id / "processed"
    if processed_dir.exists():
        for old in processed_dir.iterdir():
            if old.is_file():
                old.unlink()
    processed_dir.mkdir(parents=True, exist_ok=True)

    pdf_path_existing = OUTPUT_DIR / f"{task_id}.pdf"
    if pdf_path_existing.exists():
        pdf_path_existing.unlink()

    image_paths = sorted(
        p
        for p in upload_dir.iterdir()
        if p.suffix.lower() in {".png", ".jpg", ".jpeg"}
    )
    total = len(image_paths)
    if total == 0:
        raise ValueError("Aucune image à traiter.")

    _set_progress(task_id, 5, "Préparation…")
    init_report(task_id, source_language, target_language)
    reset_translator_probe()
    if is_ocr_fast_mode():
        raise RuntimeError(
            "Mode test OCR actif. Désactivez OCR_FAST_MODE dans backend/.env "
            "puis redémarrez le serveur."
        )
    if not translation.is_translator_available():
        raise RuntimeError(
            "Service de traduction indisponible. Vérifiez la configuration "
            "puis redémarrez le serveur."
        )
    _set_progress(task_id, 8, "Préparation terminée")

    include_toa = total >= MIN_PAGES_FOR_TOA
    chibie_scan_ctx = ChibieScanContext()
    if include_toa:
        _set_progress(task_id, 9, "Toa repère la série sur les scans…")
        chibie_scan_ctx = research_initial_scan_context(
            image_paths,
            session_id=task_id,
            max_pages=min(3, total),
        )

    processed_images: list[Path] = []
    total_translated_bubbles = 0
    effective_source = normalize_lang_code(source_language)
    story_so_far: list[str] = []
    page_width = 0

    for page_idx, image_path in enumerate(image_paths):
        page_num = page_idx + 1
        base_pct = 10 + int((page_idx / total) * 75)

        _set_progress(
            task_id,
            base_pct,
            f"Page {page_num}/{total} — analyse visuelle…",
        )
        blocks, detected_lang = translation.detect_and_translate_full_page_with_cursor(
            image_path,
            source_language,
            target_language,
            session_id=task_id,
            page_index=page_idx,
        )
        if source_language == "auto" and detected_lang:
            effective_source = detected_lang
            update_task(task_id, sourceLanguage=effective_source)
        ocr_lang = resolve_ocr_language(source_language, effective_source)
        _set_progress(
            task_id,
            base_pct + 2,
            f"Page {page_num}/{total} — repérage des zones texte…",
        )
        blocks = align_blocks_to_page_detections(
            image_path,
            blocks,
            ocr_lang,
            page_idx,
        )
        total_translated_bubbles += len(blocks)
        _set_progress(
            task_id,
            base_pct + 5,
            f"Page {page_num}/{total} — traduction en cours…",
        )

        page_entry = build_page_entry(image_path, page_idx, blocks)
        append_page(
            task_id,
            source_language=source_language,
            target_language=target_language,
            page_entry=page_entry,
        )
        log_disk_report(task_id, page_entry)

        _set_progress(
            task_id,
            base_pct + 12,
            f"Page {page_num}/{total} — superposition du texte…",
        )
        out_path = processed_dir / f"page_{page_idx:04d}.png"
        rendering.inpaint_and_render(
            image_path, blocks, out_path, target_language
        )

        if include_toa:
            chibie_scan_ctx = research_page_from_blocks(
                chibie_scan_ctx,
                page_index=page_idx,
                blocks=blocks,
                session_id=task_id,
            )
            _set_progress(
                task_id,
                base_pct + 14,
                f"Page {page_num}/{total} — avis de Toa…",
            )
            mood, chibie_comment = generate_page_commentary(
                page_index=page_idx,
                total_pages=total,
                blocks=blocks,
                story_so_far=story_so_far,
                target_language=target_language,
                session_id=task_id,
                scan_context=chibie_scan_ctx,
            )
            story_so_far.append(build_page_digest(page_idx, blocks))

            final_page_path = processed_dir / f"page_{page_idx:04d}_final.png"
            append_chibie_footer(
                out_path,
                final_page_path,
                mood=mood,
                comment=chibie_comment,
            )
            with Image.open(final_page_path) as fin:
                page_width = max(page_width, fin.size[0])
            processed_images.append(final_page_path)
        else:
            with Image.open(out_path) as fin:
                page_width = max(page_width, fin.size[0])
            processed_images.append(out_path)

    if include_toa:
        _set_progress(task_id, 90, "Debrief de Toa…")
        debrief_mood, debrief_text = generate_debrief_commentary(
            story_so_far=story_so_far,
            target_language=target_language,
            session_id=task_id,
            scan_context=chibie_scan_ctx,
        )
        debrief_path = processed_dir / "page_toa_debrief.png"
        render_debrief_page(
            debrief_path,
            width=page_width or 900,
            mood=debrief_mood,
            comment=debrief_text,
        )
        processed_images.append(debrief_path)

    _set_progress(task_id, 92, "Génération du PDF…")
    pdf_path = OUTPUT_DIR / f"{task_id}.pdf"
    compile_pdf(processed_images, pdf_path)

    update_task(
        task_id,
        status="completed",
        amountCFA=total_translated_bubbles * PRICE_PER_BUBBLE_CFA,
        billableBubblesCount=total_translated_bubbles,
        progressPercent=100,
        progressMessage="Terminé",
        errorMessage=None,
        pdfUrl=f"/api/tasks/{task_id}/pdf",
    )
    logger.info("Pipeline terminé pour %s", task_id)
