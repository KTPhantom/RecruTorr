"""Fetch jobs from a company's public Ashby job board.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/{slug}
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

import requests

from src.schemas import Job, html_to_text, looks_remote

log = logging.getLogger(__name__)

API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
TIMEOUT = 20
HEADERS = {"User-Agent": "offcampus-matcher/0.1 (student side project)"}


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch(company_name: str, slug: str) -> List[Job]:
    """Return normalized jobs for one Ashby job board. Raises on network failure."""
    resp = requests.get(
        API.format(slug=slug),
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    if resp.status_code == 404:
        log.warning("Ashby board not found: %s", slug)
        return []
    resp.raise_for_status()

    data = resp.json()
    items = data.get("jobs", []) if isinstance(data, dict) else []

    jobs: List[Job] = []
    for item in items:
        title = item.get("title")
        url = item.get("jobUrl") or item.get("applyUrl")
        job_id = item.get("id")
        if not title or not url or not job_id:
            continue

        location = item.get("location") or item.get("locationName") or ""
        remote = bool(item.get("isRemote")) or looks_remote(location, title)
        desc_html = item.get("descriptionHtml") or item.get("descriptionPlain") or ""
        description = html_to_text(desc_html)
        posted_at = _parse_dt(item.get("publishedAt"))

        jobs.append(
            Job(
                company=company_name,
                title=title.strip(),
                location=str(location).strip(),
                description=description,
                apply_url=url,
                source="ashby",
                external_id=str(job_id),
                remote=remote,
                posted_at=posted_at,
            )
        )
    log.info("Ashby %s: %d jobs", slug, len(jobs))
    return jobs
