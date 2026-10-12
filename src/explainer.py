"""Agent 5: Explainer

Generates a short, honest fit note per matched job.
Uses an LLM for nuanced phrasing (or a strict deterministic template fallback).
Rules: Only reference skills present in the resume or job text; no invented claims.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import List, Optional

from src.schemas import FitExplanation, Job, Profile

log = logging.getLogger(__name__)


def generate_rule_based_explanation(
    profile: Profile,
    job: Job,
    matched_skills: List[str],
    missing_skills: List[str]
) -> FitExplanation:
    """Deterministic, honest fit note generation."""
    matched_str = ", ".join(matched_skills[:4]) if matched_skills else "General technical foundation"
    summary_parts = []
    
    if matched_skills:
        summary_parts.append(f"Strong overlap with your skills in {matched_str}.")
    else:
        summary_parts.append(f"Aligns with your interest in {', '.join(profile.target_roles[:2]) or 'engineering'}.")

    if job.remote:
        summary_parts.append("Remote-friendly role.")
    elif job.location:
        summary_parts.append(f"Based in {job.location}.")

    fit_summary = " ".join(summary_parts)
    return FitExplanation(
        fit_summary=fit_summary,
        matched_skills=matched_skills[:5],
        missing_skills=missing_skills[:3],
    )


def generate_llm_explanation(
    profile: Profile,
    job: Job,
    matched_skills: List[str],
    missing_skills: List[str]
) -> Optional[FitExplanation]:
    """Generate concise fit note via LLM if available."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI
        base_url = os.getenv("OPENAI_BASE_URL")
        client = OpenAI(api_key=api_key, base_url=base_url if base_url else None)

        prompt = (
            "You are an honest career advisor for college freshers.\n"
            "Generate a 1-2 sentence fit note for this student and job. Follow these rules strictly:\n"
            "1. ONLY mention skills actually present in the student's profile or job description.\n"
            "2. Never invent qualifications or experience.\n"
            "3. Do not include random match percentages.\n"
            "Return JSON ONLY with keys: 'fit_summary' (string), 'matched_skills' (list of strings), 'missing_skills' (list of strings).\n\n"
            f"Student: Degree={profile.degree}, Skills={profile.skills}, Target Roles={profile.target_roles}\n"
            f"Job: Title={job.title}, Company={job.company}, Location={job.location}\n"
            f"Detected Skill Overlap: {matched_skills}\n"
        )

        resp = client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
        )

        content = resp.choices[0].message.content
        if not content:
            return None

        # Clean potential markdown fences
        clean_json = content.strip()
        if "```" in clean_json:
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean_json, re.DOTALL)
            if m:
                clean_json = m.group(1)
            else:
                m_brace = re.search(r"\{.*\}", clean_json, re.DOTALL)
                if m_brace:
                    clean_json = m_brace.group(0)

        data = json.loads(clean_json)

        return FitExplanation(
            fit_summary=data.get("fit_summary", ""),
            matched_skills=data.get("matched_skills", matched_skills),
            missing_skills=data.get("missing_skills", missing_skills),
        )
    except Exception as exc:
        log.warning("LLM explanation generation failed, using fallback: %s", exc)
        return None


def explain_match(
    profile: Profile,
    job: Job,
    matched_skills: Optional[List[str]] = None,
    missing_skills: Optional[List[str]] = None
) -> FitExplanation:
    """Master entry point for Agent 5: Explainer."""
    m_skills = matched_skills or []
    miss_skills = missing_skills or []

    # Try LLM first if API key configured
    llm_result = generate_llm_explanation(profile, job, m_skills, miss_skills)
    if llm_result and llm_result.fit_summary:
        return llm_result

    # Fallback to deterministic rules
    return generate_rule_based_explanation(profile, job, m_skills, miss_skills)
