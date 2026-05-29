import json
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
from fastapi.responses import FileResponse, HTMLResponse

from config import (
    DISABLE_PAYMENT,
    FRONTEND_ORIGIN,
    OUTPUT_DIR,
    PRICE_PER_BUBBLE_CFA,
)
from languages import SUPPORTED_SOURCE_CODES, SUPPORTED_TARGET_CODES
from models import (
    AppConfigResponse,
    CheckoutResponse,
    StartProcessingResponse,
    TransformationReportResponse,
    TranslationTask,
    UploadResponse,
)
from services.transformation_report import load_report, render_html_report
from services.cleanup import purge_all_tasks
from services.ocr import detect_bubbles, reset_ocr_engines
from services.paydunya import PayDunyaError, create_checkout_invoice
from services.storage import (
    create_task,
    get_output_pdf,
    get_task,
    get_upload_dir,
    update_task,
)
from services.worker import schedule_pipeline

router = APIRouter(prefix="/api", tags=["tasks"])


@router.post("/session/reset")
async def reset_session():
    """Efface toutes les tâches et fichiers avant une nouvelle évaluation."""
    purge_all_tasks()
    reset_ocr_engines()
    return {"ok": True, "message": "Session réinitialisée"}


@router.get("/config", response_model=AppConfigResponse)
async def app_config():
    return AppConfigResponse(
        paymentDisabled=DISABLE_PAYMENT,
        pricePerBubbleCFA=PRICE_PER_BUBBLE_CFA,
    )


@router.post("/tasks/upload", response_model=UploadResponse)
async def upload_task(
    images: List[UploadFile] = File(...),
    source_language: str = Form("auto"),
    target_language: str = Form("fr"),
):
    if source_language not in SUPPORTED_SOURCE_CODES:
        raise HTTPException(400, "Langue source invalide.")
    if target_language not in SUPPORTED_TARGET_CODES:
        raise HTTPException(400, "Langue cible invalide.")
    if not images:
        raise HTTPException(400, "Aucune image fournie.")

    count = len(images)
    # Tarification par bulle: estimation immédiate avant paiement.
    estimated_bubbles = 0
    task = create_task(
        count,
        source_language,
        target_language,
        amount_cfa=0,
        billable_bubbles_count=0,
    )
    upload_dir = get_upload_dir(task.id)

    for idx, upload in enumerate(images):
        suffix = Path(upload.filename or "page.png").suffix or ".png"
        dest = upload_dir / f"page_{idx:04d}{suffix}"
        content = await upload.read()
        dest.write_bytes(content)
        estimated_bubbles += len(detect_bubbles(dest))

    amount = estimated_bubbles * PRICE_PER_BUBBLE_CFA
    update_task(
        task.id,
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
        token, payment_url = create_checkout_invoice(task)
    except PayDunyaError as exc:
        raise HTTPException(502, str(exc)) from exc

    update_task(task_id, payduniaToken=token)

    if "mock_payment" in payment_url:
        background_tasks.add_task(_mock_payment_success, task_id, token)  # noqa: still uses thread via schedule

    return CheckoutResponse(paymentUrl=payment_url, token=token)


def _mock_payment_success(task_id: str, token: str) -> None:
    update_task(task_id, status="paid", payduniaToken=token)
    task = get_task(task_id)
    if not task:
        return
    update_task(
        task_id,
        status="processing",
        progressPercent=5,
        progressMessage="Démarrage…",
    )
    schedule_pipeline(
        task_id, task.sourceLanguage, task.targetLanguage
    )


@router.get("/tasks/{task_id}", response_model=TranslationTask)
async def task_status(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Tâche introuvable.")
    return task


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

    if status != "completed" and status != "success":
        return {"received": True, "ignored": True}

    if not task_id:
        raise HTTPException(400, "task_id manquant dans custom_data.")

    task = get_task(task_id)
    if not task:
        raise HTTPException(404, "Tâche introuvable.")

    update_task(
        task_id,
        status="processing",
        payduniaToken=token,
        progressPercent=5,
        progressMessage="Démarrage…",
    )
    schedule_pipeline(
        task_id, task.sourceLanguage, task.targetLanguage
    )
    return {"received": True}
