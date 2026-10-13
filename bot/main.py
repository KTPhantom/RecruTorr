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
import ipaddress
import json
import logging
import os
import sys
import time
import uuid
from typing import Any, List, Optional
from urllib.parse import urlsplit

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
from src.schemas import Job, Profile, html_to_text, looks_remote
from src.verifier import verify_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")
_job_refresh_task: Optional[asyncio.Task[Any]] = None

CONSENT_TEXT = (
    "👋 *Welcome to OffCampus Matcher!*\n\n"
    "This bot finds and delivers authentic, verified off-campus job and internship openings tailored to your resume.\n\n"
    "🔒 *Privacy Notice:*\n"
    "• We only extract skills, projects, and role preferences from your resume.\n"
    "• Your data is used exclusively for job matching and is never sold or shared.\n"
    "• Job links you submit are shared with configured moderators; approved public listings may be retained without your account details.\n"
    "• You can permanently delete all your data at any time with `/delete`.\n\n"
    "Do you consent to proceed?"
)

HELP_TEXT = (
    "📖 *How OffCampus Matcher Works:*\n\n"
    "1️⃣ *Zero-Scam Verification:* We fetch from configured company ATS boards and RSS/Atom feeds. "
    "Community-submitted links are reviewed by a moderator before they can be published. "
    "Jobs are checked for fee-based scams, senior roles (>3 yrs), and stale postings.\n\n"
    "2️⃣ *AI-Powered Fit Notes:* For every matched job, our AI reviews your resume skills and highlights *Matched Skills* and *Missing Skills*.\n\n"
    "3️⃣ *Smart Feedback Loop:* Tapping *❌ Not relevant* immediately down-ranks similar roles and companies for your future digests.\n\n"
    "4️⃣ *Reminders & Tracking:* Tap *⏰ Remind me* to receive an automated ping in 24 hours, and track submissions with `/applied`.\n\n"
    "📌 *Commands Available:*\n"
    "• `/jobs` — Browse today's fresh verified matches\n"
    "• `/preferences` — Toggle your target roles & locations\n"
    "• `/submitjob` — Submit a job link for admin review\n"
    "• `/profile` — View or update your resume details\n"
    "• `/applied` — View all jobs you've marked as applied\n"
    "• `/pause` & `/resume` — Control your daily digests\n"
    "• `/delete` — Permanently erase all your personal data"
)


# ---------- Command Handlers ----------

def _admin_telegram_ids() -> set[int]:
    """Read configured moderator IDs without accepting malformed values."""
    configured = os.getenv("ADMIN_TELEGRAM_IDS", "")
    admin_ids: set[int] = set()
    for value in configured.replace(" ", ",").split(","):
        if not value:
            continue
        try:
            admin_ids.add(int(value))
        except ValueError:
            log.warning("Ignoring invalid ADMIN_TELEGRAM_IDS entry")
    return admin_ids


