import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src import db
from src.schemas import Job, canonicalize_job_url
from src.verifier import verify_job
from bot import main as bot_main


def test_canonicalize_job_url_removes_tracking_and_fragment():
    assert canonicalize_job_url(
        "https://Careers.Example/jobs/123/?utm_source=feed#apply"
    ) == "https://careers.example/jobs/123"


def test_save_jobs_deduplicates_canonical_urls_across_sources(tmp_path):
    conn = db.get_db(tmp_path / "dedupe.db")
    try:
        jobs = [
            Job("Example", "Graduate Engineer", "Remote", "", "https://jobs.example/1?utm_source=rss", "rss", "one", verified=True),
            Job("Example", "Graduate Engineer", "Remote", "", "https://jobs.example/1#apply", "manual", "two", verified=True),
        ]

        db.save_jobs(conn, jobs, domains=["jobs.example"])

        rows = conn.execute("SELECT job_id FROM jobs").fetchall()
        assert len(rows) == 1
    finally:
        conn.close()


def test_rejected_duplicate_does_not_block_verified_listing(tmp_path):
    conn = db.get_db(tmp_path / "rejected-dedupe.db")
    rejected = Job(
        "Example",
        "Senior Engineer",
        "Remote",
        "",
        "https://jobs.example/1",
        "rss",
        "rejected",
        verified=False,
        reject_reason="Senior role",
    )
    verified = Job(
        "Example",
        "Graduate Engineer",
        "Remote",
        "",
        "https://jobs.example/1?utm_source=ats",
        "greenhouse",
        "verified",
        verified=True,
    )
    try:
        db.save_jobs(conn, [rejected])

        assert db.save_jobs(conn, [verified]) == 1
        assert [job.title for job in db.get_verified_jobs(conn)] == ["Graduate Engineer"]
        duplicate = Job(
            "Example",
            "Graduate Engineer",
            "Remote",
            "",
            "https://jobs.example/1#apply",
            "manual",
            "duplicate",
            verified=True,
        )
        assert db.save_jobs(conn, [duplicate]) == 0
    finally:
        conn.close()


def test_job_submission_lifecycle_and_duplicate_protection(tmp_path):
    conn = db.get_db(tmp_path / "submissions.db")
    values = {
        "submission_id": "submission123",
        "submitter_user_id": "42",
        "company": "Example Co",
        "title": "Graduate Software Engineer",
        "location": "Remote",
        "description": "Entry-level Python role",
        "apply_url": "https://careers.example/jobs/1?utm_source=chat",
    }
    try:
        assert db.create_job_submission(conn, **values) is True
        assert db.create_job_submission(
            conn,
            **{**values, "submission_id": "submission456", "apply_url": "https://careers.example/jobs/1#apply"},
        ) is False
        pending = db.get_pending_job_submissions(conn)
        assert len(pending) == 1
        assert pending[0]["status"] == "pending"
        assert db.review_job_submission(conn, "submission123", "approved", "7", job_id="job123") is True
        assert db.get_pending_job_submissions(conn) == []
        assert db.review_job_submission(conn, "submission123", "rejected", "7") is False
    finally:
        conn.close()


def test_submit_job_requires_consent_and_queues_valid_job(tmp_path, monkeypatch):
    path = tmp_path / "bot-submission.db"
    original_get_db = db.get_db
    monkeypatch.setattr(bot_main.db, "get_db", lambda: original_get_db(path))
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "700")
    conn = original_get_db(path)
    db.upsert_user(conn, 42, "Student", consent_at="consented")
    conn.close()

    message = SimpleNamespace(
        text=(
            "/submitjob https://careers.example/jobs/1 | "
            "Graduate Software Engineer | Example Co | Remote | Entry-level Python role"
        ),
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=42, first_name="Student"),
    )
    context = SimpleNamespace(args=[
        "https://careers.example/jobs/1", "|", "Graduate", "Software", "Engineer",
        "|", "Example", "Co", "|", "Remote", "|", "Entry-level", "Python", "role",
    ], bot=SimpleNamespace(send_message=AsyncMock()))

    asyncio.run(bot_main.submitjob_cmd(update, context))

    message.reply_text.assert_awaited_once()
    assert "queued for admin review" in message.reply_text.await_args.args[0]
    conn = original_get_db(path)
    try:
        assert len(db.get_pending_job_submissions(conn)) == 1
    finally:
        conn.close()


