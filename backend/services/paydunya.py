"""Intégration API Checkout PayDunya."""

import json
import urllib.error
import urllib.request

from config import (
    FRONTEND_ORIGIN,
    PAYDUNYA_API_URL,
    PAYDUNYA_MASTER_KEY,
    PAYDUNYA_PRIVATE_KEY,
    PAYDUNYA_TOKEN,
)
from models import TranslationTask


class PayDunyaError(Exception):
    pass


def create_checkout_invoice(task: TranslationTask) -> tuple[str, str]:
    if not PAYDUNYA_MASTER_KEY or not PAYDUNYA_PRIVATE_KEY or not PAYDUNYA_TOKEN:
        mock_token = f"mock_{task.id}"
        mock_url = (
            f"{FRONTEND_ORIGIN}?task_id={task.id}"
            f"&mock_payment=1&token={mock_token}"
        )
        return mock_token, mock_url

    payload = {
        "invoice": {
            "total_amount": task.amountCFA,
            "description": (
                f"Traduction Manga Toa AI - {task.billableBubblesCount} bulles"
            ),
        },
        "store": {"name": "Toa AI"},
        "custom_data": {"task_id": task.id},
        "actions": {
            "cancel_url": f"{FRONTEND_ORIGIN}?task_id={task.id}&cancelled=1",
            "return_url": f"{FRONTEND_ORIGIN}?task_id={task.id}",
        },
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        PAYDUNYA_API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "PAYDUNYA-MASTER-KEY": PAYDUNYA_MASTER_KEY,
            "PAYDUNYA-PRIVATE-KEY": PAYDUNYA_PRIVATE_KEY,
            "PAYDUNYA-TOKEN": PAYDUNYA_TOKEN,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise PayDunyaError(detail) from exc

    if body.get("response_code") != "00":
        raise PayDunyaError(body.get("response_text", "Erreur PayDunya"))

    token = body["token"]
    url = body.get("response_text") or body.get("invoice_url", "")
    return token, url


def verify_webhook_token(token: str, task_id: str) -> bool:
    from services.storage import _load_tasks

    tasks = _load_tasks()
    raw = tasks.get(task_id)
    if not raw:
        return False
    return raw.get("payduniaToken") == token
