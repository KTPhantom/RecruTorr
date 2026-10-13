import asyncio
import datetime
import threading
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from telegram.ext import Application

from bot import main as bot_main
from src.schemas import Profile
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


def test_preferences_keyboard_exposes_custom_and_new_preference_options():
    keyboard = bot_main.build_preferences_keyboard(
        Profile(
            user_id="123",
            custom_titles=["iOS Developer"],
            locations=["Pune"],
            work_modes=["hybrid"],
            job_types=["internship"],
        )
    )
    callback_data = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    }

    assert "pref:add_city" in callback_data
    assert "pref:add_title" in callback_data
    assert "pref:mode:hybrid" in callback_data
    assert "pref:type:internship" in callback_data
    assert "pref:remove_title:0" in callback_data


def test_initial_job_refresh_is_shared(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def refresh():
        calls.append(1)
        started.set()
        release.wait(timeout=3)
        return {}

    async def run():
        first = bot_main._start_job_refresh()
        second = bot_main._start_job_refresh()
        assert first is second
        assert await asyncio.to_thread(started.wait, 3)
        release.set()
        await asyncio.gather(first, second)

    monkeypatch.setattr(bot_main, "_job_refresh_task", None)
    monkeypatch.setattr(bot_main, "refresh_job_listings", refresh)

    asyncio.run(run())

    assert calls == [1]


def test_resume_parse_preserves_configured_preferences():
    parsed = Profile(user_id="123", skills=["python"], locations=["Bengaluru"])
    saved = Profile(
        user_id="123",
        target_roles=["backend"],
        custom_titles=["iOS Developer"],
        locations=[],
        work_modes=["hybrid"],
        job_types=["internship"],
        digest_frequency="on_demand",
        preferences_configured=True,
    )

    merged = bot_main.merge_saved_preferences(parsed, saved)

    assert merged.skills == ["python"]
    assert merged.target_roles == ["backend"]
    assert merged.custom_titles == ["iOS Developer"]
    assert merged.locations == []
    assert merged.work_modes == ["hybrid"]
    assert merged.job_types == ["internship"]
    assert merged.digest_frequency == "on_demand"


def test_digest_cadence_is_daily_weekly_or_manual():
    monday = datetime.datetime(2026, 10, 12, 4, 0, tzinfo=datetime.timezone.utc)
    tuesday = datetime.datetime(2026, 10, 13, 4, 0, tzinfo=datetime.timezone.utc)

    assert bot_main.should_send_digest(Profile(user_id="daily"), tuesday)
    assert bot_main.should_send_digest(
        Profile(user_id="weekly", digest_frequency="weekly"),
        monday,
    )
    assert not bot_main.should_send_digest(
        Profile(user_id="weekly", digest_frequency="weekly"),
        tuesday,
    )
    assert not bot_main.should_send_digest(
        Profile(user_id="manual", digest_frequency="on_demand"),
        monday,
    )