def test_admin_approval_verifies_before_publishing(tmp_path, monkeypatch):
    path = tmp_path / "approval.db"
    original_get_db = db.get_db
    monkeypatch.setattr(bot_main.db, "get_db", lambda: original_get_db(path))
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "700")
    conn = original_get_db(path)
    db.create_job_submission(
        conn,
        submission_id="submission123",
        submitter_user_id="42",
        company="Example Co",
        title="Graduate Software Engineer",
        location="Remote",
        description="Entry-level Python role",
        apply_url="https://careers.example/jobs/1",
    )
    conn.close()

    query = SimpleNamespace(
        data="submission:approve:submission123",
        from_user=SimpleNamespace(id=700),
        message=SimpleNamespace(chat_id=700),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    asyncio.run(bot_main.on_button_callback(
        SimpleNamespace(callback_query=query),
        context,
    ))

    conn = original_get_db(path)
    try:
        submission = conn.execute(
            "SELECT status FROM job_submissions WHERE submission_id = ?",
            ("submission123",),
        ).fetchone()
        jobs = db.get_verified_jobs(conn)
        assert submission["status"] == "approved"
        assert len(jobs) == 1
        assert jobs[0].source == "manual"
        assert verify_job(jobs[0], allowed_domains=["careers.example"]).verified
    finally:
        conn.close()


def test_non_admin_cannot_review_submissions(monkeypatch):
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "700")
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=42))
    context = SimpleNamespace()

    asyncio.run(bot_main.reviewjobs_cmd(update, context))

    assert "not authorized" in message.reply_text.await_args.args[0].lower()


def test_myid_command_shows_only_callers_numeric_id():
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=123456789),
    )

    asyncio.run(bot_main.myid_cmd(update, SimpleNamespace()))

    assert message.reply_text.await_args.args == (
        "Your Telegram numeric ID is: 123456789",
    )


def test_submission_parser_rejects_non_https_and_private_network_urls():
    assert bot_main._parse_submission_args([
        "http://careers.example/jobs/1", "|", "Graduate Engineer", "|", "Example Co", "|", "Remote",
    ]) is None
    assert bot_main._parse_submission_args([
        "https://127.0.0.1/jobs/1", "|", "Graduate Engineer", "|", "Example Co", "|", "Remote",
    ]) is None


def test_admin_cannot_publish_submission_failing_safety_rules(tmp_path, monkeypatch):
    path = tmp_path / "rejected.db"
    original_get_db = db.get_db
    monkeypatch.setattr(bot_main.db, "get_db", lambda: original_get_db(path))
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "700")
    conn = original_get_db(path)
    db.create_job_submission(
        conn,
        submission_id="submissionbad1",
        submitter_user_id="42",
        company="Example Co",
        title="Senior Software Engineer",
        location="Remote",
        description="Requires a registration fee.",
        apply_url="https://careers.example/jobs/2",
    )
    conn.close()

    query = SimpleNamespace(
        data="submission:approve:submissionbad1",
        from_user=SimpleNamespace(id=700),
        message=SimpleNamespace(chat_id=700),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    asyncio.run(bot_main.on_button_callback(
        SimpleNamespace(callback_query=query),
        context,
    ))

    conn = original_get_db(path)
    try:
        submission = conn.execute(
            "SELECT status FROM job_submissions WHERE submission_id = ?",
            ("submissionbad1",),
        ).fetchone()
        assert submission["status"] == "rejected"
        assert db.get_verified_jobs(conn) == []
    finally:
        conn.close()


def test_delete_user_data_removes_pending_submission_and_anonymizes_approved(tmp_path):
    conn = db.get_db(tmp_path / "submission-privacy.db")
    try:
        for submission_id, url in (
            ("pending12345", "https://careers.example/jobs/1"),
            ("approved1234", "https://careers.example/jobs/2"),
        ):
            db.create_job_submission(
                conn,
                submission_id=submission_id,
                submitter_user_id="42",
                company="Example Co",
                title="Graduate Engineer",
                location="Remote",
                description="",
                apply_url=url,
            )
        db.review_job_submission(conn, "approved1234", "approved", "7", job_id="job123")

        db.delete_user_data(conn, 42)

        pending = db.get_job_submission(conn, "pending12345")
        approved = db.get_job_submission(conn, "approved1234")
        assert pending is None
        assert approved["submitter_user_id"] is None
    finally:
        conn.close()
