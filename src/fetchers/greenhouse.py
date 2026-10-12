"""Fetch jobs from a company's public Greenhouse job board.

Endpoint: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

import requests

from src.schemas import Job, html_to_text, looks_remote

log = logging.getLogger(__name__)

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
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
    """Return normalized jobs for one Greenhouse board. Raises on network failure."""
    resp = requests.get(
        API.format(slug=slug),
        params={"content": "true"},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    if resp.status_code == 404:
        log.warning("Greenhouse board not found: %s", slug)
        return []
    resp.raise_for_status()

    jobs: List[Job] = []
    for item in resp.json().get("jobs", []):
        location = (item.get("location") or {}).get("name", "") or ""
        description = html_to_text(item.get("content", ""))
        url = item.get("absolute_url")
        if not url or not item.get("title"):
            continue
        jobs.append(
            Job(
                company=company_name,
                title=item["title"].strip(),
                location=location,
                description=description,
                apply_url=url,
                source="greenhouse",
                external_id=str(item.get("id")),
                remote=looks_remote(location, item["title"]),
                posted_at=_parse_dt(item.get("updated_at")),
            )
        )
    log.info("Greenhouse %s: %d jobs", slug, len(jobs))
    return jobs
