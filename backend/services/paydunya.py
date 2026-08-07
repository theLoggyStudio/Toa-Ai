"""Intégration API Checkout PayDunya (sandbox + production)."""

import json
import urllib.error
import urllib.request

from config import (
    BACKEND_PUBLIC_URL,
    FRONTEND_ORIGIN,
    PAYDUNYA_API_URL,
    PAYDUNYA_MASTER_KEY,
    PAYDUNYA_MODE,
    PAYDUNYA_PRIVATE_KEY,
    PAYDUNYA_TOKEN,
)
from models import TranslationTask


class PayDunyaError(Exception):
    pass


def _paydunya_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "ToaAI/1.0 (PayDunya-Checkout)",
        "PAYDUNYA-MASTER-KEY": PAYDUNYA_MASTER_KEY,
        "PAYDUNYA-PRIVATE-KEY": PAYDUNYA_PRIVATE_KEY,
        "PAYDUNYA-TOKEN": PAYDUNYA_TOKEN,
    }


def _confirm_url(token: str) -> str:
    base = (
        "https://app.paydunya.com/api/v1/checkout-invoice/confirm"
        if PAYDUNYA_MODE == "production"
        else "https://app.paydunya.com/sandbox-api/v1/checkout-invoice/confirm"
    )
    return f"{base}/{token}"


def _frontend_return_base(task: TranslationTask) -> str:
    """URL de page front selon le produit (traduction vs Éclat)."""
    origin = FRONTEND_ORIGIN.rstrip("/")
    if getattr(task, "kind", "translate") == "restore":
        return f"{origin}/eclat"
    return f"{origin}/TOA.ai"


def create_checkout_invoice(task: TranslationTask) -> tuple[str, str]:
    if not PAYDUNYA_MASTER_KEY or not PAYDUNYA_PRIVATE_KEY or not PAYDUNYA_TOKEN:
        raise PayDunyaError(
            "Clés PayDunya manquantes. Configurez PAYDUNYA_* dans backend/.env."
        )

    callback_url = f"{BACKEND_PUBLIC_URL.rstrip('/')}/api/webhooks/paydunya"
    page_base = _frontend_return_base(task)
    if task.kind == "restore":
        description = f"Éclat - restauration photo ({task.amountCFA} FCFA)"
    else:
        description = (
            f"Traduction Manga Toa AI - {task.billableBubblesCount} bulles"
        )

    payload = {
        "invoice": {
            "total_amount": task.amountCFA,
            "description": description,
        },
        "store": {"name": "Toa AI"},
        "custom_data": {"task_id": task.id, "kind": task.kind},
        "actions": {
            "cancel_url": f"{page_base}?task_id={task.id}&cancelled=1",
            "return_url": f"{page_base}?task_id={task.id}&paid_return=1",
            "callback_url": callback_url,
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        PAYDUNYA_API_URL,
        data=data,
        headers=_paydunya_headers(),
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if "error code: 1010" in detail:
            raise PayDunyaError(
                "PayDunya a refusé la connexion (protection Cloudflare). "
                "Réessayez dans quelques instants."
            ) from exc
        raise PayDunyaError(detail) from exc

    if body.get("response_code") != "00":
        raise PayDunyaError(body.get("response_text", "Erreur PayDunya"))

    token = body["token"]
    url = body.get("response_text") or body.get("invoice_url", "")
    if not url:
        raise PayDunyaError("PayDunya n'a pas renvoyé d'URL de paiement.")
    return token, url


def confirm_checkout_invoice(token: str) -> dict:
    """Vérifie le statut d'une facture (après retour utilisateur ou IPN)."""
    req = urllib.request.Request(
        _confirm_url(token),
        headers=_paydunya_headers(),
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if "error code: 1010" in detail:
            raise PayDunyaError(
                "PayDunya a refusé la connexion (protection Cloudflare). "
                "Réessayez dans quelques instants."
            ) from exc
        raise PayDunyaError(detail) from exc


def invoice_status_from_confirm(body: dict) -> str:
    status = str(body.get("status", "")).lower()
    if status:
        return status
    invoice = body.get("invoice")
    if isinstance(invoice, dict):
        return str(invoice.get("status", "")).lower()
    return ""


def verify_webhook_token(token: str, task_id: str) -> bool:
    from services.storage import _load_tasks

    tasks = _load_tasks()
    raw = tasks.get(task_id)
    if not raw:
        return False
    return raw.get("payduniaToken") == token
