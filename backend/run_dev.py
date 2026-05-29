"""Lance Uvicorn : mode stable par defaut, reload optionnel pour le dev Python."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

BACKEND_DIR = Path(__file__).resolve().parent
RELOAD_DIRS = [
    str(BACKEND_DIR / "services"),
    str(BACKEND_DIR / "routers"),
    str(BACKEND_DIR),
]
RELOAD_EXCLUDES = [
    "data",
    "data/**",
    "venv",
    "venv/**",
    "**/__pycache__",
    "**/__pycache__/**",
    "**/*.pyc",
    ".env",
]


def _reload_enabled() -> bool:
    return os.getenv("UVICORN_RELOAD", "0").lower() in ("1", "true", "yes")


def _watchfiles_available() -> bool:
    try:
        import watchfiles  # noqa: F401

        return True
    except ImportError:
        return False


def main() -> None:
    reload_on = _reload_enabled()
    kwargs: dict = {
        "app": "main:app",
        "host": "127.0.0.1",
        "port": 8000,
        "reload": reload_on,
    }
    if reload_on:
        kwargs["reload_delay"] = 1.0
        if _watchfiles_available():
            kwargs["reload_dirs"] = [str(BACKEND_DIR)]
            kwargs["reload_excludes"] = RELOAD_EXCLUDES
        else:
            # StatReload : limiter aux dossiers code (pas venv/data)
            kwargs["reload_dirs"] = [
                str(BACKEND_DIR / "services"),
                str(BACKEND_DIR / "routers"),
            ]
    uvicorn.run(**kwargs)


if __name__ == "__main__":
    main()
