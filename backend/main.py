import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from config import get_frontend_origins
from routers.tasks import router as tasks_router
from services.storage import recover_interrupted_tasks
from services.transformation_report import render_home_page

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    recovered = recover_interrupted_tasks()
    if recovered:
        logger.warning(
            "%d tache(s) en processing remise(s) en failed apres redemarrage",
            recovered,
        )
    yield


app = FastAPI(
    title="Toa AI API",
    description="Traducteur automatique de mangas/manhwas",
    version="1.0.0",
    lifespan=lifespan,
)

origins = list(
    dict.fromkeys(
        [
            *get_frontend_origins(),
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks_router)


@app.get("/", response_class=HTMLResponse)
async def home(task: str | None = Query(None, description="ID de tâche à afficher")):
    """Vue disques des transformations (dernière tâche par défaut)."""
    return HTMLResponse(render_home_page(task_id=task))


@app.post("/api/glossary/preload")
async def preload_glossary():
    """Télécharge et indexe les dictionnaires RAG (JMdict + Kengdic)."""
    from services.glossary_rag import preload_dictionaries

    return preload_dictionaries()


@app.get("/health")
async def health():
    from config import CURSOR_MODEL, is_ocr_fast_mode
    from services.translation import (
        get_translator_status,
        is_translator_available,
    )

    from services.glossary_rag import get_glossary_status

    translator = get_translator_status()
    return {
        "status": "ok",
        "service": "Toa AI",
        "ocrFastMode": is_ocr_fast_mode(),
        "translatorAvailable": is_translator_available(),
        "translatorProvider": translator.get("provider"),
        "cursorModelConfigured": CURSOR_MODEL,
        "cursorModelActive": translator.get("model"),
        "glossaryRag": get_glossary_status(),
    }
