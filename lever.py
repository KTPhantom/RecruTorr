"""Fetch jobs from a company's public Lever postings page.

Endpoint: https://api.lever.co/v0/postings/{slug}?mode=json
(EU-hosted accounts use api.eu.lever.co)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

import requests

from src.schemas import Job, looks_remote

log = logging.getLogger(__name__)

API = "https://api.lever.co/v0/postings/{slug}"
API_EU = "https://api.eu.lever.co/v0/postings/{slug}"
TIMEOUT = 20
HEADERS = {"User-Agent": "offcampus-matcher/0.1 (student side project)"}


def fetch(company_name: str, slug: str, eu: bool = False) -> List[Job]:
    """Return normalized jobs for one Lever account. Raises on network failure."""
    base = API_EU if eu else API
    resp = requests.get(
        base.format(slug=slug),
        params={"mode": "json"},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    if resp.status_code == 404:
        log.warning("Lever account not found: %s", slug)
        return []
    resp.raise_for_status()

    jobs: List[Job] = []
    for item in resp.json():
        cats = item.get("categories") or {}
        location = cats.get("location") or ""
        workplace = item.get("workplaceType") or ""
        url = item.get("hostedUrl") or item.get("applyUrl")
        title = item.get("text")
        if not url or not title:
            continue

        created_ms = item.get("createdAt")
        posted = (
            datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            if isinstance(created_ms, (int, float))
            else None
        )

        # descriptionPlain + list sections (requirements etc.) give the full text
        parts = [item.get("descriptionPlain", "")]
        for section in item.get("lists", []) or []:
            parts.append(section.get("text", ""))
            parts.append(_strip(section.get("content", "")))
        parts.append(item.get("additionalPlain", ""))
        description = "\n".join(p for p in parts if p).strip()

        jobs.append(
            Job(
                company=company_name,
                title=title.strip(),
                location=location,
                description=description,
                apply_url=url,
                source="lever",
                external_id=str(item.get("id")),
                remote=workplace == "remote" or looks_remote(location, title),
                posted_at=posted,
            )
        )
    log.info("Lever %s: %d jobs", slug, len(jobs))
    return jobs


def _strip(html_text: str) -> str:
    from src.schemas import html_to_text

    return html_to_text(html_text)
