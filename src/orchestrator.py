"""Agent 8: Orchestrator

Coordinates the end-to-end pipeline:
1. Read companies.yaml
2. Fetch jobs from Greenhouse, Lever, Ashby
3. Run Verifier (deterministic rules)
4. Store verified & rejected jobs in the database
5. For each active user: Match -> Explain -> Deliver
6. Record execution summary in `runs` table
One failing source never stops the run.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src import db
from src.delivery import build_job_keyboard, format_job_card
from src.explainer import explain_match
from src.fetchers import ashby, greenhouse, lever
from src.matcher import match_for_user
from src.schemas import Job, Profile
from src.verifier import verify_and_deduplicate

log = logging.getLogger("orchestrator")

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "companies.yaml"


def fetch_and_verify_all(conn: Any, companies_config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Fetch from all configured sources and verify against rules."""
    c_path = companies_config_path or COMPANIES_FILE
    if not c_path.exists():
        log.warning("Companies config file not found: %s", c_path)
        return {"fetched": 0, "verified": 0, "rejected": 0, "errors": []}

    config = yaml.safe_load(c_path.read_text()) or {}
    companies = config.get("companies", [])

    total_fetched = 0
    total_verified = 0
    total_rejected = 0
    errors = []

    for c in companies:
        name = c.get("name")
        ats = c.get("ats")
        slug = c.get("slug")
        domains = c.get("domains", [])
        if not name or not ats or not slug:
            continue

        raw_jobs: List[Job] = []
        try:
            if ats == "greenhouse":
                raw_jobs = greenhouse.fetch(name, slug)
            elif ats == "lever":
                raw_jobs = lever.fetch(name, slug, eu=c.get("eu", False))
            elif ats == "ashby":
                raw_jobs = ashby.fetch(name, slug)
            else:
                raise ValueError(f"Unsupported ATS type '{ats}'")
            total_fetched += len(raw_jobs)
        except Exception as exc:
            log.error("Failed fetching %s (%s): %s", name, ats, exc)
            errors.append({"company": name, "ats": ats, "error": str(exc)})
            continue

        # Run Verifier on raw jobs
        verified_batch = verify_and_deduplicate(raw_jobs, allowed_domains=domains)
        db.save_jobs(conn, verified_batch, domains=domains)

        v_count = sum(1 for j in verified_batch if j.verified)
        r_count = len(verified_batch) - v_count
        total_verified += v_count
        total_rejected += r_count

        time.sleep(0.3)  # Rate limiting courtesy

    return {
        "fetched": total_fetched,
        "verified": total_verified,
        "rejected": total_rejected,
        "errors": errors,
    }


async def deliver_matches_for_users(conn: Any, bot_app: Optional[Any] = None) -> int:
    """Run Matcher and Explainer for each active user and deliver digest."""
    active_users = db.get_all_active_users(conn)
    verified_jobs = db.get_verified_jobs(conn)

    total_sent = 0
    now_str = time.strftime("%Y-%m-%dT%H:%M:%S")

    for u in active_users:
        telegram_id = u["telegram_id"]
        profile = db.get_profile(conn, str(telegram_id))
        if not profile:
            continue

        # Get already seen job ids for this user
        seen_rows = conn.execute(
            "SELECT job_id FROM applications WHERE user_id = ?",
            (str(telegram_id),),
        ).fetchall()
        seen_ids = {r["job_id"] for r in seen_rows}

        # User negative feedback for ranking
        feedback = db.get_user_negative_feedback(conn, str(telegram_id))

        # Match top jobs
        top_matches = match_for_user(
            profile=profile,
            verified_jobs=verified_jobs,
            already_seen_ids=seen_ids,
            negative_feedback=feedback,
            limit=5,
        )

        for job, score, matched_skills, missing_skills in top_matches:
            fit = explain_match(profile, job, matched_skills, missing_skills)

            # Record match in DB
            conn.execute(
                """INSERT INTO matches (user_id, job_id, score, fit_json, sent_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, job_id) DO UPDATE SET
                       score = excluded.score,
                       fit_json = excluded.fit_json,
                       sent_at = excluded.sent_at""",
                (str(telegram_id), job.job_id, score, json.dumps(fit.to_dict()), now_str),
            )
            # Mark application state as 'sent'
            db.record_application_event(conn, str(telegram_id), job.job_id, status="sent")

            # Send via telegram bot if bot instance provided
            if bot_app:
                try:
                    text = format_job_card(job, fit)
                    kb = build_job_keyboard(job)
                    await bot_app.bot.send_message(
                        chat_id=telegram_id,
                        text=text,
                        reply_markup=kb,
                        parse_mode="Markdown",
                    )
                    total_sent += 1
                except Exception as exc:
                    log.error("Failed sending job %s to user %s: %s", job.job_id, telegram_id, exc)
            else:
                total_sent += 1

    conn.commit()
    return total_sent


async def run_pipeline(bot_app: Optional[Any] = None) -> Dict[str, Any]:
    """Master entry point for Agent 8: Orchestrator."""
    conn = db.get_db()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    log.info("Starting Orchestrator pipeline run at %s...", started_at)

    fetch_res = fetch_and_verify_all(conn)
    sent_count = await deliver_matches_for_users(conn, bot_app)

    finished_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    conn.execute(
        """INSERT INTO runs (started_at, finished_at, fetched, verified, rejected, sent, errors_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            started_at,
            finished_at,
            fetch_res["fetched"],
            fetch_res["verified"],
            fetch_res["rejected"],
            sent_count,
            json.dumps(fetch_res["errors"]),
        ),
    )
    conn.commit()

    summary = {
        "started_at": started_at,
        "finished_at": finished_at,
        "fetched": fetch_res["fetched"],
        "verified": fetch_res["verified"],
        "rejected": fetch_res["rejected"],
        "sent": sent_count,
        "errors": fetch_res["errors"],
    }
    log.info("Orchestrator pipeline finished: %s", summary)
    return summary
