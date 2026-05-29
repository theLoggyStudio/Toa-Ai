"""Purge task data between sessions (no cache)."""

import json
import logging
import shutil
from pathlib import Path

from config import OUTPUT_DIR, UPLOAD_DIR

logger = logging.getLogger(__name__)
TASKS_FILE = OUTPUT_DIR / "tasks.json"


def _rm_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def purge_task_artifacts(task_id: str) -> None:
    _rm_tree(UPLOAD_DIR / task_id)
    _rm_tree(OUTPUT_DIR / task_id)
    pdf = OUTPUT_DIR / f"{task_id}.pdf"
    if pdf.exists():
        pdf.unlink(missing_ok=True)


def purge_all_tasks(*, keep_task_id: str | None = None) -> None:
    """Clear uploads, outputs and PDFs; optionally keep one active task."""
    if UPLOAD_DIR.exists():
        for entry in UPLOAD_DIR.iterdir():
            if entry.is_dir() and entry.name != keep_task_id:
                _rm_tree(entry)

    if OUTPUT_DIR.exists():
        for entry in OUTPUT_DIR.iterdir():
            if entry.is_dir() and entry.name != keep_task_id:
                _rm_tree(entry)
            elif entry.is_file() and entry.suffix == ".pdf":
                if keep_task_id is None or entry.stem != keep_task_id:
                    entry.unlink(missing_ok=True)

    if keep_task_id is None and TASKS_FILE.exists():
        TASKS_FILE.write_text("{}", encoding="utf-8")
    elif TASKS_FILE.exists():
        try:
            tasks = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
            tasks = {k: v for k, v in tasks.items() if k == keep_task_id}
            TASKS_FILE.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
        except json.JSONDecodeError:
            TASKS_FILE.write_text("{}", encoding="utf-8")

    logger.info("Purge done (keep_task_id=%s)", keep_task_id)
