"""File d'attente de pipelines : un pool de workers borné au lieu d'un thread par tâche."""

import logging
import queue
import threading

from config import PIPELINE_MAX_CONCURRENT_TASKS

logger = logging.getLogger(__name__)

_task_queue: "queue.Queue[tuple[str, str, str]]" = queue.Queue()
_workers_started = False
_workers_lock = threading.Lock()


def _worker_loop(worker_id: int) -> None:
    from services.pipeline import run_translation_pipeline

    while True:
        task_id, source_lang, target_lang = _task_queue.get()
        try:
            logger.info("Worker %s : pipeline démarré pour %s", worker_id, task_id)
            run_translation_pipeline(task_id, source_lang, target_lang)
        except Exception:
            logger.exception(
                "Worker %s : erreur inattendue pour %s", worker_id, task_id
            )
        finally:
            _task_queue.task_done()


def _ensure_workers() -> None:
    global _workers_started
    with _workers_lock:
        if _workers_started:
            return
        for i in range(PIPELINE_MAX_CONCURRENT_TASKS):
            thread = threading.Thread(
                target=_worker_loop,
                args=(i,),
                name=f"pipeline-worker-{i}",
                daemon=True,
            )
            thread.start()
        _workers_started = True
        logger.info(
            "%s worker(s) de pipeline démarré(s)", PIPELINE_MAX_CONCURRENT_TASKS
        )


def schedule_pipeline(task_id: str, source_lang: str, target_lang: str) -> None:
    from services.storage import update_task

    _ensure_workers()
    waiting = _task_queue.qsize()
    if waiting > 0:
        update_task(
            task_id,
            progressMessage=f"En file d'attente ({waiting} tâche(s) devant)…",
        )
    _task_queue.put((task_id, source_lang, target_lang))
