import asyncio
import json
import shutil
from pathlib import Path
from typing import List

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from config import (
    DISABLE_PAYMENT,
    FRONTEND_ORIGIN,
    OUTPUT_DIR,
    PRICE_BASE_CFA,
    PRICE_PER_BUBBLE_CFA,
    amount_cfa_for_bubbles,
    estimate_bubbles_for_pages,
)
from languages import SUPPORTED_TARGET_CODES
from models import (
    AppConfigResponse,
    CheckoutResponse,
    ConfirmPaymentResponse,
    StartProcessingResponse,
    TransformationReportResponse,
    TranslationTask,
    UploadResponse,
)
from services.transformation_report import load_report, render_html_report
from services.cleanup import purge_all_tasks
from services.translation import is_translator_available
from services.scan_ingest import SUPPORTED_UPLOAD_SUFFIXES, normalize_upload_dir
from services.paydunya import (
    PayDunyaError,
    confirm_checkout_invoice,
    create_checkout_invoice,
    invoice_status_from_confirm,
    verify_webhook_token,
)
from services.storage import (
    create_task,
    get_output_pdf,
    get_task,
    get_upload_dir,
    update_task,
)
from services.worker import schedule_pipeline

router = APIRouter(prefix="/api", tags=["tasks"])

_PAYMENT_DONE_STATUSES = frozenset({"processing", "completed", "paid"})


def _start_pipeline_after_payment(task_id: str, token: str | None = None) -> bool:
    """Démarre le pipeline une seule fois après paiement confirmé."""
    task = get_task(task_id)
    if not task:
        return False
    if task.status in _PAYMENT_DONE_STATUSES:
        return False
    updates: dict = {
        "status": "processing",
        "progressPercent": 5,
        "progressMessage": "Démarrage…",
        "errorMessage": None,
    }
    if token:
        updates["payduniaToken"] = token
    update_task(task_id, **updates)
    schedule_pipeline(task_id, task.sourceLanguage, task.targetLanguage)
    return True


def _clear_upload_dir(upload_dir: Path) -> None:
    upload_dir.mkdir(parents=True, exist_ok=True)
    for entry in upload_dir.iterdir():
        if entry.is_file():
            entry.unlink(missing_ok=True)
        elif entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)


def _dedupe_upload_files(files: List[UploadFile]) -> List[UploadFile]:
    seen: set[tuple[str, int | None]] = set()
    unique: List[UploadFile] = []
    for upload in files:
        key = (upload.filename or "page.png", upload.size)
        if key in seen:
            continue
        seen.add(key)
        unique.append(upload)
    return unique


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


@router.post("/session/reset")
async def reset_session(request: Request):
    """Efface toutes les tâches et fichiers avant une nouvelle évaluation.

    Action destructive : réservée aux requêtes locales (le backend tourne
    sur la machine de l'utilisateur, son navigateur est donc en loopback).
    """
    client_host = request.client.host if request.client else ""
    if client_host not in _LOOPBACK_HOSTS:
        raise HTTPException(403, "Réinitialisation réservée à la machine locale.")
    purge_all_tasks()
    return {"ok": True, "message": "Session réinitialisée"}


@router.get("/config", response_model=AppConfigResponse)
async def app_config():
    return AppConfigResponse(
        paymentDisabled=DISABLE_PAYMENT,
        priceBaseCFA=PRICE_BASE_CFA,
        pricePerBubbleCFA=PRICE_PER_BUBBLE_CFA,
    )


def _parse_form_bool(value: str) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "on")


MAX_UPLOAD_FILE_BYTES = 20 * 1024 * 1024  # 20 Mo par image
MAX_UPLOAD_TOTAL_BYTES = 300 * 1024 * 1024  # 300 Mo par requête
MAX_UPLOAD_FILES = 200

_IMAGE_MAGIC_PREFIXES: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",  # JPEG
)


def _is_real_image(data: bytes) -> bool:
    return any(data.startswith(magic) for magic in _IMAGE_MAGIC_PREFIXES)


