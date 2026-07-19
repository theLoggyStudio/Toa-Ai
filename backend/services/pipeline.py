"""Pipeline IA : vision Cursor par lots → pages en parallèle → PDF partiels → fusion.

Robustesse :
- retry par page puis repli sur la page originale non traduite (jamais d'échec global
  pour une seule page) ;
- checkpoint JSON par page : une reprise (retry de tâche) ne repaye pas les pages déjà faites ;
- PDF partiel régénéré après chaque lot, téléchargeable pendant le traitement.
"""

import json
import logging
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from config import (
    BATCH_PAGE_SIZE,
    CHIBIE_ENABLED,
    CURSOR_PAGE_DELAY_SEC,
    OUTPUT_DIR,
    PAGE_TRANSLATION_ATTEMPTS,
    PIPELINE_PAGE_CONCURRENCY,
    amount_cfa_for_bubbles,
)
from models import TextBlock
from services import translation
from services.chibie_commentary import (
    build_page_digest,
    generate_debrief_commentary,
    generate_page_context_and_commentary,
)
from services.chibie_scan_research import (
    ChibieScanContext,
    research_initial_scan_context,
)
from services.chibie_panel import append_chibie_footer, render_debrief_page
from services.cleanup import purge_task_uploads
from services.html_bubble_render import (
    close_thread_browser,
    render_page_html_overlays,
)
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


# ---------------------------------------------------------------------------
# Checkpoint par page
# ---------------------------------------------------------------------------


def _checkpoint_path(task_id: str) -> Path:
    return OUTPUT_DIR / task_id / "checkpoint.json"


def _load_checkpoint(task_id: str) -> dict:
    path = _checkpoint_path(task_id)
    if not path.exists():
        return {"pages": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"pages": {}}


_checkpoint_lock = threading.Lock()


