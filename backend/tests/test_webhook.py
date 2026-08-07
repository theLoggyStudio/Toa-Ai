"""Tests de sécurité du webhook PayDunya et de la validation des uploads."""

from fastapi.testclient import TestClient

import routers.tasks as tasks_router
from main import app
from models import TranslationTask
from routers.tasks import _is_real_image

client = TestClient(app)

WEBHOOK_URL = "/api/webhooks/paydunya"


def _fake_task(status: str = "pending_payment") -> TranslationTask:
    return TranslationTask(
        id="task-123",
        originalImagesCount=2,
        sourceLanguage="auto",
        targetLanguage="fr",
        status=status,
        amountCFA=500,
        payduniaToken="secret-token",
    )


def _patch_common(monkeypatch, *, confirm_status: str = "completed"):
    started: list[str] = []
    monkeypatch.setattr(
        tasks_router, "get_task", lambda task_id: _fake_task()
    )
    monkeypatch.setattr(
        tasks_router,
        "verify_webhook_token",
        lambda token, task_id: token == "secret-token",
    )
    monkeypatch.setattr(
        tasks_router,
        "confirm_checkout_invoice",
        lambda token, kind="translate": {"status": confirm_status},
    )
    monkeypatch.setattr(
        tasks_router,
        "_start_pipeline_after_payment",
        lambda task_id, token=None: started.append(task_id) or True,
    )
    return started


class TestPaydunyaWebhook:
    def test_webhook_without_token_rejected(self, monkeypatch):
        started = _patch_common(monkeypatch)
        resp = client.post(
            WEBHOOK_URL,
            json={
                "status": "completed",
                "custom_data": {"task_id": "task-123"},
            },
        )
        assert resp.status_code == 403
        assert started == []

    def test_webhook_with_bad_token_rejected(self, monkeypatch):
        started = _patch_common(monkeypatch)
        resp = client.post(
            WEBHOOK_URL,
            json={
                "status": "completed",
                "token": "forged",
                "custom_data": {"task_id": "task-123"},
            },
        )
        assert resp.status_code == 403
        assert started == []

    def test_webhook_valid_token_starts_pipeline(self, monkeypatch):
        started = _patch_common(monkeypatch)
        resp = client.post(
            WEBHOOK_URL,
            json={
                "status": "completed",
                "token": "secret-token",
                "custom_data": {"task_id": "task-123"},
            },
        )
        assert resp.status_code == 200
        assert started == ["task-123"]

    def test_webhook_unconfirmed_by_paydunya_ignored(self, monkeypatch):
        # Le payload prétend "completed" mais l'API PayDunya dit "pending".
        started = _patch_common(monkeypatch, confirm_status="pending")
        resp = client.post(
            WEBHOOK_URL,
            json={
                "status": "completed",
                "token": "secret-token",
                "custom_data": {"task_id": "task-123"},
            },
        )
        assert resp.status_code == 200
        assert resp.json().get("ignored") is True
        assert started == []

    def test_webhook_non_success_status_ignored(self, monkeypatch):
        started = _patch_common(monkeypatch)
        resp = client.post(
            WEBHOOK_URL,
            json={
                "status": "cancelled",
                "token": "secret-token",
                "custom_data": {"task_id": "task-123"},
            },
        )
        assert resp.status_code == 200
        assert started == []


class TestTaskSerialization:
    def test_pydunia_token_never_serialized(self):
        task = _fake_task()
        assert "secret-token" not in task.model_dump_json()
        assert "payduniaToken" not in task.model_dump()


class TestUploadValidation:
    def test_png_magic_accepted(self):
        assert _is_real_image(b"\x89PNG\r\n\x1a\n" + b"0" * 16)

    def test_jpeg_magic_accepted(self):
        assert _is_real_image(b"\xff\xd8\xff\xe0" + b"0" * 16)

    def test_non_image_rejected(self):
        assert not _is_real_image(b"<?php echo 'pwned'; ?>")
        assert not _is_real_image(b"")
