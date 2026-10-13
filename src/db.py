"""Database management and queries for OffCampus Matcher (SQLite with full schema)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.schemas import Job, Profile

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data.db")))
_POSTGRES_SCHEMA_LOCK = threading.Lock()
_POSTGRES_SCHEMA_INITIALIZED = False

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    name TEXT,
    grad_year INTEGER,
    consent_at TEXT,
    paused INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    summary_embedding TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ats_type TEXT NOT NULL,
    board_slug TEXT NOT NULL,
    careers_url TEXT,
    domains TEXT,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    remote INTEGER DEFAULT 0,
    description TEXT,
    apply_url TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    posted_at TEXT,
    verified INTEGER DEFAULT 0,
    reject_reason TEXT,
    fresher_friendly INTEGER DEFAULT 1,
    domains TEXT,
    embedding TEXT,
    fetched_at TEXT,
    first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS matches (
    user_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    score REAL NOT NULL,
    fit_json TEXT,
    sent_at TEXT,
    PRIMARY KEY (user_id, job_id)
);

CREATE TABLE IF NOT EXISTS applications (
    user_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL, -- 'sent', 'applied', 'not_relevant', 'remind'
    reason TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, job_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    fetched INTEGER DEFAULT 0,
    verified INTEGER DEFAULT 0,
    rejected INTEGER DEFAULT 0,
    sent INTEGER DEFAULT 0,
    errors_json TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
    user_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    due_at TEXT NOT NULL,
    sent_at TEXT,
    PRIMARY KEY (user_id, job_id)
);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    name TEXT,
    grad_year INTEGER,
    consent_at TEXT,
    paused INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP::text
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    summary_embedding TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    ats_type TEXT NOT NULL,
    board_slug TEXT NOT NULL,
    careers_url TEXT,
    domains TEXT,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT,
    remote INTEGER DEFAULT 0,
    description TEXT,
    apply_url TEXT NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    posted_at TEXT,
    verified INTEGER DEFAULT 0,
    reject_reason TEXT,
    fresher_friendly INTEGER DEFAULT 1,
    domains TEXT,
    embedding TEXT,
    fetched_at TEXT,
    first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP::text
);

CREATE TABLE IF NOT EXISTS matches (
    user_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    fit_json TEXT,
    sent_at TEXT,
    PRIMARY KEY (user_id, job_id)
);

CREATE TABLE IF NOT EXISTS applications (
    user_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, job_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    fetched INTEGER DEFAULT 0,
    verified INTEGER DEFAULT 0,
    rejected INTEGER DEFAULT 0,
    sent INTEGER DEFAULT 0,
    errors_json TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
    user_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    due_at TEXT NOT NULL,
    sent_at TEXT,
    PRIMARY KEY (user_id, job_id)
);
"""


