"""Telegram bot implementation for OffCampus Matcher.

Features:
- Consent check (/start)
- Resume PDF upload & parsing into Profile (/profile)
- Live preference customizer (/preferences)
- Verified job matching with OpenRouter fit notes (/jobs)
- In-place button feedback updates (Applied, Not Relevant, Remind Me)
- Scheduled 24-hour reminder alerts (via JobQueue)
- Automated daily morning digest (via JobQueue)
- Application tracker (/applied)
- How it works guide (/help)
- Pause/resume digests (/pause, /resume)
- GDPR-style complete data deletion (/delete)
- Auto-registered bot commands menu
"""
from __future__ import annotations

import asyncio
import datetime
import hmac
import io
import json
import logging
import os
import sys
import time
from typing import Any, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import uvicorn

from src import db, tracker
from src.delivery import build_job_keyboard, format_job_card
from src.explainer import explain_match
from src.matcher import match_for_user
from src.orchestrator import fetch_and_verify_all
from src.resume_parser import parse_resume
from src.schemas import Profile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

CONSENT_TEXT = (
    "👋 *Welcome to OffCampus Matcher!*\n\n"
    "This bot finds and delivers authentic, verified off-campus job and internship openings tailored to your resume.\n\n"
    "🔒 *Privacy Notice:*\n"
    "• We only extract skills, projects, and role preferences from your resume.\n"
    "• Your data is used exclusively for job matching and is never sold or shared.\n"
    "• You can permanently delete all your data at any time with `/delete`.\n\n"
    "Do you consent to proceed?"
)

HELP_TEXT = (
    "📖 *How OffCampus Matcher Works:*\n\n"
    "1️⃣ *Zero-Scam Verification:* We fetch directly from official company ATS systems (Greenhouse, Lever, Ashby). "
    "Every job is checked by our deterministic verifier to filter out fee-based scams, senior roles (>3 yrs), and stale postings.\n\n"
    "2️⃣ *AI-Powered Fit Notes:* For every matched job, our AI reviews your resume skills and highlights *Matched Skills* and *Missing Skills*.\n\n"
    "3️⃣ *Smart Feedback Loop:* Tapping *❌ Not relevant* immediately down-ranks similar roles and companies for your future digests.\n\n"
    "4️⃣ *Reminders & Tracking:* Tap *⏰ Remind me* to receive an automated ping in 24 hours, and track submissions with `/applied`.\n\n"
    "📌 *Commands Available:*\n"
    "• `/jobs` — Browse today's fresh verified matches\n"
    "• `/preferences` — Toggle your target roles & locations\n"
    "• `/profile` — View or update your resume details\n"
    "• `/applied` — View all jobs you've marked as applied\n"
    "• `/pause` & `/resume` — Control your daily digests\n"
    "• `/delete` — Permanently erase all your personal data"
)


