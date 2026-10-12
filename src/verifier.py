"""Agent 2: Verifier

Decides whether a posting is authentic and suitable for freshers using deterministic rules.
No LLM used here.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple
from urllib.parse import urlparse

from src.schemas import Job

log = logging.getLogger(__name__)

# Known trusted ATS domains
KNOWN_ATS_DOMAINS = {
    "greenhouse.io",
    "boards.greenhouse.io",
    "lever.co",
    "jobs.lever.co",
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "workable.com",
    "smartrecruiters.com",
}

# Scam / fee-based keywords
FEE_KEYWORDS = [
    "registration fee",
    "training fee",
    "security deposit",
    "pay to join",
    "upfront fee",
    "processing fee",
    "caution deposit",
    "course fee",
    "placement charges",
    "payment required",
    "invest in training",
]

# Patterns indicating senior / experienced roles (not suitable for freshers)
SENIOR_TITLE_PATTERNS = [
    r"\bsenior\b",
    r"\bsr\.?\b",
    r"\blead\b",
    r"\bstaff\b",
    r"\bprincipal\b",
    r"(?<!associate\s)(?<!assistant\s)\bmanager\b",
    r"\bdirector\b",
    r"\bhead of\b",
    r"\bvp\b",
    r"\barchitect\b",
    r"\bexecutive\b",
]

FRESHER_TITLE_EXCEPTIONS = [
    r"\bintern\b",
    r"\binternship\b",
    r"\btrainee\b",
    r"\bgraduate\b",
    r"\bjunior\b",
    r"\bjr\.?\b",
    r"\bassociate\b",
    r"\bentry[- ]level\b",
    r"\bapm\b",
]

SENIOR_EXP_PATTERNS = [
    r"\b([3-9]|\d{2})\+?\s*(?:to\s*\d+\s*)?(?:years?|yrs?)(?:\s+of)?\s+experience\b",
    r"\bminimum\s+(?:of\s+)?([3-9]|\d{2})\s+years?\b",
    r"\bat\s+least\s+([3-9]|\d{2})\s+years?\b",
]

MAX_JOB_AGE_DAYS = 30


def check_domain_authenticity(apply_url: str, allowed_domains: Optional[List[str]] = None) -> bool:
    """Ensure the apply URL domain belongs to a trusted ATS or the company's verified domains."""
    if not apply_url:
        return False
    try:
        parsed = urlparse(apply_url)
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False

        # Match known ATS domains (e.g. *.greenhouse.io or greenhouse.io)
        for ats in KNOWN_ATS_DOMAINS:
            if hostname == ats or hostname.endswith("." + ats):
                return True

        # Match company domains if configured
        if allowed_domains:
            for domain in allowed_domains:
                clean_d = domain.lower().strip()
                if hostname == clean_d or hostname.endswith("." + clean_d):
                    return True

        return False
    except Exception:
        return False


def check_fee_keywords(text: str) -> Optional[str]:
    """Return matched fee keyword if found, else None."""
    lower_text = text.lower()
    for kw in FEE_KEYWORDS:
        if kw in lower_text:
            return kw
    return None


def is_job_stale(posted_at: Optional[datetime], max_days: int = MAX_JOB_AGE_DAYS) -> bool:
    """Return True if posted more than max_days ago."""
    if not posted_at:
        return False
    now = datetime.now(timezone.utc)
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    age = (now - posted_at).total_seconds() / 86400
    return age > max_days


def is_fresher_friendly(title: str, description: str) -> Tuple[bool, Optional[str]]:
    """Check if title and description are free from senior experience requirements."""
    t_lower = title.lower()

    # Check for senior title patterns (unless explicitly junior/intern/associate like 'Associate Product Manager')
    for pat in SENIOR_TITLE_PATTERNS:
        if re.search(pat, t_lower):
            # If it has "senior" or "lead" or "staff" or "principal", definitely senior
            if any(re.search(s_word, t_lower) for s_word in [r"\bsenior\b", r"\bsr\.?\b", r"\blead\b", r"\bstaff\b", r"\bprincipal\b"]):
                return False, f"Senior role detected in title ({pat})"
            # For other roles, if it explicitly has fresher markers like associate, intern, etc. allow it
            if not any(re.search(f_exc, t_lower) for f_exc in FRESHER_TITLE_EXCEPTIONS):
                return False, f"Senior role detected in title ({pat})"

    d_lower = description.lower()
    for pat in SENIOR_EXP_PATTERNS:
        match = re.search(pat, d_lower)
        if match:
            return False, f"Experience requirement detected ({match.group(0)})"

    return True, None


def normalize_title(title: str) -> str:
    """Normalize title for deduplication."""
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", title.lower())
    return " ".join(clean.split())


def verify_job(job: Job, allowed_domains: Optional[List[str]] = None) -> Job:
    """Run all verification rules on a single Job and update its verification attributes."""
    # 1. Domain Check
    if not check_domain_authenticity(job.apply_url, allowed_domains):
        job.verified = False
        job.reject_reason = f"Apply domain not authorized: {job.apply_url}"
        job.fresher_friendly = False
        return job

    # 2. Fee / Scam Check
    full_text = f"{job.title}\n{job.description}"
    fee_kw = check_fee_keywords(full_text)
    if fee_kw:
        job.verified = False
        job.reject_reason = f"Asking for fees/deposits detected: '{fee_kw}'"
        job.fresher_friendly = False
        return job

    # 3. Staleness Check
    if is_job_stale(job.posted_at):
        job.verified = False
        job.reject_reason = f"Posting exceeds {MAX_JOB_AGE_DAYS} days old"
        job.fresher_friendly = False
        return job

    # 4. Fresher-Friendly Check
    friendly, reason = is_fresher_friendly(job.title, job.description)
    job.fresher_friendly = friendly
    if not friendly:
        job.verified = False
        job.reject_reason = reason or "Not fresher-friendly"
        return job

    # Passed all verification rules
    job.verified = True
    job.reject_reason = None
    job.fresher_friendly = True
    return job


def verify_and_deduplicate(jobs: List[Job], allowed_domains: Optional[List[str]] = None) -> List[Job]:
    """Verify a batch of jobs and deduplicate by (company, normalized_title, location)."""
    seen_keys: Set[Tuple[str, str, str]] = set()
    verified_jobs: List[Job] = []

    for job in jobs:
        verified_job = verify_job(job, allowed_domains)
        if not verified_job.verified:
            verified_jobs.append(verified_job)
            continue

        dedupe_key = (
            job.company.lower().strip(),
            normalize_title(job.title),
            (job.location or "").lower().strip(),
        )

        if dedupe_key in seen_keys:
            verified_job.verified = False
            verified_job.reject_reason = "Duplicate of existing posting in batch"
        else:
            seen_keys.add(dedupe_key)

        verified_jobs.append(verified_job)

    return verified_jobs
