"""Exécute le pipeline IA dans un thread séparé pour ne pas bloquer l'API."""

import logging
import threading

logger = logging.getLogger(__name__)


def schedule_pipeline(task_id: str, source_lang: str, target_lang: str) -> None:
    from services.pipeline import run_translation_pipeline

    def _run() -> None:
        logger.info("Pipeline démarré pour %s", task_id)
        run_translation_pipeline(task_id, source_lang, target_lang)

    thread = threading.Thread(
        target=_run,
        name=f"pipeline-{task_id[:8]}",
        daemon=True,
    )
    thread.start()