async def myid_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the caller their own Telegram numeric ID for moderator setup."""
    if update.message and update.effective_user:
        await update.message.reply_text(
            f"Your Telegram numeric ID is: {update.effective_user.id}"
        )


def _parse_submission_args(args: list[str]) -> Optional[dict[str, str]]:
    fields = [value.strip() for value in " ".join(args).split("|", 4)]
    if len(fields) not in {4, 5}:
        return None
    apply_url, title, company, location = fields[:4]
    description = fields[4] if len(fields) == 5 else ""

    try:
        parsed = urlsplit(apply_url)
        host = parsed.hostname
        if (
            parsed.scheme.lower() != "https"
            or not host
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or any(character.isspace() or ord(character) < 33 for character in apply_url)
            or "\\" in parsed.netloc
        ):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            labels = host.lower().rstrip(".").split(".")
            valid_labels = all(
                0 < len(label) <= 63
                and label[0].isalnum()
                and label[-1].isalnum()
                and all(char.isalnum() or char == "-" for char in label)
                for label in labels
            )
            if (
                len(host) > 253
                or len(labels) < 2
                or not valid_labels
                or host.lower().endswith((".local", ".internal"))
            ):
                return None
    except ValueError:
        return None

    title = html_to_text(title)
    company = html_to_text(company)
    location = html_to_text(location)
    description = html_to_text(description)
    if not title or not company:
        return None
    if len(apply_url) > 2048 or len(title) > 240 or len(company) > 160:
        return None
    if len(location) > 240 or len(description) > 12000:
        return None
    return {
        "apply_url": apply_url,
        "title": title,
        "company": company,
        "location": location,
        "description": description,
    }


async def submitjob_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Queue a student-submitted listing for explicit human review."""
    if not update.message:
        return
    user = update.effective_user
    admins = _admin_telegram_ids()
    if not admins:
        await update.message.reply_text(
            "Job submissions are temporarily unavailable because admin review is not configured."
        )
        return

    conn = db.get_db()
    try:
        account = db.get_user(conn, user.id)
        if not account or not account["consent_at"]:
            await update.message.reply_text("Please run /start and accept the consent notice first.")
            return

        submission = _parse_submission_args(ctx.args or [])
        if not submission:
            await update.message.reply_text(
                "Use this format:\n"
                "/submitjob https://company.example/jobs/1 | Job title | Company | Location | "
                "Short description (optional)\n\n"
                "Only HTTPS links are accepted. An admin will inspect the live listing before approval."
            )
            return

        pending_count = conn.execute(
            """SELECT COUNT(*) AS total FROM job_submissions
               WHERE submitter_user_id = ? AND status = 'pending'""",
            (str(user.id),),
        ).fetchone()["total"]
        if pending_count >= 5:
            await update.message.reply_text(
                "You already have five listings awaiting review. Please wait for those to be reviewed."
            )
            return

        submission_id = uuid.uuid4().hex[:12]
        created = db.create_job_submission(
            conn,
            submission_id=submission_id,
            submitter_user_id=str(user.id),
            **submission,
        )
        if not created:
            await update.message.reply_text(
                "That listing is already in the job database or has already been submitted for review."
            )
            return
    finally:
        conn.close()

    await update.message.reply_text(
        "✅ Your job listing is queued for admin review. It won't be shared unless it passes review."
    )
    notice = (
        f"New job submission {submission_id}\n"
        f"{submission['title']} — {submission['company']}\n"
        f"Location: {submission['location'] or 'Not specified'}\n"
        f"Apply link: {submission['apply_url']}\n\n"
        "Inspect that the listing is live, authentic, and suitable for early-career applicants, "
        "then use /reviewjobs to approve or reject it."
    )
    for admin_id in admins:
        try:
            await ctx.bot.send_message(chat_id=admin_id, text=notice, disable_web_page_preview=True)
        except Exception:
            log.exception("Could not notify a configured job-submission moderator")


