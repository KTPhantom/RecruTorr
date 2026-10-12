"""Interactive CLI Simulator for OffCampus Matcher.

Allows testing the entire student flow (Consent -> Resume -> Profile -> Jobs -> Actions -> Applied)
locally without requiring an active Telegram connection.

Usage:
    python -m bot.simulate
"""
from __future__ import annotations

import json
import sys
import time
from typing import Optional

from src import db, tracker
from src.delivery import format_job_card
from src.explainer import explain_match
from src.matcher import match_for_user
from src.resume_parser import heuristic_parse_resume, parse_resume
from src.schemas import Profile


def simulate_cli() -> None:
    conn = db.get_db()
    sim_uid = 999999

    print("=" * 60)
    print("🤖 OffCampus Matcher — Interactive CLI Simulation Mode")
    print("=" * 60)

    # 1. Start & Consent
    print("\n[Bot]: Welcome to OffCampus Matcher!")
    print("Do you consent to store your resume details for job matching? (yes/no)")
    choice = input("Your choice (default 'yes'): ").strip().lower() or "yes"
    if choice != "yes":
        print("[Bot]: No problem. Nothing was recorded. Exiting.")
        return

    db.upsert_user(conn, sim_uid, name="Demo Student")
    print("[Bot]: Consent accepted! You are registered.")

    # 2. Resume Onboarding
    print("\n[Bot]: Now let's set up your profile.")
    print("Options:")
    print("  1. Enter skills & target roles interactively")
    print("  2. Load sample fresher resume (Python, React, SQL, FastAPI)")
    resume_choice = input("Select option (1 or 2, default 2): ").strip() or "2"

    if resume_choice == "1":
        name = input("Enter your name (default: Demo Student): ").strip() or "Demo Student"
        deg = input("Enter degree (default: B.Tech): ").strip() or "B.Tech"
        grad = int(input("Enter graduation year (default: 2026): ").strip() or "2026")
        skills_input = input("Enter skills separated by commas (e.g. python, react, sql): ").strip()
        skills = [s.strip().lower() for s in skills_input.split(",") if s.strip()]
        roles_input = input("Target roles (e.g. sde, backend, ml): ").strip()
        roles = [r.strip().lower() for r in roles_input.split(",") if r.strip()] or ["sde"]
        locs_input = input("Preferred locations (e.g. bangalore, remote): ").strip()
        locs = [l.strip().lower() for l in locs_input.split(",") if l.strip()] or ["remote", "bangalore"]

        profile = Profile(
            user_id=str(sim_uid),
            name=name,
            degree=deg,
            grad_year=grad,
            skills=skills,
            target_roles=roles,
            locations=locs,
        )
    else:
        sample_resume_text = """
        Aman Verma
        B.Tech Computer Science, 2026 graduate.
        Skills: Python, FastAPI, React, SQL, Docker, Linux, Git.
        Projects: Built an API gateway using FastAPI and Postgres.
        Interests: SDE, Backend Developer, Full Stack Engineer.
        """
        profile = heuristic_parse_resume(sample_resume_text, user_id=str(sim_uid))
        profile.locations = ["remote", "bangalore"]

    db.save_profile(conn, profile)
    print("\n✅ Profile Saved:")
    print(f"• Name: {profile.name} ({profile.degree}, {profile.grad_year})")
    print(f"• Skills ({len(profile.skills)}): {', '.join(profile.skills)}")
    print(f"• Roles: {', '.join(profile.target_roles)}")
    print(f"• Locations: {', '.join(profile.locations)}")

    # 3. Interactive Command Loop
    while True:
        print("\n" + "-" * 50)
        print("Available Commands:")
        print("  1. /jobs    - Find and review today's verified matches")
        print("  2. /profile - View current extracted profile")
        print("  3. /applied - View jobs you have marked as applied")
        print("  4. /delete  - Wipe all personal data")
        print("  5. exit     - Exit simulation")
        cmd = input("\nEnter command: ").strip().lower()

        if cmd in ["1", "/jobs", "jobs"]:
            v_jobs = db.get_verified_jobs(conn)
            if not v_jobs:
                print("\n[Bot]: No verified jobs in database! Run 'python -m src.run_fetch' first.")
                continue

            seen_rows = conn.execute(
                "SELECT job_id FROM applications WHERE user_id = ?",
                (str(sim_uid),),
            ).fetchall()
            seen_ids = {r["job_id"] for r in seen_rows}
            neg_feedback = db.get_user_negative_feedback(conn, str(sim_uid))

            matches = match_for_user(
                profile=profile,
                verified_jobs=v_jobs,
                already_seen_ids=seen_ids,
                negative_feedback=neg_feedback,
                limit=3,
            )

            if not matches:
                print("\n[Bot]: No new matches found today (or you have reviewed them all).")
                continue

            print(f"\n[Bot]: Found {len(matches)} top match(es):\n")
            for i, (job, score, m_skills, miss_skills) in enumerate(matches, 1):
                fit = explain_match(profile, job, m_skills, miss_skills)
                card = format_job_card(job, fit)
                print(f"--- MATCH #{i} (Score: {score:.2f}) ---")
                print(card)
                print(f"Apply URL: {job.apply_url}")
                print("\nOptions: [a] Applied | [n] Not Relevant | [r] Remind Me | [s] Skip")
                act = input("Action for this job: ").strip().lower()

                if act == "a":
                    tracker.record_feedback(conn, str(sim_uid), job.job_id, "applied")
                    print("🎉 Marked as applied!")
                elif act == "n":
                    tracker.record_feedback(conn, str(sim_uid), job.job_id, "not_relevant")
                    print("👍 Recorded negative feedback. Similar roles will be penalized.")
                elif act == "r":
                    tracker.record_feedback(conn, str(sim_uid), job.job_id, "remind")
                    print("⏰ Reminder recorded.")
                else:
                    tracker.record_feedback(conn, str(sim_uid), job.job_id, "sent")
                    print("Skipped.")
                print()

        elif cmd in ["2", "/profile", "profile"]:
            p = db.get_profile(conn, str(sim_uid))
            if p:
                print(f"\n👤 Profile for {p.name}:")
                print(f"• Degree: {p.degree} ({p.grad_year})")
                print(f"• Skills: {', '.join(p.skills)}")
                print(f"• Target Roles: {', '.join(p.target_roles)}")
                print(f"• Locations: {', '.join(p.locations)}")
            else:
                print("\nNo profile found.")

        elif cmd in ["3", "/applied", "applied"]:
            applied_rows = db.get_user_applied_jobs(conn, str(sim_uid))
            if not applied_rows:
                print("\n[Bot]: You haven't marked any jobs as applied yet.")
            else:
                print(f"\n📋 You have applied to {len(applied_rows)} job(s):")
                for r in applied_rows:
                    print(f"• {r['title']} at {r['company']} (Applied: {r['updated_at'][:10]})")
                    print(f"  URL: {r['apply_url']}")

        elif cmd in ["4", "/delete", "delete"]:
            confirm = input("Are you sure you want to delete all user data? (yes/no): ").strip().lower()
            if confirm == "yes":
                db.delete_user_data(conn, sim_uid)
                print("\n🗑️ All data deleted permanently. Exiting simulation.")
                break

        elif cmd in ["5", "exit", "quit", "q"]:
            print("\nExiting simulator. Good luck with off-campus placements!")
            break


if __name__ == "__main__":
    simulate_cli()
