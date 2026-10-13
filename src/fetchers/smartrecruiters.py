"""Fetch jobs from a public SmartRecruiters career site.

Endpoint: https://api.smartrecruiters.com/v1/companies/{identifier}/postings
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

import requests

from src.schemas import Job, looks_remote

log = logging.getLogger(__name__)

API = "https://api.smartrecruiters.com/v1/companies/{identifier}/postings"
TIMEOUT = 20
PAGE_SIZE = 100
MAX_PAGES = 10
HEADERS = {"User-Agent": "recrutorr/0.1 (student side project)"}


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch(company_name: str, identifier: str) -> List[Job]:
    """Return normalized jobs for one SmartRecruiters career site."""
    url = API.format(identifier=identifier)
    jobs: List[Job] = []
    offset = 0
    total_found: Optional[int] = None

    for _ in range(MAX_PAGES):
        resp = requests.get(
            url,
            params={"limit": PAGE_SIZE, "offset": offset},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        if resp.status_code == 404:
            log.warning("SmartRecruiters company not found: %s", identifier)
            return jobs
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("content", []) if isinstance(payload, dict) else []
        if not items:
            break
        if total_found is None:
            total_found = payload.get("totalFound")

        for item in items:
            title = item.get("name")
            job_id = item.get("id")
            if not title or not job_id:
                continue

            location_data = item.get("location") or {}
            location = ", ".join(
                part for part in (
                    location_data.get("city"),
                    location_data.get("region"),
                    location_data.get("country"),
                ) if part
            )
            remote = bool(location_data.get("remote")) or looks_remote(location, title)
            apply_url = f"https://careers.smartrecruiters.com/{identifier}/{job_id}"
            department = (item.get("department") or {}).get("label", "")
            function = (item.get("function") or {}).get("label", "")
            employment_type = (item.get("typeOfEmployment") or {}).get("label", "")
            description = " ".join(
                part for part in (department, function, employment_type) if part
            )

            jobs.append(
                Job(
                    company=company_name,
                    title=title.strip(),
                    location=location,
                    description=description,
                    apply_url=apply_url,
                    source="smartrecruiters",
                    external_id=str(job_id),
                    remote=remote,
                    posted_at=_parse_dt(item.get("releasedDate")),
                )
            )

        offset += len(items)
        if len(items) < PAGE_SIZE or (total_found is not None and offset >= total_found):
            break
    else:
        log.warning("SmartRecruiters %s listing capped at %d pages", identifier, MAX_PAGES)

    log.info("SmartRecruiters %s: %d jobs", identifier, len(jobs))
    return jobs