class PostgresConnection:
    """Small adapter for the SQLite-style execute calls used by the application."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, query: str, parameters: Any = ()) -> Any:
        return self._connection.execute(query.replace("?", "%s"), parameters or ())

    def commit(self) -> None:
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()


def get_db(db_path: Optional[Path] = None) -> Any:
    """Return an initialized database connection (PostgreSQL in deployment, SQLite locally)."""
    global _POSTGRES_SCHEMA_INITIALIZED
    database_url = os.getenv("DATABASE_URL") if db_path is None else None
    if database_url:
        import psycopg
        from psycopg.rows import dict_row

        connection = psycopg.connect(database_url, row_factory=dict_row, connect_timeout=10)
        postgres = PostgresConnection(connection)
        if not _POSTGRES_SCHEMA_INITIALIZED:
            try:
                with _POSTGRES_SCHEMA_LOCK:
                    if not _POSTGRES_SCHEMA_INITIALIZED:
                        for statement in POSTGRES_SCHEMA.split(";"):
                            if statement.strip():
                                postgres.execute(statement)
                        connection.commit()
                        _POSTGRES_SCHEMA_INITIALIZED = True
            except Exception:
                connection.close()
                raise
        return postgres

    p = db_path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript(SQLITE_SCHEMA)
    return conn


# --- User & Profile Helpers ---

def get_user(conn: Any, telegram_id: int) -> Optional[Any]:
    return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()


def upsert_user(conn: Any, telegram_id: int, name: str, consent_at: Optional[str] = None) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        """INSERT INTO users (telegram_id, name, consent_at, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(telegram_id) DO UPDATE SET
               name = excluded.name,
               consent_at = COALESCE(excluded.consent_at, users.consent_at)""",
        (telegram_id, name, consent_at or now, now),
    )
    conn.commit()


def set_user_paused(conn: Any, telegram_id: int, paused: bool) -> None:
    conn.execute("UPDATE users SET paused = ? WHERE telegram_id = ?", (1 if paused else 0, telegram_id))
    conn.commit()


def delete_user_data(conn: Any, telegram_id: int) -> None:
    uid = str(telegram_id)
    conn.execute("DELETE FROM reminders WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM applications WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM matches WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM profiles WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
    conn.commit()


def save_profile(conn: Any, profile: Profile) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    emb_str = json.dumps(profile.summary_embedding) if profile.summary_embedding else None
    conn.execute(
        """INSERT INTO profiles (user_id, profile_json, summary_embedding, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               profile_json = excluded.profile_json,
               summary_embedding = excluded.summary_embedding,
               updated_at = excluded.updated_at""",
        (profile.user_id, json.dumps(profile.to_dict()), emb_str, now),
    )
    conn.commit()


def get_profile(conn: Any, user_id: str) -> Optional[Profile]:
    row = conn.execute("SELECT profile_json FROM profiles WHERE user_id = ?", (str(user_id),)).fetchone()
    if not row:
        return None
    data = json.loads(row["profile_json"])
    return Profile.from_dict(data)


def get_all_active_users(conn: Any) -> List[Any]:
    return conn.execute("SELECT * FROM users WHERE consent_at IS NOT NULL AND paused = 0").fetchall()


# --- Job Helpers ---

def save_jobs(conn: Any, jobs: List[Job], domains: Optional[List[str]] = None) -> None:
    for j in jobs:
        d = j.to_dict()
        emb_str = json.dumps(j.embedding) if j.embedding else None
        domains_str = json.dumps(domains or [])
        conn.execute(
            """INSERT INTO jobs (
                   job_id, company, title, location, remote, description,
                   apply_url, source, external_id, posted_at, verified,
                   reject_reason, fresher_friendly, domains, embedding, fetched_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO UPDATE SET
                   title = excluded.title,
                   location = excluded.location,
                   remote = excluded.remote,
                   description = excluded.description,
                   apply_url = excluded.apply_url,
                   posted_at = excluded.posted_at,
                   verified = excluded.verified,
                   reject_reason = excluded.reject_reason,
                   fresher_friendly = excluded.fresher_friendly,
                   domains = excluded.domains,
                   embedding = COALESCE(excluded.embedding, jobs.embedding),
                   fetched_at = excluded.fetched_at""",
            (
                d["job_id"], d["company"], d["title"], d["location"], int(d["remote"]),
                d["description"], d["apply_url"], d["source"], d["external_id"],
                d["posted_at"], int(d["verified"]), d["reject_reason"],
                int(d["fresher_friendly"]) if d["fresher_friendly"] is not None else 1,
                domains_str, emb_str, d["fetched_at"],
            ),
        )
    conn.commit()


def get_verified_jobs(conn: Any) -> List[Job]:
    rows = conn.execute("SELECT * FROM jobs WHERE verified = 1").fetchall()
    return [Job.from_row(r) for r in rows]


def get_job_by_id(conn: Any, job_id: str) -> Optional[Job]:
    row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if not row:
        return None
    return Job.from_row(row)


# --- Application & Tracking Helpers ---

def record_application_event(
    conn: Any,
    user_id: str,
    job_id: str,
    status: str,
    reason: Optional[str] = None
) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        """INSERT INTO applications (user_id, job_id, status, reason, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user_id, job_id) DO UPDATE SET
               status = excluded.status,
               reason = COALESCE(excluded.reason, applications.reason),
               updated_at = excluded.updated_at""",
        (str(user_id), job_id, status, reason, now),
    )
    conn.commit()


def get_user_negative_feedback(conn: Any, user_id: str) -> Dict[str, Any]:
    """Retrieve negative and positive job-card feedback for ranking."""
    rows = conn.execute(
        """SELECT j.company, j.title, j.location, a.reason FROM applications a
           JOIN jobs j ON a.job_id = j.job_id
           WHERE a.user_id = ? AND a.status = 'not_relevant'""",
        (str(user_id),),
    ).fetchall()
    liked_rows = conn.execute(
        """SELECT j.company, j.title FROM applications a
           JOIN jobs j ON a.job_id = j.job_id
           WHERE a.user_id = ? AND a.status = 'liked'""",
        (str(user_id),),
    ).fetchall()
    return {
        "companies": {
            r["company"].lower()
            for r in rows
            if r["reason"] in {None, "other"}
        },
        "titles": [r["title"].lower() for r in rows],
        "locations": [
            r["location"].lower()
            for r in rows
            if r["reason"] == "location" and r["location"]
        ],
        "liked_companies": {r["company"].lower() for r in liked_rows},
        "liked_titles": [r["title"].lower() for r in liked_rows],
    }


def get_user_applied_jobs(conn: Any, user_id: str) -> List[Any]:
    return conn.execute(
        """SELECT j.title, j.company, j.apply_url, a.updated_at FROM applications a
           JOIN jobs j ON a.job_id = j.job_id
           WHERE a.user_id = ? AND a.status = 'applied'
           ORDER BY a.updated_at DESC""",
        (str(user_id),),
    ).fetchall()


def schedule_reminder(conn: Any, user_id: str, job_id: str, due_at: str) -> None:
    conn.execute(
        """INSERT INTO reminders (user_id, job_id, due_at, sent_at)
           VALUES (?, ?, ?, NULL)
           ON CONFLICT(user_id, job_id) DO UPDATE SET
               due_at = excluded.due_at,
               sent_at = NULL""",
        (str(user_id), job_id, due_at),
    )
    conn.commit()


def get_due_reminders(conn: Any) -> List[Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return conn.execute(
        """SELECT r.user_id, r.job_id, j.title, j.company, j.location, j.apply_url
           FROM reminders r JOIN jobs j ON r.job_id = j.job_id
           WHERE r.sent_at IS NULL AND r.due_at <= ?
           ORDER BY r.due_at""",
        (now,),
    ).fetchall()


def mark_reminder_sent(conn: Any, user_id: str, job_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE reminders SET sent_at = ? WHERE user_id = ? AND job_id = ? AND sent_at IS NULL",
        (now, str(user_id), job_id),
    )
    conn.commit()