async def reviewjobs_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show pending submissions to configured moderators only."""
    if not update.message:
        return
    if update.effective_user.id not in _admin_telegram_ids():
        await update.message.reply_text("You are not authorized to review job submissions.")
        return

    conn = db.get_db()
    try:
        submissions = db.get_pending_job_submissions(conn)
    finally:
        conn.close()

    if not submissions:
        await update.message.reply_text("There are no job submissions awaiting review.")
        return

    for submission in submissions:
        text = (
            f"Pending job submission {submission['submission_id']}\n"
            f"{submission['title']} — {submission['company']}\n"
            f"Location: {submission['location'] or 'Not specified'}\n"
            f"Description: {submission['description'] or 'Not provided'}\n"
            f"Apply link: {submission['apply_url']}"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"submission:approve:{submission['submission_id']}",
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"submission:reject:{submission['submission_id']}",
            ),
        ]])
        await update.message.reply_text(
            text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )


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
        f"• *Custom Titles:* {', '.join(profile.custom_titles) or 'None'}\n"
        f"• *Locations:* {loc_str}\n"
        f"• *Work Modes:* {', '.join(profile.work_modes) or 'Any'}\n"
        f"• *Job Types:* {', '.join(profile.job_types) or 'Any'}\n"
        f"• *Digest Frequency:* {profile.digest_frequency.replace('_', ' ')}\n"
        f"• *Extracted Skills ({len(profile.skills)}):* {skills_str}\n\n"
        f"💡 _Tip: Use `/preferences` to customize locations & roles, or upload a new PDF anytime to refresh._"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


def build_preferences_keyboard(profile: Profile) -> InlineKeyboardMarkup:
    """Build preference toggles and custom-entry actions."""
    all_locations = [
        "remote", "bangalore", "delhi", "hyderabad", "pune", "mumbai",
        "chennai", "kolkata", "ahmedabad",
    ]
    all_roles = [
        "sde", "backend", "frontend", "ml", "data", "qa",
        "product", "ios", "android", "devops", "security", "design",
    ]
    all_modes = ["remote", "hybrid", "onsite"]
    all_types = ["internship", "full_time", "contract", "part_time"]
    all_frequencies = ["daily", "weekly", "on_demand"]

    current_locs = {loc.lower() for loc in profile.locations}
    current_roles = {role.lower() for role in profile.target_roles}
    current_modes = {mode.lower() for mode in profile.work_modes}
    current_types = {job_type.lower() for job_type in profile.job_types}

    def rows(options: list[str], active_values: set[str], kind: str) -> list[list[InlineKeyboardButton]]:
        buttons = [
            InlineKeyboardButton(
                f"{'✅' if value in active_values else '➕'} {value.replace('_', ' ').title()}",
                callback_data=f"pref:{kind}:{value}",
            )
            for value in options
        ]
        return [buttons[index:index + 2] for index in range(0, len(buttons), 2)]

    kb = [[InlineKeyboardButton("— Locations —", callback_data="noop")]]
    kb.extend(rows(all_locations, current_locs, "loc"))
    for index, location in enumerate(profile.locations):
        if location.lower() not in all_locations:
            kb.append([
                InlineKeyboardButton(
                    f"✖ {location[:32]}",
                    callback_data=f"pref:remove_loc:{index}",
                )
            ])
    kb.append([InlineKeyboardButton("➕ Add city", callback_data="pref:add_city")])
    kb.append([InlineKeyboardButton("— Target roles —", callback_data="noop")])
    kb.extend(rows(all_roles, current_roles, "role"))
    for index, title in enumerate(profile.custom_titles):
        kb.append([
            InlineKeyboardButton(
                f"✖ {title[:32]}",
                callback_data=f"pref:remove_title:{index}",
            )
        ])
    kb.append([InlineKeyboardButton("➕ Add custom job title", callback_data="pref:add_title")])
    kb.append([InlineKeyboardButton("— Work mode —", callback_data="noop")])
    kb.extend(rows(all_modes, current_modes, "mode"))
    kb.append([InlineKeyboardButton("— Job type —", callback_data="noop")])
    kb.extend(rows(all_types, current_types, "type"))
    kb.append([InlineKeyboardButton("— Digest frequency —", callback_data="noop")])
    kb.append([
        InlineKeyboardButton(
            f"{'✅' if profile.digest_frequency == value else '▫️'} {value.replace('_', ' ').title()}",
            callback_data=f"pref:frequency:{value}",
        )
        for value in all_frequencies
    ])
    kb.append([InlineKeyboardButton("✅ Done", callback_data="pref:done")])
    return InlineKeyboardMarkup(kb)


def preferences_summary(profile: Profile) -> str:
    """Render the saved settings above the preference keyboard."""
    return (
        "⚙️ *Your job preferences*\n\n"
        f"📍 Locations: {', '.join(profile.locations) or 'Any location'}\n"
        f"🎯 Role groups: {', '.join(profile.target_roles) or 'Any role'}\n"
        f"✍️ Custom titles: {', '.join(profile.custom_titles) or 'None'}\n"
        f"🏢 Work modes: {', '.join(profile.work_modes) or 'Any'}\n"
        f"💼 Job types: {', '.join(profile.job_types) or 'Any'}\n\n"
        f"📨 Digest frequency: {profile.digest_frequency.replace('_', ' ')}\n\n"
        "Tap choices to toggle them. Add custom cities or titles using the buttons below. "
        "You can change these any time with /preferences."
    )


def merge_saved_preferences(profile: Profile, saved: Optional[Profile]) -> Profile:
    """Keep user-selected settings when a new resume is parsed."""
    if saved and saved.preferences_configured:
        for field_name in (
            "target_roles",
            "custom_titles",
            "locations",
            "work_modes",
            "job_types",
        ):
            setattr(profile, field_name, getattr(saved, field_name))
        profile.digest_frequency = saved.digest_frequency
        profile.preferences_configured = True
    return profile


def should_send_digest(profile: Profile, now: Optional[datetime.datetime] = None) -> bool:
    """Check the user's configured digest cadence, using Monday as the weekly send day."""
    if profile.digest_frequency == "on_demand":
        return False
    if profile.digest_frequency == "weekly":
        ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        current = now or datetime.datetime.now(ist)
        if current.tzinfo is None:
            current = current.replace(tzinfo=ist)
        return current.astimezone(ist).weekday() == 0
    return True


