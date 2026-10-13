"""Agent 4: Matcher

Applies hard filters and ranks verified jobs for each user using vector/text similarity,
skill-overlap boosts, and user feedback penalties.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from src.schemas import Job, Profile

log = logging.getLogger(__name__)


CITY_SYNONYMS = {
    "bangalore": ["bengaluru", "bangalore"],
    "bengaluru": ["bengaluru", "bangalore"],
    "gurgaon": ["gurugram", "gurgaon", "ncr"],
    "gurugram": ["gurugram", "gurgaon", "ncr"],
    "mumbai": ["mumbai", "bombay"],
    "bombay": ["mumbai", "bombay"],
    "chennai": ["chennai", "madras"],
    "madras": ["chennai", "madras"],
    "delhi": ["delhi", "new delhi", "ncr", "noida", "gurgaon", "gurugram"],
    "noida": ["noida", "delhi", "ncr"],
    "hyderabad": ["hyderabad", "secunderabad"],
    "kolkata": ["kolkata", "calcutta"],
}


def matches_location(job: Job, preferred_locations: List[str]) -> bool:
    """Check if job matches location preferences or is remote."""
    if not preferred_locations:
        return True
    loc_clean = (job.location or "").lower()
    normalized_preferences = {pref.lower().strip() for pref in preferred_locations}
    if normalized_preferences & {"any", "anywhere"}:
        return True
    if job.remote and normalized_preferences & {"remote", "wfh"}:
        return True
    for pref_clean in normalized_preferences:
        if pref_clean in {"remote", "wfh"}:
            continue
        elif pref_clean in loc_clean:
            return True
        else:
            # Check city synonyms (e.g. bangalore <-> bengaluru)
            synonyms = CITY_SYNONYMS.get(pref_clean, [])
            if any(syn in loc_clean for syn in synonyms):
                return True
    return False


def matches_role_family(
    job: Job,
    target_roles: List[str],
    custom_titles: Optional[List[str]] = None,
) -> bool:
    """Check if job title or description matches user target roles."""
    if not target_roles and not custom_titles:
        return True
    
    blob = f"{job.title} {job.description[:400]}".lower()
    role_synonyms = {
        "sde": ["software", "developer", "engineer", "sde", "full stack", "backend", "frontend"],
        "frontend": ["frontend", "front-end", "react", "web developer", "ui"],
        "backend": ["backend", "back-end", "python", "node", "java", "api", "database"],
        "data": ["data analyst", "data engineer", "analytics", "sql", "bi developer"],
        "ml": ["machine learning", "ai", "deep learning", "nlp", "computer vision", "data scientist"],
        "qa": ["qa", "quality assurance", "test engineer", "automation tester", "sdet"],
        "product": ["product manager", "product associate", "apm", "associate product manager"],
        "ios": ["ios", "swift", "iphone"],
        "android": ["android", "kotlin"],
        "devops": ["devops", "site reliability", "sre", "platform engineer"],
        "security": ["security engineer", "cybersecurity", "application security"],
        "design": ["product designer", "ux designer", "ui designer"],
    }

    for title in custom_titles or []:
        title_clean = title.lower().strip()
        if title_clean and re.search(r"\b" + re.escape(title_clean) + r"\b", blob):
            return True

    for role in target_roles:
        role_key = role.lower().strip()
        keywords = role_synonyms.get(role_key, [role_key])
        if any(kw in blob for kw in keywords):
            return True

    return False


def matches_work_mode(job: Job, preferred_modes: List[str]) -> bool:
    """Apply optional remote, hybrid, or on-site preferences."""
    if not preferred_modes:
        return True
    text = f"{job.title} {job.location} {job.description}".lower()
    if job.remote or re.search(r"\bremote\b|\bwork from home\b|\bwfh\b|\banywhere\b", text):
        mode = "remote"
    elif re.search(r"\bhybrid\b", text):
        mode = "hybrid"
    else:
        mode = "onsite"
    return mode in {item.lower().replace("-", "").replace("_", "") for item in preferred_modes}


def matches_job_type(job: Job, preferred_types: List[str]) -> bool:
    """Apply optional internship, full-time, contract, and part-time preferences."""
    if not preferred_types:
        return True
    text = f"{job.title} {job.description}".lower()
    if re.search(r"\bintern(?:ship)?\b", text):
        job_type = "internship"
    elif re.search(r"\bcontract(?:or)?\b|\bfreelance\b|\btemporary\b", text):
        job_type = "contract"
    elif re.search(r"\bpart[- ]time\b", text):
        job_type = "part_time"
    else:
        job_type = "full_time"
    normalized = {item.lower().replace("-", "").replace("_", "") for item in preferred_types}
    return job_type.replace("_", "") in normalized


def has_dealbreaker(job: Job, dealbreakers: List[str]) -> bool:
    """Check if job contains dealbreakers like 'bond' or 'unpaid'."""
    if not dealbreakers:
        return False
    text = f"{job.title} {job.description}".lower()
    for db in dealbreakers:
        db_clean = db.lower().strip()
        if db_clean == "unpaid":
            if "unpaid" in text or "no stipend" in text or "without stipend" in text:
                return True
        elif db_clean == "bond":
            if "service agreement" in text or "service bond" in text or "employment bond" in text:
                return True
        elif db_clean in text:
            return True
    return False


def compute_skill_overlap(job_text: str, skills: List[str]) -> Tuple[int, List[str]]:
    """Return count and list of matched skills."""
    matched = []
    text_lower = job_text.lower()
    for s in skills:
        s_clean = s.lower().strip()
        if len(s_clean) >= 2 and re.search(r"\b" + re.escape(s_clean) + r"\b", text_lower):
            matched.append(s)
    return len(matched), matched


def compute_cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Compute cosine similarity between two embedding vectors."""
    a = np.array(vec1, dtype=float)
    b = np.array(vec2, dtype=float)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def score_jobs(
    profile: Profile,
    candidate_jobs: List[Job],
    negative_feedback: Optional[Dict[str, Any]] = None
) -> List[Tuple[Job, float, List[str], List[str]]]:
    """
    Score candidates using TF-IDF / vector similarity, skill overlap, recency, and feedback penalties.
    Returns list of (job, score, matched_skills, missing_skills) sorted by score descending.
    """
    if not candidate_jobs:
        return []

    feedback = negative_feedback or {}
    disliked_companies: Set[str] = feedback.get("companies", set())
    disliked_titles = feedback.get("titles", [])
    disliked_locations = feedback.get("locations", [])
    liked_companies: Set[str] = feedback.get("liked_companies", set())
    liked_titles = feedback.get("liked_titles", [])

    # Build profile text representation
    profile_skills_text = " ".join(profile.skills)
    profile_projects_text = " ".join(f"{p.title} {p.summary}" for p in profile.projects)
    profile_doc = f"{profile.degree} {profile_skills_text} {profile_projects_text} {' '.join(profile.target_roles)}"

    # Compute TF-IDF similarities
    job_docs = [f"{j.title} {j.company} {j.description[:1000]}" for j in candidate_jobs]
    vectorizer = TfidfVectorizer(stop_words="english", max_features=1000)
    try:
        all_docs = [profile_doc] + job_docs
        tfidf_matrix = vectorizer.fit_transform(all_docs)
        sim_scores = (tfidf_matrix[0:1] * tfidf_matrix[1:].T).toarray()[0]
    except Exception:
        sim_scores = [0.1] * len(candidate_jobs)

    now = datetime.now(timezone.utc)
    results = []

    for idx, job in enumerate(candidate_jobs):
        base_sim = float(sim_scores[idx])

        # If both job and profile have vector embeddings, blend cosine similarity
        if profile.summary_embedding and job.embedding:
            emb_sim = compute_cosine_similarity(profile.summary_embedding, job.embedding)
            base_sim = 0.5 * base_sim + 0.5 * emb_sim

        # Skill overlap boost
        overlap_count, matched_skills = compute_skill_overlap(
            f"{job.title} {job.description}", profile.skills
        )
        skill_boost = min(0.25, overlap_count * 0.05)

        # Missing skills (profile skills not in job vs key job terms)
        missing_skills = [s for s in profile.skills if s not in matched_skills][:3]

        # Recency boost (within 7 days => +0.05)
        recency_boost = 0.0
        if job.posted_at:
            dt = job.posted_at if job.posted_at.tzinfo else job.posted_at.replace(tzinfo=timezone.utc)
            if (now - dt).total_seconds() < 7 * 86400:
                recency_boost = 0.05

        # Feedback penalty
        feedback_penalty = 0.0
        if job.company.lower().strip() in disliked_companies:
            feedback_penalty += 0.3
        normalized_title = " ".join(re.findall(r"[a-z0-9]+", job.title.lower()))
        title_tokens = set(normalized_title.split())
        if any(
            normalized_title == title
            or (
                title_tokens
                and len(title_tokens & set(title.split())) / len(title_tokens | set(title.split())) >= 0.6
            )
            for title in disliked_titles
        ):
            feedback_penalty += 0.2
        if any(location in job.location.lower() for location in disliked_locations):
            feedback_penalty += 0.15

        feedback_boost = 0.0
        if job.company.lower().strip() in liked_companies:
            feedback_boost += 0.1
        if any(
            normalized_title == title
            or (
                title_tokens
                and len(title_tokens & set(title.split())) / len(title_tokens | set(title.split())) >= 0.6
            )
            for title in liked_titles
        ):
            feedback_boost += 0.1

        final_score = max(
            0.0,
            base_sim + skill_boost + recency_boost + feedback_boost - feedback_penalty,
        )
        results.append((job, final_score, matched_skills, missing_skills))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def match_for_user(
    profile: Profile,
    verified_jobs: List[Job],
    already_seen_ids: Optional[Set[str]] = None,
    negative_feedback: Optional[Dict[str, Any]] = None,
    limit: int = 10
) -> List[Tuple[Job, float, List[str], List[str]]]:
    """
    Filter and rank jobs for a given profile.
    Returns up to `limit` best matched jobs.
    """
    seen = already_seen_ids or set()
    avoid_companies = {c.lower().strip() for c in profile.avoid_companies}

    # Step 1: Apply Hard Filters
    candidates: List[Job] = []
    for job in verified_jobs:
        if not job.verified:
            continue
        if job.job_id in seen:
            continue
        if job.company.lower().strip() in avoid_companies:
            continue
        if not matches_location(job, profile.locations):
            continue
        if not matches_role_family(job, profile.target_roles, profile.custom_titles):
            continue
        if not matches_work_mode(job, profile.work_modes):
            continue
        if not matches_job_type(job, profile.job_types):
            continue
        if has_dealbreaker(job, profile.dealbreakers):
            continue
        candidates.append(job)

    # Step 2 & 3: Score and Rank
    ranked = score_jobs(profile, candidates, negative_feedback)
    return ranked[:limit]
