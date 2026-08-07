import json
import os
import threading
import uuid
from pathlib import Path
from typing import Optional

from config import OUTPUT_DIR, UPLOAD_DIR
from models import TranslationTask

TASKS_FILE = OUTPUT_DIR / "tasks.json"

# Protège les cycles lecture-modification-écriture de tasks.json :
# le thread pipeline, le webhook PayDunya et le polling écrivent en concurrence.
_TASKS_LOCK = threading.RLock()


def _load_tasks() -> dict[str, dict]:
    with _TASKS_LOCK:
        if not TASKS_FILE.exists():
            return {}
        return json.loads(TASKS_FILE.read_text(encoding="utf-8"))


def _save_tasks(tasks: dict[str, dict]) -> None:
    with _TASKS_LOCK:
        TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(tasks, indent=2)
        tmp = TASKS_FILE.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, TASKS_FILE)


def _task_from_raw(raw: dict) -> TranslationTask:
    return TranslationTask(
        id=raw["id"],
        originalImagesCount=raw["originalImagesCount"],
        sourceLanguage=raw["sourceLanguage"],
        targetLanguage=raw["targetLanguage"],
        status=raw["status"],
        amountCFA=raw["amountCFA"],
        billableBubblesCount=int(raw.get("billableBubblesCount", 0)),
        includeToa=bool(raw.get("includeToa", True)),
        kind=raw.get("kind") or "translate",
        imageWidth=raw.get("imageWidth"),
        imageHeight=raw.get("imageHeight"),
        restoredImageUrl=raw.get("restoredImageUrl"),
        payduniaToken=raw.get("payduniaToken"),
        pdfUrl=raw.get("pdfUrl"),
        partialPdfUrl=raw.get("partialPdfUrl"),
        progressPercent=int(raw.get("progressPercent", 0)),
        progressMessage=raw.get("progressMessage"),
        errorMessage=raw.get("errorMessage"),
    )


def create_task(
    image_count: int,
    source_language: str,
    target_language: str,
    amount_cfa: int,
    billable_bubbles_count: int = 0,
    include_toa: bool = True,
    kind: str = "translate",
    image_width: int | None = None,
    image_height: int | None = None,
) -> TranslationTask:
    task_id = str(uuid.uuid4())
    task = TranslationTask(
        id=task_id,
        originalImagesCount=image_count,
        sourceLanguage=source_language,  # type: ignore[arg-type]
        targetLanguage=target_language,  # type: ignore[arg-type]
        status="pending_payment",
        amountCFA=amount_cfa,
        billableBubblesCount=billable_bubbles_count,
        includeToa=include_toa,
        kind=kind,  # type: ignore[arg-type]
        imageWidth=image_width,
        imageHeight=image_height,
    )
    with _TASKS_LOCK:
        tasks = _load_tasks()
        tasks[task_id] = task.model_dump()
        tasks[task_id]["upload_dir"] = str(UPLOAD_DIR / task_id)
        _save_tasks(tasks)
    (UPLOAD_DIR / task_id).mkdir(parents=True, exist_ok=True)
    return task


def get_task(task_id: str) -> Optional[TranslationTask]:
    tasks = _load_tasks()
    raw = tasks.get(task_id)
    if not raw:
        return None
    return _task_from_raw(raw)


def update_task(task_id: str, **fields: object) -> Optional[TranslationTask]:
    with _TASKS_LOCK:
        tasks = _load_tasks()
        if task_id not in tasks:
            return None
        tasks[task_id].update(fields)
        _save_tasks(tasks)
    return get_task(task_id)


def get_upload_dir(task_id: str) -> Path:
    return UPLOAD_DIR / task_id


def get_output_pdf(task_id: str) -> Optional[Path]:
    pdf = OUTPUT_DIR / f"{task_id}.pdf"
    return pdf if pdf.exists() else None


def get_restored_image_path(task_id: str) -> Optional[Path]:
    for name in (f"{task_id}_restored.png", f"{task_id}_restored.jpg"):
        path = OUTPUT_DIR / name
        if path.exists():
            return path
    return None


def recover_interrupted_tasks() -> int:
    """Remet en failed les taches restees en processing apres un redemarrage."""
    with _TASKS_LOCK:
        tasks = _load_tasks()
        recovered = 0
        for task_id, raw in tasks.items():
            if raw.get("status") != "processing":
                continue
            tasks[task_id].update(
                {
                    "status": "failed",
                    "progressPercent": 0,
                    "progressMessage": None,
                    "errorMessage": (
                        "Traitement interrompu (redemarrage du serveur). "
                        "Utilisez « Reprendre » pour continuer la ou ca s'est arrete."
                    ),
                }
            )
            recovered += 1
        if recovered:
            _save_tasks(tasks)
        return recovered
