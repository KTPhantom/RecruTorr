"""Agent 7: Tracker & Feedback

Records user interactions on job cards (Applied, Not relevant, Remind me),
manages reminders, and adjusts future ranking weights based on user feedback.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from src.db import record_application_event, get_user_negative_feedback

log = logging.getLogger(__name__)


def record_feedback(
    conn: Any,
    user_id: str,
    job_id: str,
    action: str,
    reason: Optional[str] = None
) -> str:
    """
    Handle action button tap ('applied', 'not_relevant', 'remind').
    Returns a user-friendly acknowledgement string.
    """
    record_application_event(conn, user_id, job_id, status=action, reason=reason)
    
    if action == "applied":
        return "🎉 Marked as applied! We'll keep track of this under /applied."
    elif action == "not_relevant":
        return "👍 Got it. We will down-rank this role and company for your future matches."
    elif action == "remind":
        return "⏰ Reminder saved! We will remind you to check this role."
    else:
        return "Action saved."


def get_feedback_weights(conn: Any, user_id: str) -> Dict[str, Any]:
    """Retrieve negative feedback data for adjusting ranking in the Matcher."""
    return get_user_negative_feedback(conn, user_id)