def _save_checkpoint_page(task_id: str, page_idx: int, entry: dict) -> None:
    with _checkpoint_lock:
        data = _load_checkpoint(task_id)
        data["pages"][str(page_idx)] = entry
        path = _checkpoint_path(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def clear_checkpoint(task_id: str) -> None:
    _checkpoint_path(task_id).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Traitement d'une page (traduction + rendu), avec retry et repli
# ---------------------------------------------------------------------------


class PageResult:
    __slots__ = (
        "page_idx",
        "blocks",
        "detected_lang",
        "output_path",
        "fallback_used",
        "from_checkpoint",
    )

    def __init__(
        self,
        page_idx: int,
        blocks: list[TextBlock],
        detected_lang: str | None,
        output_path: Path,
        fallback_used: bool = False,
        from_checkpoint: bool = False,
    ) -> None:
        self.page_idx = page_idx
        self.blocks = blocks
        self.detected_lang = detected_lang
        self.output_path = output_path
        self.fallback_used = fallback_used
        self.from_checkpoint = from_checkpoint


def _restore_page_from_checkpoint(
    task_id: str,
    page_idx: int,
    processed_dir: Path,
) -> PageResult | None:
    entry = _load_checkpoint(task_id)["pages"].get(str(page_idx))
    if not entry:
        return None
    out_path = processed_dir / f"page_{page_idx:04d}.png"
    if not out_path.exists():
        return None
    blocks = [TextBlock(**raw) for raw in entry.get("blocks", [])]
    return PageResult(
        page_idx=page_idx,
        blocks=blocks,
        detected_lang=entry.get("detectedLang"),
        output_path=out_path,
        fallback_used=bool(entry.get("fallbackUsed", False)),
        from_checkpoint=True,
    )


def _process_single_page(
    task_id: str,
    image_path: Path,
    page_idx: int,
    target_language: str,
    batch_session: str,
    processed_dir: Path,
) -> PageResult:
    """Traduit et compose une page. Ne lève jamais : repli sur la page originale."""
    restored = _restore_page_from_checkpoint(task_id, page_idx, processed_dir)
    if restored:
        logger.info("Page %s restaurée depuis le checkpoint", page_idx + 1)
        return restored

    out_path = processed_dir / f"page_{page_idx:04d}.png"
    last_error: Exception | None = None

    for attempt in range(PAGE_TRANSLATION_ATTEMPTS):
        try:
            blocks, detected_lang, page_css = (
                translation.detect_and_translate_full_page_with_cursor(
                    image_path,
                    "auto",
                    target_language,
                    session_id=f"{batch_session}-try{attempt}",
                    page_index=page_idx,
                )
            )
            render_page_html_overlays(
                image_path,
                blocks,
                out_path,
                page_css=page_css,
            )
            _save_checkpoint_page(
                task_id,
                page_idx,
                {
                    "blocks": [b.model_dump() for b in blocks],
                    "detectedLang": detected_lang,
                    "fallbackUsed": False,
                },
            )
            return PageResult(page_idx, blocks, detected_lang, out_path)
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Page %s : tentative %s/%s échouée (%s)",
                page_idx + 1,
                attempt + 1,
                PAGE_TRANSLATION_ATTEMPTS,
                exc,
            )
            if attempt < PAGE_TRANSLATION_ATTEMPTS - 1:
                time.sleep(2.0)

    # Repli : livrer la page originale plutôt que de faire échouer toute la tâche.
    logger.error(
        "Page %s : abandon après %s tentatives (%s) — page originale conservée",
        page_idx + 1,
        PAGE_TRANSLATION_ATTEMPTS,
        last_error,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(image_path, out_path)
    _save_checkpoint_page(
        task_id,
        page_idx,
        {"blocks": [], "detectedLang": None, "fallbackUsed": True},
    )
    return PageResult(page_idx, [], None, out_path, fallback_used=True)


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------


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
    finally:
        close_thread_browser()


def _partial_pdf_public_path(task_id: str) -> Path:
    return OUTPUT_DIR / f"{task_id}_partial.pdf"


def _publish_partial_pdf(task_id: str, partial_pdfs: list[Path]) -> None:
    """Fusionne les lots déjà finis en un PDF téléchargeable pendant le traitement."""
    if not partial_pdfs:
        return
    try:
        dest = _partial_pdf_public_path(task_id)
        merge_pdfs(partial_pdfs, dest)
        update_task(task_id, partialPdfUrl=f"/api/tasks/{task_id}/pdf/partial")
    except Exception as exc:
        logger.warning("PDF partiel indisponible pour %s: %s", task_id, exc)


def _run_pipeline(
    task_id: str,
    source_language: str,
    target_language: str,
) -> None:
    upload_dir = get_upload_dir(task_id)
    processed_dir = OUTPUT_DIR / task_id / "processed"
    processed_dir.mkdir(parents=True, exist_ok=True)
    has_checkpoint = bool(_load_checkpoint(task_id)["pages"])
    if not has_checkpoint:
        # Premier passage : repartir d'un dossier propre.
        for old in processed_dir.iterdir():
            if old.is_file():
                old.unlink()

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
    _set_progress(
        task_id,
        8,
        f"Préparation — {total} page(s), lots de {batch_size}, "
        f"{PIPELINE_PAGE_CONCURRENCY} page(s) en parallèle",
    )

    task_cfg = get_task(task_id)
    include_toa = (
        CHIBIE_ENABLED
        and (task_cfg.includeToa if task_cfg else True)
        and total >= MIN_PAGES_FOR_TOA
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
    fallback_pages: list[int] = []
    story_so_far: list[str] = []
    page_width = 0
    partial_pdfs: list[Path] = []
    pages_done = 0
    progress_lock = threading.Lock()

    for batch_idx, batch_paths in enumerate(batches):
        reset_translator_probe()
        batch_session = f"{task_id}-batch-{batch_idx:04d}-{secrets.token_hex(4)}"
        batch_start = batch_idx * batch_size

        _set_progress(
            task_id,
            10 + int((batch_idx / batch_count) * 72),
            f"Lot {batch_idx + 1}/{batch_count} — démarrage…",
        )
        logger.info(
            "Lot %s/%s pour %s (pages %s-%s)",
            batch_idx + 1,
            batch_count,
            task_id,
            batch_start + 1,
            batch_start + len(batch_paths),
        )

        # Phase 1 — traduction + rendu des pages du lot, en parallèle.
        def _run_page(local_idx: int, image_path: Path) -> PageResult:
            nonlocal pages_done
            # Étale légèrement les départs pour ne pas rafaler l'API Cursor.
            if local_idx > 0 and CURSOR_PAGE_DELAY_SEC > 0:
                time.sleep(CURSOR_PAGE_DELAY_SEC * local_idx)
            result = _process_single_page(
                task_id,
                image_path,
                batch_start + local_idx,
                target_language,
                batch_session,
                processed_dir,
            )
            with progress_lock:
                pages_done += 1
                done = pages_done
            _set_progress(
                task_id,
                10 + int((done / total) * 72),
                f"Page {done}/{total} traduite…",
            )
            return result

        max_workers = min(PIPELINE_PAGE_CONCURRENCY, len(batch_paths))
        if max_workers > 1:
            with ThreadPoolExecutor(
                max_workers=max_workers,
                thread_name_prefix=f"page-{task_id[:8]}",
            ) as pool:
                results = list(
                    pool.map(
                        _run_page,
                        range(len(batch_paths)),
                        batch_paths,
                    )
                )
        else:
            results = [
                _run_page(local_idx, path)
                for local_idx, path in enumerate(batch_paths)
            ]

        # Phase 2 — séquentiel dans l'ordre des pages : rapport, Toa, PDF.
        batch_images: list[Path] = []
        for local_idx, result in enumerate(results):
            global_page_idx = result.page_idx
            page_num = global_page_idx + 1
            image_path = batch_paths[local_idx]

            if result.detected_lang:
                update_task(task_id, sourceLanguage=result.detected_lang)
            total_translated_bubbles += len(result.blocks)
            if result.fallback_used:
                fallback_pages.append(page_num)

            page_entry = build_page_entry(
                image_path, global_page_idx, result.blocks
            )
            append_page(
                task_id,
                source_language=source_language,
                target_language=target_language,
                page_entry=page_entry,
            )
            log_disk_report(task_id, page_entry)

            page_for_pdf = result.output_path
            if include_toa and result.blocks:
                _set_progress(
                    task_id,
                    10 + int((page_num / total) * 72),
                    f"Page {page_num}/{total} — avis de Toa…",
                )
                # Un seul appel Cursor : contexte + commentaire de la page.
                chibie_scan_ctx, mood, chibie_comment = (
                    generate_page_context_and_commentary(
                        ctx=chibie_scan_ctx,
                        page_index=global_page_idx,
                        total_pages=total,
                        blocks=result.blocks,
                        story_so_far=story_so_far,
                        target_language=target_language,
                        session_id=task_id,
                    )
                )
                story_so_far.append(
                    build_page_digest(global_page_idx, result.blocks)
                )
                final_page_path = (
                    processed_dir / f"page_{global_page_idx:04d}_final.png"
                )
                append_chibie_footer(
                    result.output_path,
                    final_page_path,
                    mood=mood,
                    comment=chibie_comment,
                )
                page_for_pdf = final_page_path
            else:
                story_so_far.append(
                    build_page_digest(global_page_idx, result.blocks)
                )

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
        _publish_partial_pdf(task_id, partial_pdfs)

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

    warning = None
    if fallback_pages:
        pages_list = ", ".join(str(p) for p in fallback_pages[:10])
        warning = (
            f"{len(fallback_pages)} page(s) non traduite(s) "
            f"(conservée(s) en version originale) : {pages_list}"
        )

    update_task(
        task_id,
        status="completed",
        amountCFA=amount_cfa_for_bubbles(total_translated_bubbles),
        billableBubblesCount=total_translated_bubbles,
        progressPercent=100,
        progressMessage="Terminé" if not warning else f"Terminé — {warning}",
        errorMessage=None,
        pdfUrl=f"/api/tasks/{task_id}/pdf",
        partialPdfUrl=None,
    )
    _partial_pdf_public_path(task_id).unlink(missing_ok=True)
    clear_checkpoint(task_id)
    purge_task_uploads(task_id)
    logger.info(
        "Pipeline terminé pour %s (%s lots, %s pages, %s page(s) en repli)",
        task_id,
        batch_count,
        total,
        len(fallback_pages),
    )
