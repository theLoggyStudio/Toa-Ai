"""Pipeline IA : vision Cursor par lots de 5 pages → PDF partiels → fusion."""

import logging
import secrets
import time
from pathlib import Path

from PIL import Image

from config import (
    BATCH_PAGE_SIZE,
    CURSOR_PAGE_DELAY_SEC,
    OUTPUT_DIR,
    amount_cfa_for_bubbles,
)
from services import translation
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
from services.cleanup import purge_task_uploads
from services.html_bubble_render import render_page_html_overlays
from services.pdf_compiler import compile_pdf, merge_pdfs
from services.scan_ingest import list_page_images
from services.storage import get_task, get_upload_dir, update_task
from services.translation import reset_translator_probe
from services.transformation_report import (
    append_page,
    build_page_entry,
    init_report,
    log_disk_report,
)

logger = logging.getLogger(__name__)

# Réactions Toa (bandeau + debrief) uniquement à partir de ce nombre de pages.
MIN_PAGES_FOR_TOA = 3


def _safe_error_message(exc: BaseException, limit: int = 500) -> str:
    text = str(exc) or repr(exc)
    return text.encode("utf-8", errors="replace").decode("utf-8")[:limit]


def _set_progress(task_id: str, percent: int, message: str) -> None:
    update_task(
        task_id,
        progressPercent=min(99, max(0, percent)),
        progressMessage=message,
    )


