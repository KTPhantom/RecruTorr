"""Read RSS 2.0 and Atom job feeds into the shared Job schema."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, List, Optional
from urllib.parse import urlsplit
from xml.etree import ElementTree

import requests

from src.schemas import Job, html_to_text, looks_remote

log = logging.getLogger(__name__)

TIMEOUT = 20
MAX_FEED_BYTES = 5 * 1024 * 1024
HEADERS = {"User-Agent": "recrutorr/0.1 (student side project)"}
_LOCATION_RE = re.compile(r"(?:location|based in)\s*[:\-]\s*([^\n|]+)", re.IGNORECASE)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()


def _child_text(element: ElementTree.Element, *names: str) -> str:
    name_set = {name.lower() for name in names}
    for child in element:
        if _local_name(child.tag) in name_set:
            return " ".join(part.strip() for part in child.itertext() if part.strip()).strip()
    return ""


def _entry_link(element: ElementTree.Element) -> str:
    for child in element:
        if _local_name(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        rel = (child.attrib.get("rel") or "alternate").lower()
        if href and rel == "alternate":
            return href
        text = " ".join(part.strip() for part in child.itertext() if part.strip()).strip()
        if text:
            return text
    return ""


def _parse_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None


def _validate_source(source: dict[str, Any]) -> tuple[str, str]:
    name = source.get("name")
    url = source.get("url")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("RSS source must have a non-empty name")
    if not isinstance(url, str):
        raise ValueError(f"RSS source '{name}' must have an HTTPS feed URL")
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"RSS source '{name}' must have a valid HTTPS feed URL")
    domains = source.get("domains", [])
    if (
        not isinstance(domains, list)
        or not domains
        or any(not isinstance(domain, str) or not domain.strip() for domain in domains)
    ):
        raise ValueError(f"RSS source '{name}' must configure allowed application domains")
    return name.strip(), url.strip()


def fetch(source: dict[str, Any]) -> List[Job]:
    """Fetch one configured RSS/Atom feed and normalize its job entries."""
    source_name, url = _validate_source(source)
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
    response.raise_for_status()

    body = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > MAX_FEED_BYTES:
            raise ValueError(f"RSS feed '{source_name}' exceeded the 5 MiB response limit")

    root = ElementTree.fromstring(body)
    entries = [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"item", "entry"}
    ]
    jobs: List[Job] = []
    configured_company = str(source.get("company") or source_name).strip()
    configured_location = str(source.get("location") or "").strip()

    for item in entries:
        title = html_to_text(_child_text(item, "title"))
        company = html_to_text(
            _child_text(item, "company", "employer", "organization", "source")
        )
        if not company and source.get("company_in_title"):
            title_company, separator, title_role = title.partition(":")
            if separator and title_company.strip() and title_role.strip():
                company = title_company.strip()
                title = title_role.strip()

        apply_url = _entry_link(item) or _child_text(item, "url", "link")
        if not title or not apply_url:
            continue

        description_raw = _child_text(item, "encoded", "description", "summary", "content")
        description = html_to_text(description_raw)
        company = company or configured_company
        location = html_to_text(_child_text(item, "location", "region")) or configured_location
        if not location:
            location_match = _LOCATION_RE.search(description)
            location = location_match.group(1).strip() if location_match else ""

        item_id = _child_text(item, "guid", "id")
        if not item_id:
            item_id = apply_url
        jobs.append(
            Job(
                company=company[:160],
                title=title[:240],
                location=location[:240],
                description=description[:12000],
                apply_url=apply_url,
                source="rss",
                external_id=item_id[:512],
                remote=looks_remote(location, title, description),
                posted_at=_parse_date(
                    _child_text(item, "pubdate", "published", "updated", "date")
                ),
            )
        )

    log.info("RSS %s: %d jobs", source_name, len(jobs))
    return jobs