def build_feedback_reason_keyboard(job_id: str) -> InlineKeyboardMarkup:
    reasons = [
        ("Role mismatch", "role"),
        ("Location", "location"),
        ("Experience", "experience"),
        ("Pay / stipend", "pay"),
        ("Other", "other"),
    ]
    buttons = [
        InlineKeyboardButton(
            label,
            callback_data=f"act:reason:{job_id}:{reason}",
        )
        for label, reason in reasons
    ]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("Cancel", callback_data="noop")])
    return InlineKeyboardMarkup(rows)


async def preferences_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    ctx.user_data.pop("pending_preference", None)
    uid = update.effective_user.id
    conn = db.get_db()
    profile = db.get_profile(conn, str(uid))
    if not profile:
        profile = Profile(user_id=str(uid), name=update.effective_user.first_name or "Student")
        db.save_profile(conn, profile)

    message = await update.message.reply_text(
        preferences_summary(profile),
        reply_markup=build_preferences_keyboard(profile),
        parse_mode=ParseMode.MARKDOWN,
    )
    ctx.user_data["preferences_message_id"] = message.message_id
    ctx.user_data["preferences_chat_id"] = message.chat_id


async def handle_preference_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Save custom city/title values after the user taps an add button."""
    if not update.message or not ctx.user_data.get("pending_preference"):
        return
    pending = ctx.user_data.pop("pending_preference")
    values = [value.strip()[:60] for value in update.message.text.split(",") if value.strip()]
    if not values:
        await update.message.reply_text("Please send a non-empty value, or use /preferences to cancel.")
        ctx.user_data["pending_preference"] = pending
        return

    uid = update.effective_user.id
    conn = db.get_db()
    profile = db.get_profile(conn, str(uid)) or Profile(
        user_id=str(uid),
        name=update.effective_user.first_name or "Student",
    )
    field_values = profile.locations if pending == "locations" else profile.custom_titles
    known_values = {value.casefold() for value in field_values}
    new_values = [value for value in values if value.casefold() not in known_values]
    if len(field_values) + len(new_values) > 15:
        await update.message.reply_text(
            "Please keep up to 15 custom cities or job titles. Remove an existing choice and try again."
        )
        ctx.user_data["pending_preference"] = pending
        return
    field_values.extend(new_values)
    if pending == "locations":
        profile.locations = field_values
    else:
        profile.custom_titles = field_values
    profile.preferences_configured = True
    db.save_profile(conn, profile)

    chat_id = ctx.user_data.get("preferences_chat_id")
    message_id = ctx.user_data.get("preferences_message_id")
    if chat_id and message_id:
        await ctx.bot.edit_message_text(
            preferences_summary(profile),
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=build_preferences_keyboard(profile),
        )
    await update.message.reply_text("✅ Saved. Your matching preferences are updated.")


def _start_job_refresh() -> asyncio.Task[Any]:
    """Start one shared background refresh for all users with an empty cache."""
    global _job_refresh_task
    if _job_refresh_task is None or _job_refresh_task.done():
        _job_refresh_task = asyncio.create_task(asyncio.to_thread(refresh_job_listings))
    return _job_refresh_task


async def _refresh_then_send_matches(
    bot: Any,
    chat_id: int,
    user_id: int,
    refresh_task: asyncio.Task[Any],
) -> None:
    try:
        await refresh_task
    except Exception:
        log.exception("Initial job refresh failed for user %s", user_id)
        await bot.send_message(
            chat_id=chat_id,
            text="I couldn't refresh job listings right now. Please try /jobs again shortly.",
        )
        return

    conn = db.get_db()
    try:
        profile = db.get_profile(conn, str(user_id)) or Profile(user_id=str(user_id))
        verified_jobs = db.get_verified_jobs(conn)
    finally:
        conn.close()
    await _send_job_matches(bot, chat_id, user_id, profile, verified_jobs)


async def _send_job_matches(
    bot: Any,
    chat_id: int,
    user_id: int,
    profile: Profile,
    verified_jobs: list[Any],
) -> None:
    conn = db.get_db()
    try:
        seen_rows = conn.execute(
            "SELECT job_id FROM applications WHERE user_id = ?",
            (str(user_id),),
        ).fetchall()
        seen_ids = {r["job_id"] for r in seen_rows}
        negative_feedback = db.get_user_negative_feedback(conn, str(user_id))
        matches = match_for_user(
            profile=profile,
            verified_jobs=verified_jobs,
            already_seen_ids=seen_ids,
            negative_feedback=negative_feedback,
            limit=5,
        )

        if not matches:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "✨ *No new matches for these preferences yet.* "
                    "Try /preferences to broaden your filters, or check /jobs again later."
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await bot.send_message(
            chat_id=chat_id,
            text=f"🔍 Found {len(matches)} fresh verified match(es) for you:",
        )
        now_str = time.strftime("%Y-%m-%dT%H:%M:%S")
        for job, score, matched_skills, missing_skills in matches:
            fit = explain_match(profile, job, matched_skills, missing_skills)
            card_text = format_job_card(job, fit)
            keyboard = build_job_keyboard(job)
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=card_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                await bot.send_message(
                    chat_id=chat_id,
                    text=card_text.replace("*", "").replace("_", ""),
                    reply_markup=keyboard,
                )
            conn.execute(
                """INSERT INTO matches (user_id, job_id, score, fit_json, sent_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, job_id) DO UPDATE SET
                       score = excluded.score,
                       fit_json = excluded.fit_json,
                       sent_at = excluded.sent_at""",
                (str(user_id), job.job_id, score, json.dumps(fit.to_dict()), now_str),
            )
            db.record_application_event(conn, str(user_id), job.job_id, status="sent")
        conn.commit()
    finally:
        conn.close()


async def jobs_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    uid = update.effective_user.id
    conn = db.get_db()
    try:
        user = db.get_user(conn, uid)
        if not user or not user["consent_at"]:
            await update.message.reply_text("Please run /start and accept the consent notice first.")
            return

        profile = db.get_profile(conn, str(uid))
        if not profile:
            profile = Profile(user_id=str(uid), name=update.effective_user.first_name or "Student")
        verified_jobs = db.get_verified_jobs(conn)
    finally:
        conn.close()

    if not verified_jobs:
        await update.message.reply_text(
            "There are no cached listings yet. I'm refreshing the job boards now and will send your matches here when ready."
        )
        refresh_task = _start_job_refresh()
        ctx.application.create_task(
            _refresh_then_send_matches(
                ctx.bot,
                update.effective_chat.id,
                uid,
                refresh_task,
            )
        )
        return

    await _send_job_matches(ctx.bot, update.effective_chat.id, uid, profile, verified_jobs)


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

    status_msg = await update.message.reply_text("📥 Reading and parsing your resume...")

    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        buffer = io.BytesIO()
        await tg_file.download_to_memory(buffer)
        buffer.seek(0)

        existing_profile = db.get_profile(conn, str(uid))
        profile = parse_resume(
            pdf_source=buffer,
            user_id=str(uid),
            form_answers={"name": update.effective_user.first_name or "Student"},
        )
        profile = merge_saved_preferences(profile, existing_profile)
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
        if not should_send_digest(profile):
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
            profile = db.get_profile(conn, str(uid)) or Profile(
                user_id=str(uid),
                name=q.from_user.first_name or "Student",
            )
            db.save_profile(conn, profile)
            welcome_msg = (
                "🎉 *Thank you for consenting!*\n\n"
                "Let's personalize your matches. Set roles, cities, and work preferences now, "
                "or skip and upload your resume whenever you're ready."
            )
            kb = InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton("⚙️ Set preferences", callback_data="pref:open"),
                    InlineKeyboardButton("Skip for now", callback_data="pref:skip"),
                ]]
            )
            await q.edit_message_text(
                welcome_msg,
                reply_markup=kb,
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await q.edit_message_text("No problem. No information was recorded.")

    elif kind == "pref":
        sub, _, val = rest.partition(":")
        profile = db.get_profile(conn, str(uid))
        if not profile:
            profile = Profile(user_id=str(uid), name=q.from_user.first_name or "Student")

        if sub == "open":
            await q.answer()
            if q.message:
                ctx.user_data["preferences_message_id"] = q.message.message_id
                ctx.user_data["preferences_chat_id"] = q.message.chat_id
                await q.edit_message_text(
                    preferences_summary(profile),
                    reply_markup=build_preferences_keyboard(profile),
                )

        elif sub == "skip":
            await q.answer()
            await q.edit_message_text(
                "No problem. Upload your resume when ready, or use /preferences later. "
                "Use /jobs to browse when listings are available."
            )

        elif sub in {"add_city", "add_title"}:
            ctx.user_data["pending_preference"] = (
                "locations" if sub == "add_city" else "custom_titles"
            )
            if q.message:
                ctx.user_data["preferences_message_id"] = q.message.message_id
                ctx.user_data["preferences_chat_id"] = q.message.chat_id
            prompt = (
                "Send the city or cities you want, separated by commas."
                if sub == "add_city"
                else "Send the job title or titles you want, separated by commas."
            )
            await q.answer()
            if q.message:
                await q.message.reply_text(prompt)

        elif sub in {"loc", "role", "mode", "type"}:
            fields = {
                "loc": ("locations", "locations"),
                "role": ("target_roles", "roles"),
                "mode": ("work_modes", "work modes"),
                "type": ("job_types", "job types"),
            }
            field_name, label = fields[sub]
            values = list(getattr(profile, field_name))
            matching = next((item for item in values if item.casefold() == val.casefold()), None)
            if matching is not None:
                values.remove(matching)
                response = f"Removed {val.replace('_', ' ')} from {label}"
            else:
                values.append(val)
                response = f"Added {val.replace('_', ' ')} to {label}"
            setattr(profile, field_name, values)
            profile.preferences_configured = True
            db.save_profile(conn, profile)
            await q.answer(response)
            if q.message:
                await q.edit_message_text(
                    preferences_summary(profile),
                    reply_markup=build_preferences_keyboard(profile),
                )

        elif sub in {"remove_loc", "remove_title"}:
            field_name = "locations" if sub == "remove_loc" else "custom_titles"
            values = list(getattr(profile, field_name))
            try:
                index = int(val)
                removed = values.pop(index)
            except (ValueError, IndexError):
                await q.answer("That preference is no longer available.", show_alert=True)
                return
            setattr(profile, field_name, values)
            profile.preferences_configured = True
            db.save_profile(conn, profile)
            await q.answer(f"Removed {removed}")
            if q.message:
                await q.edit_message_text(
                    preferences_summary(profile),
                    reply_markup=build_preferences_keyboard(profile),
                )

        elif sub == "frequency":
            if val not in {"daily", "weekly", "on_demand"}:
                await q.answer("That digest frequency is unavailable.", show_alert=True)
                return
            profile.digest_frequency = val
            profile.preferences_configured = True
            db.save_profile(conn, profile)
            await q.answer(f"Digest frequency set to {val.replace('_', ' ')}.")
            if q.message:
                await q.edit_message_text(
                    preferences_summary(profile),
                    reply_markup=build_preferences_keyboard(profile),
                    parse_mode=ParseMode.MARKDOWN,
                )

        elif sub == "done":
            profile.preferences_configured = True
            db.save_profile(conn, profile)
            await q.answer("Preferences saved!")
            await q.edit_message_text(
                "✅ *Preferences saved!*\nType `/jobs` to see matches with your updated settings. "
                "Use /preferences any time to change them.",
                parse_mode=ParseMode.MARKDOWN,
            )

    elif kind == "submission":
        action, _, submission_id = rest.partition(":")
        if uid not in _admin_telegram_ids():
            await q.answer("You are not authorized to review submissions.", show_alert=True)
            conn.close()
            return

        try:
            submission = db.get_job_submission(conn, submission_id)
            if not submission or submission["status"] != "pending":
                await q.answer("This submission has already been reviewed.", show_alert=True)
                return

            if action == "reject":
                reviewed = db.review_job_submission(
                    conn,
                    submission_id,
                    "rejected",
                    str(uid),
                    rejection_reason="Rejected by moderator",
                )
                response = "Submission rejected." if reviewed else "This submission was already reviewed."
                user_notice = (
                    "Your submitted job listing was reviewed and wasn't approved."
                    if reviewed else None
                )
            elif action == "approve":
                host = urlsplit(submission["apply_url"]).hostname or ""
                job = Job(
                    company=submission["company"],
                    title=submission["title"],
                    location=submission["location"] or "",
                    description=submission["description"] or "",
                    apply_url=submission["apply_url"],
                    source="manual",
                    external_id=submission_id,
                    remote=looks_remote(
                        submission["location"] or "",
                        submission["title"],
                        submission["description"] or "",
                    ),
                )
                verified_job = verify_job(job, allowed_domains=[host])
                if not verified_job.verified:
                    reason = verified_job.reject_reason or "Failed the listing verification rules"
                    reviewed = db.review_job_submission(
                        conn,
                        submission_id,
                        "rejected",
                        str(uid),
                        rejection_reason=reason,
                    )
                    response = (
                        f"Not approved: {reason}"
                        if reviewed else "This submission was already reviewed."
                    )
                    user_notice = (
                        "Your submitted job listing was reviewed but didn't pass the safety checks."
                        if reviewed else None
                    )
                elif db.save_jobs(conn, [verified_job], domains=[host], commit=False) == 0:
                    conn.rollback()
                    db.review_job_submission(
                        conn,
                        submission_id,
                        "rejected",
                        str(uid),
                        rejection_reason="Duplicate of an existing listing",
                    )
                    response = "Not approved: this listing is already in the job database."
                    user_notice = "Your submitted job listing was already present, so it wasn't added again."
                else:
                    reviewed = db.review_job_submission(
                        conn,
                        submission_id,
                        "approved",
                        str(uid),
                        job_id=verified_job.job_id,
                        commit=False,
                    )
                    if reviewed:
                        conn.commit()
                        response = "Approved and added to verified job listings."
                        user_notice = "Your submitted job listing was approved and added to the job pool."
                    else:
                        conn.rollback()
                        response = "This submission was already reviewed."
                        user_notice = None
            else:
                await q.answer("That review action is unavailable.", show_alert=True)
                return

            await q.answer(response[:180], show_alert=action == "approve" and response.startswith("Not approved"))
            if q.message:
                await q.edit_message_text(
                    f"Submission {submission_id}: {response}\n\n"
                    f"{submission['title']} — {submission['company']}\n"
                    f"Apply link: {submission['apply_url']}"
                )
            if submission["submitter_user_id"] and user_notice:
                try:
                    await ctx.bot.send_message(
                        chat_id=int(submission["submitter_user_id"]),
                        text=user_notice,
                        disable_web_page_preview=True,
                    )
                except Exception:
                    log.exception("Could not notify the job submitter of the review result")
        except Exception:
            conn.rollback()
            log.exception("Failed reviewing job submission %s", submission_id)
            await q.answer("Review failed. Please retry or check the server logs.", show_alert=True)
        finally:
            conn.close()
        return

    elif kind == "act":
        action, _, action_value = rest.partition(":")

        if action == "applied":
            job_id = action_value
            job = db.get_job_by_id(conn, job_id)
            tracker.record_feedback(conn, str(uid), job_id, "applied")
            await q.answer("🎉 Marked as applied! Tracked under /applied", show_alert=False)
            if job and q.message:
                try:
                    await q.edit_message_reply_markup(reply_markup=build_job_keyboard(job, "applied"))
                except Exception:
                    pass

        elif action == "not_relevant":
            job = db.get_job_by_id(conn, action_value)
            if not job:
                await q.answer("This job is no longer available.", show_alert=True)
                return
            await q.answer("What didn't fit? Your answer will help tune future matches.")
            if q.message:
                await q.edit_message_reply_markup(
                    reply_markup=build_feedback_reason_keyboard(action_value)
                )

        elif action == "reason":
            job_id, _, reason = action_value.partition(":")
            reason_labels = {
                "role": "role mismatch",
                "location": "location",
                "experience": "experience level",
                "pay": "pay or stipend",
                "other": "other",
            }
            if reason not in reason_labels:
                await q.answer("That feedback option is unavailable.", show_alert=True)
                return
            job = db.get_job_by_id(conn, job_id)
            tracker.record_feedback(
                conn,
                str(uid),
                job_id,
                "not_relevant",
                reason=reason,
            )
            await q.answer(f"Thanks — noted: {reason_labels[reason]}.")
            if job and q.message:
                await q.edit_message_reply_markup(
                    reply_markup=build_job_keyboard(job, "not_relevant")
                )

        elif action == "like":
            job_id = action_value
            job = db.get_job_by_id(conn, job_id)
            tracker.record_feedback(conn, str(uid), job_id, "liked")
            await q.answer("💚 Thanks! We'll prioritize similar roles.")
            if job and q.message:
                await q.edit_message_reply_markup(
                    reply_markup=build_job_keyboard(job, "liked")
                )

        elif action == "remind":
            job_id = action_value
            job = db.get_job_by_id(conn, job_id)
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
        BotCommand("myid", "Show your Telegram numeric ID"),
        BotCommand("jobs", "Find fresh verified job matches"),
        BotCommand("preferences", "Customize target roles & locations"),
        BotCommand("submitjob", "Submit a job link for admin review"),
        BotCommand("profile", "View or update your resume details"),
        BotCommand("applied", "View all jobs you marked as applied"),
        BotCommand("help", "How the bot works & guide"),
        BotCommand("reviewjobs", "Review community job submissions (admins)"),
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
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("preferences", preferences_cmd))
    app.add_handler(CommandHandler("jobs", jobs_cmd))
    app.add_handler(CommandHandler("submitjob", submitjob_cmd))
    app.add_handler(CommandHandler("reviewjobs", reviewjobs_cmd))
    app.add_handler(CommandHandler("applied", applied_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))

    # Document upload for resumes
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_preference_text))

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