@router.post("/tasks/upload", response_model=UploadResponse)
async def upload_task(
    images: List[UploadFile] = File(...),
    target_language: str = Form("fr"),
    include_toa: str = Form("true"),
):
    source_language = "auto"
    if target_language not in SUPPORTED_TARGET_CODES:
        raise HTTPException(400, "Langue cible invalide.")
    if not images:
        raise HTTPException(400, "Aucun fichier fourni.")

    images = _dedupe_upload_files(images)
    if len(images) > MAX_UPLOAD_FILES:
        raise HTTPException(400, f"Trop d'images ({MAX_UPLOAD_FILES} max par envoi).")

    task = create_task(
        0,
        source_language,
        target_language,
        amount_cfa=0,
        billable_bubbles_count=0,
        include_toa=_parse_form_bool(include_toa),
    )
    upload_dir = get_upload_dir(task.id)
    _clear_upload_dir(upload_dir)

    total_bytes = 0
    for idx, upload in enumerate(images):
        suffix = (Path(upload.filename or "page.png").suffix or ".png").lower()
        if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
            raise HTTPException(
                400,
                "Format non supporté. Utilisez PNG, JPG ou JPEG.",
            )
        data = await upload.read()
        if len(data) > MAX_UPLOAD_FILE_BYTES:
            raise HTTPException(
                400,
                f"Image trop lourde ({upload.filename}). "
                f"Maximum {MAX_UPLOAD_FILE_BYTES // (1024 * 1024)} Mo par fichier.",
            )
        total_bytes += len(data)
        if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
            raise HTTPException(
                400,
                f"Envoi trop volumineux (max {MAX_UPLOAD_TOTAL_BYTES // (1024 * 1024)} Mo).",
            )
        if not _is_real_image(data):
            raise HTTPException(
                400,
                f"Fichier invalide ({upload.filename}) : "
                "le contenu n'est pas une image PNG ou JPEG.",
            )
        dest = upload_dir / f"raw_{idx:04d}{suffix}"
        dest.write_bytes(data)

    page_paths = normalize_upload_dir(upload_dir)
    if not page_paths:
        raise HTTPException(400, "Impossible d'extraire des pages du fichier.")

    if not is_translator_available():
        raise HTTPException(
            503,
            "Cursor indisponible. Verifiez CURSOR_API_KEY dans backend/.env.",
        )

    page_count = len(page_paths)
    estimated_bubbles = estimate_bubbles_for_pages(page_count)
    amount = amount_cfa_for_bubbles(estimated_bubbles)
    update_task(
        task.id,
        originalImagesCount=page_count,
        sourceLanguage="auto",
        amountCFA=amount,
        billableBubblesCount=estimated_bubbles,
    )
    task = get_task(task.id) or task

    return UploadResponse(
        task=task,
        checkoutReady=not DISABLE_PAYMENT,
        paymentDisabled=DISABLE_PAYMENT,
    )


@router.post("/tasks/{task_id}/start", response_model=StartProcessingResponse)
async def start_processing(task_id: str, background_tasks: BackgroundTasks):
    if not DISABLE_PAYMENT:
        raise HTTPException(403, "Le paiement est requis. Désactivez DISABLE_PAYMENT pour les tests.")

    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Tâche introuvable.")
    if task.status != "pending_payment":
        raise HTTPException(400, "Cette tâche ne peut pas être démarrée.")

    update_task(
        task_id,
        status="processing",
        progressPercent=5,
        progressMessage="Démarrage…",
        errorMessage=None,
    )
    schedule_pipeline(task_id, task.sourceLanguage, task.targetLanguage)
    updated = get_task(task_id)
    return StartProcessingResponse(task=updated)  # type: ignore[arg-type]


@router.post("/tasks/{task_id}/checkout", response_model=CheckoutResponse)
async def checkout(task_id: str, background_tasks: BackgroundTasks):
    if DISABLE_PAYMENT:
        raise HTTPException(
            403,
            "Paiement désactivé (mode test). Utilisez POST /api/tasks/{id}/start",
        )

    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Tâche introuvable.")
    if task.status != "pending_payment":
        raise HTTPException(400, "Cette tâche n'est pas en attente de paiement.")

    try:
        token, payment_url = await run_in_threadpool(create_checkout_invoice, task)
    except PayDunyaError as exc:
        raise HTTPException(502, str(exc)) from exc

    update_task(task_id, payduniaToken=token)

    if "mock_payment" in payment_url:
        background_tasks.add_task(_mock_payment_success, task_id, token)  # noqa: still uses thread via schedule

    return CheckoutResponse(paymentUrl=payment_url)


@router.post(
    "/tasks/{task_id}/confirm-payment",
    response_model=ConfirmPaymentResponse,
)
async def confirm_payment(task_id: str):
    """Confirme le paiement PayDunya au retour utilisateur (complète l'IPN local)."""
    if DISABLE_PAYMENT:
        raise HTTPException(
            403,
            "Paiement désactivé. Utilisez POST /api/tasks/{id}/start.",
        )

    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Tâche introuvable.")
    if task.status in _PAYMENT_DONE_STATUSES:
        return ConfirmPaymentResponse(
            task=task,
            alreadyStarted=True,
        )
    if not task.payduniaToken:
        raise HTTPException(400, "Aucune facture PayDunya pour cette tâche.")

    try:
        body = await run_in_threadpool(
            confirm_checkout_invoice, task.payduniaToken
        )
    except PayDunyaError as exc:
        raise HTTPException(502, str(exc)) from exc

    status = invoice_status_from_confirm(body)
    if status not in ("completed", "success"):
        return ConfirmPaymentResponse(
            task=get_task(task_id) or task,  # type: ignore[arg-type]
            paymentPending=True,
        )

    started = _start_pipeline_after_payment(task_id, task.payduniaToken)
    updated = get_task(task_id)
    return ConfirmPaymentResponse(
        task=updated,  # type: ignore[arg-type]
        alreadyStarted=not started,
    )


def _mock_payment_success(task_id: str, token: str) -> None:
    _start_pipeline_after_payment(task_id, token)