def _chunk_paths(paths: list[Path], size: int) -> list[list[Path]]:
    if size <= 0:
        return [paths]
    return [paths[i : i + size] for i in range(0, len(paths), size)]


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
            errorMessage=_safe_error_message(exc),
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

    image_paths = list_page_images(upload_dir)
    total = len(image_paths)
    if total == 0:
        raise ValueError("Aucune page à traiter (PNG, JPG ou JPEG).")

    batch_size = max(1, BATCH_PAGE_SIZE)
    batches = _chunk_paths(image_paths, batch_size)
    batch_count = len(batches)

    _set_progress(task_id, 5, "Préparation…")
    init_report(task_id, source_language, target_language)
    reset_translator_probe()
    if not translation.is_translator_available():
        raise RuntimeError(
            "Service de traduction indisponible. Vérifiez CURSOR_API_KEY."
        )
    _set_progress(task_id, 8, f"Préparation — {total} page(s), lots de {batch_size}")

    task_cfg = get_task(task_id)
    include_toa = (
        (task_cfg.includeToa if task_cfg else True) and total >= MIN_PAGES_FOR_TOA
    )
    chibie_scan_ctx = ChibieScanContext()
    if include_toa:
        _set_progress(task_id, 9, "Toa repère la série sur les scans…")
        chibie_scan_ctx = research_initial_scan_context(
            image_paths,
            session_id=task_id,
            max_pages=min(3, total),
        )

    total_translated_bubbles = 0
    story_so_far: list[str] = []
    page_width = 0
    partial_pdfs: list[Path] = []

    for batch_idx, batch_paths in enumerate(batches):
        reset_translator_probe()
        batch_session = f"{task_id}-batch-{batch_idx:04d}-{secrets.token_hex(4)}"
        batch_images: list[Path] = []
        batch_start = batch_idx * batch_size

        _set_progress(
            task_id,
            10 + int((batch_idx / batch_count) * 72),
            f"Lot {batch_idx + 1}/{batch_count} — démarrage (session isolée)…",
        )
        logger.info(
            "Lot %s/%s pour %s (pages %s-%s)",
            batch_idx + 1,
            batch_count,
            task_id,
            batch_start + 1,
            batch_start + len(batch_paths),
        )

        for local_idx, image_path in enumerate(batch_paths):
            global_page_idx = batch_start + local_idx
            if local_idx > 0 and CURSOR_PAGE_DELAY_SEC > 0:
                time.sleep(CURSOR_PAGE_DELAY_SEC)

            page_num = global_page_idx + 1
            done_in_batch = local_idx + 1
            progress_base = 10 + int(
                ((batch_idx + (local_idx / max(1, len(batch_paths)))) / batch_count)
                * 72
            )

            _set_progress(
                task_id,
                progress_base,
                f"Lot {batch_idx + 1}/{batch_count} — page {done_in_batch}/{len(batch_paths)} "
                f"(page {page_num}/{total})…",
            )
            blocks, detected_lang, page_css = (
                translation.detect_and_translate_full_page_with_cursor(
                    image_path,
                    "auto",
                    target_language,
                    session_id=batch_session,
                    page_index=local_idx,
                )
            )
            if detected_lang:
                update_task(task_id, sourceLanguage=detected_lang)
            total_translated_bubbles += len(blocks)

            page_entry = build_page_entry(image_path, global_page_idx, blocks)
            append_page(
                task_id,
                source_language=source_language,
                target_language=target_language,
                page_entry=page_entry,
            )
            log_disk_report(task_id, page_entry)

            out_path = processed_dir / f"page_{global_page_idx:04d}.png"
            render_page_html_overlays(
                image_path,
                blocks,
                out_path,
                page_css=page_css,
            )

            page_for_pdf = out_path
            if include_toa:
                chibie_scan_ctx = research_page_from_blocks(
                    chibie_scan_ctx,
                    page_index=global_page_idx,
                    blocks=blocks,
                    session_id=task_id,
                )
                _set_progress(
                    task_id,
                    progress_base + 2,
                    f"Page {page_num}/{total} — avis de Toa…",
                )
                mood, chibie_comment = generate_page_commentary(
                    page_index=global_page_idx,
                    total_pages=total,
                    blocks=blocks,
                    story_so_far=story_so_far,
                    target_language=target_language,
                    session_id=task_id,
                    scan_context=chibie_scan_ctx,
                )
                story_so_far.append(build_page_digest(global_page_idx, blocks))
                final_page_path = processed_dir / f"page_{global_page_idx:04d}_final.png"
                append_chibie_footer(
                    out_path,
                    final_page_path,
                    mood=mood,
                    comment=chibie_comment,
                )
                page_for_pdf = final_page_path
            else:
                story_so_far.append(build_page_digest(global_page_idx, blocks))

            with Image.open(page_for_pdf) as fin:
                page_width = max(page_width, fin.size[0])
            batch_images.append(page_for_pdf)

        partial_pdf = processed_dir / f"batch_{batch_idx:04d}.pdf"
        _set_progress(
            task_id,
            10 + int(((batch_idx + 0.9) / batch_count) * 72),
            f"Lot {batch_idx + 1}/{batch_count} — génération PDF partiel…",
        )
        compile_pdf(batch_images, partial_pdf)
        partial_pdfs.append(partial_pdf)

    pdfs_to_merge = list(partial_pdfs)

    if include_toa:
        _set_progress(task_id, 88, "Debrief de Toa…")
        debrief_session = f"{task_id}-toa-debrief-{secrets.token_hex(4)}"
        reset_translator_probe()
        debrief_mood, debrief_text = generate_debrief_commentary(
            story_so_far=story_so_far,
            target_language=target_language,
            session_id=debrief_session,
            scan_context=chibie_scan_ctx,
        )
        debrief_path = processed_dir / "page_toa_debrief.png"
        render_debrief_page(
            debrief_path,
            width=page_width or 900,
            mood=debrief_mood,
            comment=debrief_text,
        )
        toa_pdf = processed_dir / "batch_toa_debrief.pdf"
        compile_pdf([debrief_path], toa_pdf)
        pdfs_to_merge.append(toa_pdf)

    _set_progress(task_id, 92, "Fusion des PDF dans l'ordre…")
    final_pdf = OUTPUT_DIR / f"{task_id}.pdf"
    merge_pdfs(pdfs_to_merge, final_pdf)

    task_final = get_task(task_id)
    billed_bubbles = (
        task_final.billableBubblesCount
        if task_final and task_final.billableBubblesCount > 0
        else total_translated_bubbles
    )
    update_task(
        task_id,
        status="completed",
        amountCFA=amount_cfa_for_bubbles(billed_bubbles),
        progressPercent=100,
        progressMessage="Terminé",
        errorMessage=None,
        pdfUrl=f"/api/tasks/{task_id}/pdf",
    )
    purge_task_uploads(task_id)
    logger.info(
        "Pipeline terminé pour %s (%s lots, %s pages)",
        task_id,
        batch_count,
        total,
    )