# ---------- Command Handlers ----------

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ I agree", callback_data="consent:yes"),
                InlineKeyboardButton("❌ No thanks", callback_data="consent:no"),
            ]
        ]
    )
    await update.message.reply_text(CONSENT_TEXT, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


async def profile_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    uid = update.effective_user.id
    conn = db.get_db()
    user = db.get_user(conn, uid)

    if not user or not user["consent_at"]:
        await update.message.reply_text("Please run /start and accept the privacy notice first.")
        return

    profile = db.get_profile(conn, str(uid))
    if not profile:
        await update.message.reply_text(
            "📄 *No resume uploaded yet!*\n\n"
            "Please send me your resume PDF directly in this chat, and I will extract your skills and preferences.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    skills_str = ", ".join(profile.skills) if profile.skills else "None detected"
    roles_str = ", ".join(profile.target_roles) if profile.target_roles else "SDE"
    loc_str = ", ".join(profile.locations) if profile.locations else "Any / Remote"

    msg = (
        f"👤 *Your Current Profile:*\n\n"
        f"• *Name:* {profile.name or 'Student'}\n"
        f"• *Degree:* {profile.degree}\n"
        f"• *Grad Year:* {profile.grad_year}\n"
        f"• *Target Roles:* {roles_str}\n"
        f"• *Locations:* {loc_str}\n"
        f"• *Extracted Skills ({len(profile.skills)}):* {skills_str}\n\n"
        f"💡 _Tip: Use `/preferences` to customize locations & roles, or upload a new PDF anytime to refresh._"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


def build_preferences_keyboard(profile: Profile) -> InlineKeyboardMarkup:
    """Build toggle buttons for location and role preferences."""
    all_locations = ["remote", "bangalore", "delhi", "hyderabad", "pune"]
    all_roles = ["sde", "backend", "frontend", "ml", "data"]

    current_locs = {loc.lower() for loc in profile.locations}
    current_roles = {role.lower() for role in profile.target_roles}

    # Location buttons row
    loc_buttons = []
    for loc in all_locations:
        active = loc in current_locs
        label = f"{'✅' if active else '➕'} {loc.capitalize()}"
        loc_buttons.append(InlineKeyboardButton(label, callback_data=f"pref:loc:{loc}"))

    # Role buttons row
    role_buttons = []
    for r in all_roles:
        active = r in current_roles
        label = f"{'✅' if active else '➕'} {r.upper()}"
        role_buttons.append(InlineKeyboardButton(label, callback_data=f"pref:role:{r}"))

    # Split into 2 buttons per row for nice UI
    kb = [
        [loc_buttons[0], loc_buttons[1]],
        [loc_buttons[2], loc_buttons[3]],
        [loc_buttons[4]],
        [InlineKeyboardButton("--- Target Roles ---", callback_data="noop")],
        [role_buttons[0], role_buttons[1]],
        [role_buttons[2], role_buttons[3]],
        [role_buttons[4]],
        [InlineKeyboardButton("🔍 Done, Show My Matches (/jobs)", callback_data="pref:done")],
    ]
    return InlineKeyboardMarkup(kb)


async def preferences_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    uid = update.effective_user.id
    conn = db.get_db()
    profile = db.get_profile(conn, str(uid))
    if not profile:
        profile = Profile(user_id=str(uid), name=update.effective_user.first_name or "Student")
        db.save_profile(conn, profile)

    kb = build_preferences_keyboard(profile)
    text = (
        "⚙️ *Customize Your Preferences:*\n\n"
        "Tap the buttons below to toggle your preferred locations and target role families. "
        "Your matches will update immediately!"
    )
    await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


async def jobs_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    uid = update.effective_user.id
    conn = db.get_db()
    user = db.get_user(conn, uid)

    if not user or not user["consent_at"]:
        await update.message.reply_text("Please run /start and accept the consent notice first.")
        return

    profile = db.get_profile(conn, str(uid))
    if not profile:
        profile = Profile(user_id=str(uid), name=update.effective_user.first_name or "Student")

    verified_jobs = db.get_verified_jobs(conn)
    if not verified_jobs:
        await update.message.reply_text("Refreshing verified job listings now. Please wait a moment...")
        await asyncio.to_thread(refresh_job_listings)
        verified_jobs = db.get_verified_jobs(conn)
        if not verified_jobs:
            await update.message.reply_text("No verified jobs are available right now. Please try again later.")
            return

    seen_rows = conn.execute(
        "SELECT job_id FROM applications WHERE user_id = ?",
        (str(uid),),
    ).fetchall()
    seen_ids = {r["job_id"] for r in seen_rows}

    negative_feedback = db.get_user_negative_feedback(conn, str(uid))

    matches = match_for_user(
        profile=profile,
        verified_jobs=verified_jobs,
        already_seen_ids=seen_ids,
        negative_feedback=negative_feedback,
        limit=5,
    )

    if not matches:
        await update.message.reply_text(
            "✨ *You're all caught up!* No new unreviewed matches right now.\n"
            "We check for new openings every morning at 9:00 AM IST. Check back tomorrow!"
        )
        return

    await update.message.reply_text(f"🔍 Found {len(matches)} fresh verified match(es) for you:")

    now_str = time.strftime("%Y-%m-%dT%H:%M:%S")
    for job, score, matched_skills, missing_skills in matches:
        fit = explain_match(profile, job, matched_skills, missing_skills)
        card_text = format_job_card(job, fit)
        kb = build_job_keyboard(job)

        try:
            await update.message.reply_text(card_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await update.message.reply_text(card_text.replace("*", "").replace("_", ""), reply_markup=kb)

        conn.execute(
            """INSERT INTO matches (user_id, job_id, score, fit_json, sent_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, job_id) DO UPDATE SET
                   score = excluded.score,
                   fit_json = excluded.fit_json,
                   sent_at = excluded.sent_at""",
            (str(uid), job.job_id, score, json.dumps(fit.to_dict()), now_str),
        )
        db.record_application_event(conn, str(uid), job.job_id, status="sent")

    conn.commit()


async def applied_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    uid = update.effective_user.id
    conn = db.get_db()
    rows = db.get_user_applied_jobs(conn, str(uid))

    if not rows:
        await update.message.reply_text(
            "You haven't marked any jobs as applied yet.\n"
            "When you apply to an opening from /jobs, tap the '✅ Applied' button to track it here!"
        )
        return

    lines = [f"• *{r['title']}* at *{r['company']}* ({r['updated_at'][:10]})\n  [Link]({r['apply_url']})" for r in rows]
    await update.message.reply_text(
        f"📋 *Jobs you have applied to ({len(rows)}):*\n\n" + "\n\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


async def pause_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    conn = db.get_db()
    db.set_user_paused(conn, update.effective_user.id, True)
    await update.message.reply_text("⏸ Digests paused. Use /resume when you're ready to receive jobs again.")


async def resume_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    conn = db.get_db()
    db.set_user_paused(conn, update.effective_user.id, False)
    await update.message.reply_text("▶️ Digests resumed! You'll receive daily verified matches every morning.")


async def delete_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚠️ Yes, delete everything", callback_data="delete:yes"),
                InlineKeyboardButton("Cancel", callback_data="delete:no"),
            ]
        ]
    )
    await update.message.reply_text(
        "⚠️ *Are you sure you want to delete all your data?*\n\n"
        "This will permanently erase your profile, extracted resume skills, match history, and application records.",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------- Document Handler (Resume PDF) ----------

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.document:
        return

    doc = update.message.document
    file_name = doc.file_name or ""
    if not file_name.lower().endswith(".pdf"):
        await update.message.reply_text("Please upload a PDF file (.pdf).")
        return

    uid = update.effective_user.id
    conn = db.get_db()
    user = db.get_user(conn, uid)
    if not user or not user["consent_at"]:
        await update.message.reply_text("Please run /start and accept the privacy notice before uploading.")
        return

    status_msg = await update.message.reply_text("📥 Analyzing and parsing your resume with AI...")

    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        buffer = io.BytesIO()
        await tg_file.download_to_memory(buffer)
        buffer.seek(0)

        profile = parse_resume(
            pdf_source=buffer,
            user_id=str(uid),
            form_answers={"name": update.effective_user.first_name or "Student"},
        )
        db.save_profile(conn, profile)

        skills_summary = ", ".join(profile.skills[:8]) if profile.skills else "None detected"
        confirmation_text = (
            f"✅ *Resume Parsed Successfully!*\n\n"
            f"• *Name:* {profile.name}\n"
            f"• *Degree:* {profile.degree}\n"
            f"• *Grad Year:* {profile.grad_year}\n"
            f"• *Target Roles:* {', '.join(profile.target_roles)}\n"
            f"• *Locations:* {', '.join(profile.locations)}\n"
            f"• *Extracted Skills:* {skills_summary} (total {len(profile.skills)})\n\n"
            f"🎯 Type `/jobs` to view your personalized matches, or `/preferences` to customize filters!"
        )
        await status_msg.edit_text(confirmation_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as exc:
        log.error("Failed to process resume upload: %s", exc)
        await status_msg.edit_text(f"❌ Failed to parse resume: {exc}. Please try again.")


# ---------- Daily Morning Digest Worker ----------

def refresh_job_listings() -> dict[str, Any]:
    """Fetch and verify current listings using the bot's configured database."""
    conn = db.get_db()
    try:
        return fetch_and_verify_all(conn)
    finally:
        conn.close()


async def scheduled_daily_digest(bot: Any) -> None:
    """Refresh listings and send the daily digest to active students."""
    fetch_result = await asyncio.to_thread(refresh_job_listings)
    log.info(
        "Daily job refresh completed: fetched=%d verified=%d rejected=%d errors=%d",
        fetch_result["fetched"],
        fetch_result["verified"],
        fetch_result["rejected"],
        len(fetch_result["errors"]),
    )
    conn = db.get_db()
    active_users = db.get_all_active_users(conn)
    verified_jobs = db.get_verified_jobs(conn)

    log.info("Running automated daily morning digest for %d users...", len(active_users))

    for u in active_users:
        uid = u["telegram_id"]
        profile = db.get_profile(conn, str(uid))
        if not profile:
            continue

        seen_rows = conn.execute(
            "SELECT job_id FROM applications WHERE user_id = ?",
            (str(uid),),
        ).fetchall()
        seen_ids = {r["job_id"] for r in seen_rows}
        negative_feedback = db.get_user_negative_feedback(conn, str(uid))

        matches = match_for_user(
            profile=profile,
            verified_jobs=verified_jobs,
            already_seen_ids=seen_ids,
            negative_feedback=negative_feedback,
            limit=3,
        )

        if not matches:
            continue

        try:
            await bot.send_message(
                chat_id=uid,
                text=f"🌅 *Good morning!* Here are {len(matches)} fresh verified openings matching your resume:",
                parse_mode=ParseMode.MARKDOWN,
            )
            now_str = time.strftime("%Y-%m-%dT%H:%M:%S")
            for job, score, matched_skills, missing_skills in matches:
                fit = explain_match(profile, job, matched_skills, missing_skills)
                card_text = format_job_card(job, fit)
                kb = build_job_keyboard(job)
                await bot.send_message(
                    chat_id=uid,
                    text=card_text,
                    reply_markup=kb,
                    parse_mode=ParseMode.MARKDOWN,
                )
                conn.execute(
                    """INSERT INTO matches (user_id, job_id, score, fit_json, sent_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, job_id) DO UPDATE SET
                           score = excluded.score,
                           fit_json = excluded.fit_json,
                           sent_at = excluded.sent_at""",
                    (str(uid), job.job_id, score, json.dumps(fit.to_dict()), now_str),
                )
                db.record_application_event(conn, str(uid), job.job_id, status="sent")
            conn.commit()
        except Exception as exc:
            log.error("Failed sending daily digest to %s: %s", uid, exc)


async def send_due_reminders(bot: Any) -> int:
    """Deliver reminders persisted in the database, then mark successful sends."""
    conn = db.get_db()
    sent = 0
    try:
        for reminder in db.get_due_reminders(conn):
            msg = (
                "⏰ *Reminder: Don't miss this opportunity!*\n\n"
                "You asked to be reminded about:\n"
                f"🎯 *{reminder['title']}* at *{reminder['company']}*\n"
                f"📍 {reminder['location'] or 'Location not specified'}\n\n"
                f"👉 [Click here to apply now]({reminder['apply_url']})"
            )
            try:
                await bot.send_message(
                    chat_id=int(reminder["user_id"]),
                    text=msg,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as exc:
                log.error("Failed sending reminder to %s: %s", reminder["user_id"], exc)
                continue
            db.mark_reminder_sent(conn, reminder["user_id"], reminder["job_id"])
            sent += 1
    finally:
        conn.close()
    return sent


# ---------- Button Callbacks ----------

async def on_button_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return

    data = q.data or ""
    uid = q.from_user.id
    conn = db.get_db()

    if data == "noop":
        await q.answer()
        return

    kind, _, rest = data.partition(":")

    if kind == "consent":
        await q.answer()
        if rest == "yes":
            db.upsert_user(conn, uid, q.from_user.first_name or "User")
            welcome_msg = (
                "🎉 *Thank you for consenting!*\n\n"
                "Next step: **Upload your resume PDF directly in this chat**.\n\n"
                "Or, type `/preferences` to customize your target roles and locations, or `/jobs` to browse openings right away!"
            )
            await q.edit_message_text(welcome_msg, parse_mode=ParseMode.MARKDOWN)
        else:
            await q.edit_message_text("No problem. No information was recorded.")

    elif kind == "pref":
        sub, _, val = rest.partition(":")
        profile = db.get_profile(conn, str(uid))
        if not profile:
            profile = Profile(user_id=str(uid), name=q.from_user.first_name or "Student")

        if sub == "loc":
            locs = list(profile.locations)
            if val in locs:
                locs.remove(val)
                await q.answer(f"Removed {val.capitalize()} from locations")
            else:
                locs.append(val)
                await q.answer(f"Added {val.capitalize()} to locations")
            profile.locations = locs
            db.save_profile(conn, profile)
            await q.edit_message_reply_markup(reply_markup=build_preferences_keyboard(profile))

        elif sub == "role":
            roles = list(profile.target_roles)
            if val in roles:
                roles.remove(val)
                await q.answer(f"Removed {val.upper()} from target roles")
            else:
                roles.append(val)
                await q.answer(f"Added {val.upper()} to target roles")
            profile.target_roles = roles
            db.save_profile(conn, profile)
            await q.edit_message_reply_markup(reply_markup=build_preferences_keyboard(profile))

        elif sub == "done":
            await q.answer("Preferences saved!")
            await q.edit_message_text(
                "✅ *Preferences updated!*\nType `/jobs` to see newly filtered matches.",
                parse_mode=ParseMode.MARKDOWN,
            )

    elif kind == "act":
        action, _, job_id = rest.partition(":")
        job = db.get_job_by_id(conn, job_id)

        if action == "applied":
            tracker.record_feedback(conn, str(uid), job_id, "applied")
            await q.answer("🎉 Marked as applied! Tracked under /applied", show_alert=False)
            if job and q.message:
                try:
                    await q.edit_message_reply_markup(reply_markup=build_job_keyboard(job, "applied"))
                except Exception:
                    pass

        elif action == "not_relevant":
            tracker.record_feedback(conn, str(uid), job_id, "not_relevant")
            await q.answer("👍 Down-voted! We will show fewer roles like this.", show_alert=False)
            if job and q.message:
                try:
                    await q.edit_message_reply_markup(reply_markup=build_job_keyboard(job, "not_relevant"))
                except Exception:
                    pass

        elif action == "remind":
            tracker.record_feedback(conn, str(uid), job_id, "remind")
            due_at = (
                datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
            ).isoformat(timespec="seconds")
            db.schedule_reminder(conn, str(uid), job_id, due_at)
            await q.answer(
                "⏰ Reminder scheduled! I will ping you within about an hour after 24 hours.",
                show_alert=False,
            )
            if job and q.message:
                try:
                    await q.edit_message_reply_markup(reply_markup=build_job_keyboard(job, "remind"))
                except Exception:
                    pass

    elif kind == "delete":
        await q.answer()
        if rest == "yes":
            db.delete_user_data(conn, uid)
            await q.edit_message_text("🗑️ All your personal data and application records have been permanently erased.")
        else:
            await q.edit_message_text("Data deletion cancelled.")


# ---------- Bot Startup & Command Registration ----------

async def post_init(application: Application) -> None:
    """Register command menu."""
    commands = [
        BotCommand("start", "Start the bot & accept consent"),
        BotCommand("jobs", "Find fresh verified job matches"),
        BotCommand("preferences", "Customize target roles & locations"),
        BotCommand("profile", "View or update your resume details"),
        BotCommand("applied", "View all jobs you marked as applied"),
        BotCommand("help", "How the bot works & guide"),
        BotCommand("pause", "Pause daily morning digests"),
        BotCommand("resume", "Resume daily morning digests"),
        BotCommand("delete", "Permanently erase all your personal data"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        log.info("Registered Telegram command menu successfully.")
    except Exception as exc:
        log.warning("Could not register bot commands menu: %s", exc)

def create_web_app(application: Application, webhook_secret: str, scheduler_secret: str) -> FastAPI:
    web_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @web_app.get("/")
    @web_app.get("/healthz")
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @web_app.post("/telegram")
    async def receive_telegram_update(request: Request) -> dict[str, str]:
        supplied_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(supplied_secret, webhook_secret):
            raise HTTPException(status_code=403, detail="Invalid webhook secret")
        try:
            payload = await request.json()
            update = Update.de_json(payload, application.bot)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid Telegram update") from exc
        await application.update_queue.put(update)
        return {"status": "accepted"}

    @web_app.post("/internal/run-scheduled")
    async def run_scheduled_task(
        request: Request,
        x_scheduler_secret: str = Header(default="", alias="X-Scheduler-Secret"),
    ) -> JSONResponse:
        if not hmac.compare_digest(x_scheduler_secret, scheduler_secret):
            raise HTTPException(status_code=403, detail="Invalid scheduler secret")
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Expected a JSON task") from exc
        task = payload.get("task") if isinstance(payload, dict) else None
        if task == "digest":
            await scheduled_daily_digest(application.bot)
            return JSONResponse({"task": task, "status": "completed"})
        if task == "reminders":
            sent = await send_due_reminders(application.bot)
            return JSONResponse({"task": task, "status": "completed", "sent": sent})
        raise HTTPException(status_code=400, detail="Task must be 'digest' or 'reminders'")

    return web_app


async def serve_webhook(application: Application) -> None:
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    webhook_secret = os.getenv("WEBHOOK_SECRET")
    scheduler_secret = os.getenv("SCHEDULER_SECRET")
    if not hostname or not webhook_secret or not scheduler_secret or not os.getenv("DATABASE_URL"):
        raise RuntimeError(
            "Render webhook mode requires RENDER_EXTERNAL_HOSTNAME, DATABASE_URL, WEBHOOK_SECRET, "
            "and SCHEDULER_SECRET"
        )

    await application.initialize()
    try:
        await post_init(application)
        await application.start()
        await application.bot.set_webhook(
            url=f"https://{hostname}/telegram",
            secret_token=webhook_secret,
            allowed_updates=Update.ALL_TYPES,
        )
        log.info("Telegram webhook registered for %s.", hostname)
        server = uvicorn.Server(
            uvicorn.Config(
                create_web_app(application, webhook_secret, scheduler_secret),
                host="0.0.0.0",
                port=int(os.getenv("PORT", "10000")),
                log_level="info",
            )
        )
        await server.serve()
    finally:
        if application.running:
            await application.stop()
        await application.shutdown()


def run_bot() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        log.error("TELEGRAM_BOT_TOKEN environment variable is not set!")
        sys.exit(1)

    from telegram.request import HTTPXRequest

    proxy_url = os.getenv("TELEGRAM_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    request_kwargs = {
        "connect_timeout": 30.0,
        "read_timeout": 30.0,
        "write_timeout": 30.0,
        "pool_timeout": 30.0,
    }
    if proxy_url:
        log.info("Using proxy for Telegram Bot: %s", proxy_url)
        request_kwargs["proxy"] = proxy_url

    req = HTTPXRequest(**request_kwargs)
    builder = Application.builder().token(token).request(req).post_init(post_init)

    base_url = os.getenv("TELEGRAM_BASE_URL")
    if base_url:
        log.info("Using custom Telegram base URL: %s", base_url)
        builder = builder.base_url(base_url)

    app = builder.build()

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("preferences", preferences_cmd))
    app.add_handler(CommandHandler("jobs", jobs_cmd))
    app.add_handler(CommandHandler("applied", applied_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))

    # Document upload for resumes
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(on_button_callback))

    if os.getenv("RENDER_EXTERNAL_HOSTNAME"):
        log.info("Starting Telegram webhook server...")
        asyncio.run(serve_webhook(app))
    else:
        log.info("Starting Telegram Bot polling listener...")
        app.run_polling()


if __name__ == "__main__":
    run_bot()
