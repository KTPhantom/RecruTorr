"""Agent 6: Delivery

Formats and sends daily job digests via Telegram with action buttons:
[ Apply ]  [ Applied ]  [ Not relevant ]  [ Remind me ]
Rules: Max 10 jobs per day; never resend a job; clean markdown formatting.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.schemas import FitExplanation, Job

log = logging.getLogger(__name__)

MAX_DAILY_JOBS = 10


def format_job_card(job: Job, fit: Optional[FitExplanation] = None) -> str:
    """Format a clean, readable text card for Telegram."""
    loc = job.location or "Location not specified"
    remote_badge = " [Remote]" if job.remote else ""
    
    lines = [
        f"🎯 *{job.title}*",
        f"🏢 *{job.company}* | 📍 {loc}{remote_badge}",
    ]

    if fit:
        lines.append(f"\n💡 *Why it fits:* {fit.fit_summary}")
        if fit.matched_skills:
            lines.append(f"✅ *Matched skills:* {', '.join(fit.matched_skills)}")
        if fit.missing_skills:
            lines.append(f"⚠️ *Key missing:* {', '.join(fit.missing_skills)}")

    return "\n".join(lines)


def build_job_keyboard(job: Job, action_state: Optional[str] = None) -> InlineKeyboardMarkup:
    """Build the action buttons for a job card, adapting when a state is triggered."""
    if action_state == "applied":
        buttons = [
            [InlineKeyboardButton("🔗 Apply", url=job.apply_url)],
            [InlineKeyboardButton("✅ Applied (Tracked in /applied)", callback_data="noop")],
        ]
    elif action_state == "not_relevant":
        buttons = [
            [InlineKeyboardButton("🔗 Apply", url=job.apply_url)],
            [InlineKeyboardButton("❌ Marked Not Relevant (Tuned)", callback_data="noop")],
        ]
    elif action_state == "remind":
        buttons = [
            [InlineKeyboardButton("🔗 Apply", url=job.apply_url)],
            [
                InlineKeyboardButton("✅ Applied", callback_data=f"act:applied:{job.job_id}"),
                InlineKeyboardButton("❌ Not relevant", callback_data=f"act:not_relevant:{job.job_id}"),
            ],
            [InlineKeyboardButton("⏰ Reminder Scheduled (in 24h)", callback_data="noop")],
        ]
    else:
        buttons = [
            [InlineKeyboardButton("🔗 Apply", url=job.apply_url)],
            [
                InlineKeyboardButton("✅ Applied", callback_data=f"act:applied:{job.job_id}"),
                InlineKeyboardButton("❌ Not relevant", callback_data=f"act:not_relevant:{job.job_id}"),
            ],
            [InlineKeyboardButton("⏰ Remind me", callback_data=f"act:remind:{job.job_id}")],
        ]
    return InlineKeyboardMarkup(buttons)
