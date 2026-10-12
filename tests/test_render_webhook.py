from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from telegram.ext import Application

from bot import main as bot_main
from src.db import PostgresConnection


class RecordingConnection:
    def __init__(self):
        self.query = None
        self.parameters = None

    def execute(self, query, parameters):
        self.query = query
        self.parameters = parameters
        return "cursor"


def test_postgres_adapter_translates_sqlite_placeholders():
    raw_connection = RecordingConnection()
    connection = PostgresConnection(raw_connection)

    result = connection.execute("SELECT * FROM users WHERE id = ? AND name = ?", (7, "Ada"))

    assert result == "cursor"
    assert raw_connection.query == "SELECT * FROM users WHERE id = %s AND name = %s"
    assert raw_connection.parameters == (7, "Ada")


def test_telegram_webhook_requires_secret_and_queues_update():
    application = Application.builder().token("123456:test-token").build()
    client = TestClient(bot_main.create_web_app(application, "webhook-secret", "scheduler-secret"))

    rejected = client.post("/telegram", json={"update_id": 1})
    accepted = client.post(
        "/telegram",
        json={"update_id": 2},
        headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert application.update_queue.get_nowait().update_id == 2


def test_scheduler_endpoint_requires_secret_and_runs_requested_task(monkeypatch):
    application = Application.builder().token("123456:test-token").build()
    client = TestClient(bot_main.create_web_app(application, "webhook-secret", "scheduler-secret"))
    run_digest = AsyncMock()
    monkeypatch.setattr(bot_main, "scheduled_daily_digest", run_digest)

    rejected = client.post("/internal/run-scheduled", json={"task": "digest"})
    accepted = client.post(
        "/internal/run-scheduled",
        json={"task": "digest"},
        headers={"X-Scheduler-Secret": "scheduler-secret"},
    )

    assert rejected.status_code == 403
    assert accepted.status_code == 200
    run_digest.assert_awaited_once_with(application.bot)
