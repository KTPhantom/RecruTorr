"""Shared data structures and helpers for OffCampus Matcher."""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit


@dataclass
class Job:
    company: str
    title: str
    location: str
    description: str
    apply_url: str
    source: str  # "greenhouse" | "lever" | "ashby" | "smartrecruiters" | "rss" | "manual"
    external_id: str
    remote: bool = False
    posted_at: Optional[datetime] = None
    # Filled in by the verifier
    verified: bool = False
    reject_reason: Optional[str] = None
    fresher_friendly: Optional[bool] = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    embedding: Optional[List[float]] = None

    @property
    def job_id(self) -> str:
        """Short stable id (safe to use in Telegram callback data)."""
        raw = f"{self.source}:{self.company}:{self.external_id}"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["job_id"] = self.job_id
        d["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        d["fetched_at"] = self.fetched_at.isoformat()
        return d

    @classmethod
    def from_row(cls, row: Any) -> "Job":
        """Convert a sqlite3.Row or dict to a Job instance."""
        posted_at = None
        if row["posted_at"]:
            try:
                posted_at = datetime.fromisoformat(row["posted_at"])
            except Exception:
                pass
        
        fetched_at = datetime.now(timezone.utc)
        if "fetched_at" in row and row["fetched_at"]:
            try:
                fetched_at = datetime.fromisoformat(row["fetched_at"])
            except Exception:
                pass

        emb = None
        if "embedding" in row and row["embedding"]:
            try:
                emb = json.loads(row["embedding"]) if isinstance(row["embedding"], str) else row["embedding"]
            except Exception:
                emb = None

        return cls(
            company=row["company"],
            title=row["title"],
            location=row["location"] or "",
            description=row["description"] or "",
            apply_url=row["apply_url"],
            source=row["source"],
            external_id=row["external_id"],
            remote=bool(row["remote"]),
            posted_at=posted_at,
            verified=bool(row["verified"]),
            reject_reason=row["reject_reason"],
            fresher_friendly=bool(row["fresher_friendly"]) if row["fresher_friendly"] is not None else None,
            fetched_at=fetched_at,
            embedding=emb,
        )


def canonicalize_job_url(url: str) -> str:
    """Normalize a listing URL for cross-source duplicate detection."""
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return ""
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname.lower().rstrip(".")
        try:
            ip = ipaddress.ip_address(hostname)
            host = f"[{hostname}]" if ip.version == 6 else hostname
        except ValueError:
            host = hostname
        port = parsed.port
        if port and port != (443 if scheme == "https" else 80):
            host = f"{host}:{port}"
        path = parsed.path.rstrip("/") or "/"
        return urlunsplit((scheme, host, path, "", ""))
    except ValueError:
        return ""


@dataclass
class ProjectItem:
    title: str
    summary: str
    tech: List[str] = field(default_factory=list)


@dataclass
class ExperienceItem:
    org: str
    role: str
    months: int = 0


@dataclass
class Profile:
    user_id: str
    name: str = ""
    grad_year: int = 2026
    degree: str = "B.Tech"
    skills: List[str] = field(default_factory=list)
    projects: List[ProjectItem] = field(default_factory=list)
    experience: List[ExperienceItem] = field(default_factory=list)
    target_roles: List[str] = field(default_factory=list)  # e.g. ["sde", "data", "ml", "qa", "product"]
    custom_titles: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)     # e.g. ["bangalore", "remote"]
    work_modes: List[str] = field(default_factory=list)    # e.g. ["remote", "hybrid"]
    job_types: List[str] = field(default_factory=list)     # e.g. ["internship", "full_time"]
    digest_frequency: str = "daily"
    preferences_configured: bool = False
    min_stipend_inr: int = 0
    dealbreakers: List[str] = field(default_factory=list)  # e.g. ["unpaid", "bond", "service-company"]
    avoid_companies: List[str] = field(default_factory=list)
    summary_embedding: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "name": self.name,
            "grad_year": self.grad_year,
            "degree": self.degree,
            "skills": self.skills,
            "projects": [asdict(p) for p in self.projects],
            "experience": [asdict(e) for e in self.experience],
            "target_roles": self.target_roles,
            "custom_titles": self.custom_titles,
            "locations": self.locations,
            "work_modes": self.work_modes,
            "job_types": self.job_types,
            "digest_frequency": self.digest_frequency,
            "preferences_configured": self.preferences_configured,
            "min_stipend_inr": self.min_stipend_inr,
            "dealbreakers": self.dealbreakers,
            "avoid_companies": self.avoid_companies,
            "summary_embedding": self.summary_embedding,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Profile":
        projects = [
            ProjectItem(**p) if isinstance(p, dict) else p
            for p in data.get("projects", [])
        ]
        experience = [
            ExperienceItem(**e) if isinstance(e, dict) else e
            for e in data.get("experience", [])
        ]
        return cls(
            user_id=str(data.get("user_id", "")),
            name=data.get("name", ""),
            grad_year=data.get("grad_year", 2026),
            degree=data.get("degree", "B.Tech"),
            skills=data.get("skills", []),
            projects=projects,
            experience=experience,
            target_roles=data.get("target_roles", []),
            custom_titles=data.get("custom_titles", []),
            locations=data.get("locations", []),
            work_modes=data.get("work_modes", []),
            job_types=data.get("job_types", []),
            digest_frequency=data.get("digest_frequency", "daily"),
            preferences_configured=data.get("preferences_configured", False),
            min_stipend_inr=data.get("min_stipend_inr", 0),
            dealbreakers=data.get("dealbreakers", []),
            avoid_companies=data.get("avoid_companies", []),
            summary_embedding=data.get("summary_embedding"),
        )


@dataclass
class FitExplanation:
    fit_summary: str
    matched_skills: List[str] = field(default_factory=list)
    missing_skills: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(raw: str) -> str:
    """Unescape HTML and strip tags cleanly while preserving whitespace line breaks."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"</(p|div|li|h[1-6]|br)>", "\n", text, flags=re.I)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def looks_remote(*parts: str) -> bool:
    blob = " ".join(p for p in parts if p).lower()
    return "remote" in blob or "work from home" in blob or "wfh" in blob or "anywhere" in blob
