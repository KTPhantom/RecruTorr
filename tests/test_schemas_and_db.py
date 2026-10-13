"""Unit tests for Database operations and data lifecycle."""
from pathlib import Path
from datetime import datetime, timedelta, timezone

from src import db
from src.schemas import Job, Profile


def test_db_lifecycle(tmp_path: Path):
    test_db_path = tmp_path / "test.db"
    conn = db.get_db(test_db_path)

    # 1. Upsert user
    db.upsert_user(conn, 12345, "Rohan")
    u = db.get_user(conn, 12345)
    assert u is not None
    assert u["name"] == "Rohan"
    assert u["paused"] == 0

    # 2. Save and retrieve Profile
    prof = Profile(
        user_id="12345",
        name="Rohan",
        grad_year=2026,
        skills=["python", "pytorch"],
        target_roles=["ml", "sde"],
        custom_titles=["iOS Developer"],
        locations=["Pune"],
        work_modes=["hybrid"],
        job_types=["internship"],
        digest_frequency="weekly",
        preferences_configured=True,
    )
    db.save_profile(conn, prof)
    retrieved = db.get_profile(conn, "12345")
    assert retrieved is not None
    assert retrieved.name == "Rohan"
    assert "pytorch" in retrieved.skills
    assert retrieved.custom_titles == ["iOS Developer"]
    assert retrieved.locations == ["Pune"]
    assert retrieved.work_modes == ["hybrid"]
    assert retrieved.job_types == ["internship"]
    assert retrieved.digest_frequency == "weekly"
    assert retrieved.preferences_configured is True

    # 3. Save Jobs
    job = Job(
        company="Razorpay",
        title="Software Engineer Intern",
        location="Bengaluru",
        description="Python backend",
        apply_url="https://jobs.lever.co/razorpay/101",
        source="lever",
        external_id="101",
        verified=True,
    )
    db.save_jobs(conn, [job])
    v_jobs = db.get_verified_jobs(conn)
    assert len(v_jobs) == 1
    assert v_jobs[0].job_id == job.job_id

    # 4. Record application event
    db.record_application_event(conn, "12345", job.job_id, status="applied")
    applied = db.get_user_applied_jobs(conn, "12345")
    assert len(applied) == 1
    assert applied[0]["title"] == "Software Engineer Intern"

    db.record_application_event(
        conn,
        "12345",
        job.job_id,
        status="not_relevant",
        reason="location",
    )
    feedback = db.get_user_negative_feedback(conn, "12345")
    assert feedback["locations"] == ["bengaluru"]
    db.record_application_event(conn, "12345", job.job_id, status="liked")
    feedback = db.get_user_negative_feedback(conn, "12345")
    assert feedback["liked_companies"] == {"razorpay"}
    assert feedback["liked_titles"] == ["software engineer intern"]

    # 5. Persist and send reminders even when the bot process was asleep.
    due_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    db.schedule_reminder(conn, "12345", job.job_id, due_at)
    due_reminders = db.get_due_reminders(conn)
    assert len(due_reminders) == 1
    assert due_reminders[0]["title"] == "Software Engineer Intern"
    db.mark_reminder_sent(conn, "12345", job.job_id)
    assert db.get_due_reminders(conn) == []

    # 6. Delete user data (GDPR / privacy compliance)
    db.schedule_reminder(conn, "12345", job.job_id, due_at)
    db.delete_user_data(conn, 12345)
    assert db.get_user(conn, 12345) is None
    assert db.get_profile(conn, "12345") is None
    assert len(db.get_user_applied_jobs(conn, "12345")) == 0
    assert db.get_due_reminders(conn) == []