@router.get("/tasks/{task_id}", response_model=TranslationTask)
async def task_status(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Tâche introuvable.")
    return task


_SSE_ACTIVE_STATUSES = frozenset({"processing", "paid"})


@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str):
    """Flux SSE : pousse l'état de la tâche dès qu'il change (remplace le polling)."""
    if not get_task(task_id):
        raise HTTPException(404, "Tâche introuvable.")

    async def stream():
        last_payload: str | None = None
        while True:
            task = await run_in_threadpool(get_task, task_id)
            if not task:
                break
            payload = task.model_dump_json()
            if payload != last_payload:
                last_payload = payload
                yield f"data: {payload}\n\n"
            if task.status not in _SSE_ACTIVE_STATUSES:
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/tasks/{task_id}/retry", response_model=StartProcessingResponse)
async def retry_task(task_id: str):
    """Relance une tâche échouée en reprenant depuis le checkpoint par page."""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Tâche introuvable.")
    if task.status != "failed":
        raise HTTPException(400, "Seule une tâche échouée peut être relancée.")

    from services.scan_ingest import list_page_images

    if not list_page_images(get_upload_dir(task_id)):
        raise HTTPException(
            410,
            "Les images source ne sont plus disponibles. "
            "Relancez une nouvelle traduction.",
        )

    update_task(
        task_id,
        status="processing",
        progressPercent=2,
        progressMessage="Reprise du traitement…",
        errorMessage=None,
    )
    schedule_pipeline(task_id, task.sourceLanguage, task.targetLanguage)
    updated = get_task(task_id)
    return StartProcessingResponse(
        task=updated,  # type: ignore[arg-type]
        message="Reprise démarrée",
    )


@router.get(
    "/tasks/{task_id}/transformations",
    response_model=TransformationReportResponse,
)
async def get_transformations(task_id: str):
    """Liste JSON : images, textes par ordre et traductions."""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Tâche introuvable.")
    report = load_report(task_id)
    if not report:
        raise HTTPException(
            404,
            "Rapport de transformation indisponible (traitement non démarré ou en cours).",
        )
    return TransformationReportResponse(
        taskId=report["taskId"],
        sourceLanguage=report["sourceLanguage"],
        targetLanguage=report["targetLanguage"],
        pages=report["pages"],
        viewUrl=f"/api/tasks/{task_id}/transformations/view",
    )


@router.get("/tasks/{task_id}/transformations/view", response_class=HTMLResponse)
async def view_transformations(task_id: str):
    """Vue HTML : un disque par image, bulles alignées en cercle."""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Tâche introuvable.")
    report = load_report(task_id)
    if not report:
        raise HTTPException(404, "Rapport de transformation indisponible.")
    return HTMLResponse(
        render_html_report(
            report,
            auto_refresh=_task_processing(report["taskId"]),
            is_home=False,
        )
    )


def _task_processing(task_id: str) -> bool:
    from services.transformation_report import _task_is_active

    return _task_is_active(task_id)


@router.get("/tasks/{task_id}/pdf")
async def download_pdf(task_id: str):
    task = get_task(task_id)
    if not task or task.status != "completed":
        raise HTTPException(404, "PDF non disponible.")
    pdf = get_output_pdf(task_id)
    if not pdf:
        raise HTTPException(404, "Fichier PDF introuvable.")
    return FileResponse(
        pdf,
        media_type="application/pdf",
        filename=f"toa-ai-{task_id[:8]}.pdf",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@router.get("/tasks/{task_id}/pdf/partial")
async def download_partial_pdf(task_id: str):
    """PDF des lots déjà traduits, disponible pendant le traitement."""
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Tâche introuvable.")
    partial = OUTPUT_DIR / f"{task_id}_partial.pdf"
    if not partial.exists():
        raise HTTPException(404, "Aucun PDF partiel disponible pour le moment.")
    return FileResponse(
        partial,
        media_type="application/pdf",
        filename=f"toa-ai-{task_id[:8]}-partiel.pdf",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@router.post("/webhooks/paydunya")
async def paydunya_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    body = await request.body()
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(400, "JSON invalide.")

    status = payload.get("status", payload.get("invoice_status", ""))
    token = payload.get("token") or payload.get("invoice_token", "")
    custom = payload.get("custom_data", {})
    task_id = custom.get("task_id") if isinstance(custom, dict) else None

    if status not in ("completed", "success"):
        return {"received": True, "ignored": True}

    if not task_id:
        raise HTTPException(400, "task_id manquant dans custom_data.")

    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Tâche introuvable.")

    # Token obligatoire : un webhook sans token ne doit jamais lancer le pipeline.
    if not token or not verify_webhook_token(token, task_id):
        raise HTTPException(403, "Token PayDunya manquant ou invalide.")

    # Ne jamais faire confiance au statut du payload : re-confirmer côté PayDunya.
    try:
        body = await run_in_threadpool(confirm_checkout_invoice, token)
    except PayDunyaError as exc:
        raise HTTPException(502, str(exc)) from exc
    if invoice_status_from_confirm(body) not in ("completed", "success"):
        return {"received": True, "ignored": True, "reason": "non confirmé par PayDunya"}

    _start_pipeline_after_payment(task_id, token)
    return {"received": True}
