"""Agent 3: Resume Parser

Converts a resume PDF into structured Profile data.
Extracts text via PyPDF2 and parses into Profile schema via LLM (or robust heuristic fallback).
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from PyPDF2 import PdfReader

from src.schemas import ExperienceItem, Profile, ProjectItem

log = logging.getLogger(__name__)

# Common tech skills dictionary for regex heuristic extraction
KNOWN_TECH_SKILLS = [
    "python", "javascript", "typescript", "c++", "c", "c#", "java", "golang", "rust",
    "sql", "postgresql", "mysql", "mongodb", "redis", "sqlite",
    "react", "nextjs", "vue", "angular", "node.js", "nodejs", "express", "django", "fastapi", "flask",
    "docker", "kubernetes", "aws", "gcp", "azure", "git", "linux",
    "machine learning", "deep learning", "nlp", "computer vision", "pytorch", "tensorflow",
    "pandas", "numpy", "scikit-learn", "html", "css", "graphql", "rest api"
]


def extract_text_from_pdf(pdf_source: bytes | io.BytesIO | str) -> str:
    """Extract clean text content from a PDF file path or byte stream."""
    try:
        reader = PdfReader(pdf_source)
        text_parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
        return "\n".join(text_parts).strip()
    except Exception as exc:
        log.error("Failed to extract text from PDF: %s", exc)
        return ""


def heuristic_parse_resume(
    text: str,
    user_id: str,
    form_answers: Optional[Dict[str, Any]] = None
) -> Profile:
    """Deterministic fallback parser when LLM is unavailable or fails validation."""
    answers = form_answers or {}
    text_lower = text.lower()

    # 1. Degree detection
    degree = answers.get("degree")
    if not degree:
        if "b.tech" in text_lower or "bachelor of technology" in text_lower:
            degree = "B.Tech"
        elif "b.e" in text_lower or "bachelor of engineering" in text_lower:
            degree = "B.E"
        elif "m.tech" in text_lower or "master of technology" in text_lower:
            degree = "M.Tech"
        elif "bca" in text_lower:
            degree = "BCA"
        elif "mca" in text_lower:
            degree = "MCA"
        elif "b.sc" in text_lower:
            degree = "B.Sc"
        else:
            degree = "B.Tech"

    # 2. Grad year detection
    grad_year = answers.get("grad_year")
    if not grad_year:
        year_matches = re.findall(r"\b(202[4-9])\b", text)
        if year_matches:
            # take latest plausible grad year
            grad_year = max(int(y) for y in year_matches)
        else:
            grad_year = 2026

    # 3. Skills extraction
    skills = set(answers.get("skills", []))
    for skill in KNOWN_TECH_SKILLS:
        if re.search(r"\b" + re.escape(skill) + r"\b", text_lower):
            skills.add(skill)

    # 4. Roles
    target_roles = answers.get("target_roles", [])
    if not target_roles:
        if any(w in text_lower for w in ["machine learning", "deep learning", "pytorch", "nlp"]):
            target_roles.append("ml")
        if any(w in text_lower for w in ["frontend", "react", "html", "css"]):
            target_roles.append("frontend")
        if any(w in text_lower for w in ["backend", "sql", "api", "database"]):
            target_roles.append("backend")
        if not target_roles:
            target_roles = ["sde"]

    name = answers.get("name", "")
    if not name and text:
        non_empty_lines = [line.strip() for line in text.splitlines() if line.strip()]
        if non_empty_lines:
            candidate = non_empty_lines[0]
            if len(candidate) < 40 and not any(char.isdigit() for char in candidate):
                name = candidate

    return Profile(
        user_id=str(user_id),
        name=name or "Student",
        grad_year=int(grad_year),
        degree=degree,
        skills=sorted(list(skills)),
        projects=[
            ProjectItem(title=p.get("title", ""), summary=p.get("summary", ""), tech=p.get("tech", []))
            for p in answers.get("projects", [])
        ],
        experience=[
            ExperienceItem(org=e.get("org", ""), role=e.get("role", ""), months=int(e.get("months", 0)))
            for e in answers.get("experience", [])
        ],
        target_roles=target_roles,
        locations=answers.get("locations", ["bangalore", "remote"]),
        min_stipend_inr=answers.get("min_stipend_inr", 0),
        dealbreakers=answers.get("dealbreakers", []),
        avoid_companies=answers.get("avoid_companies", []),
    )


def parse_resume_with_llm(
    text: str,
    user_id: str,
    form_answers: Optional[Dict[str, Any]] = None
) -> Optional[Profile]:
    """Parse resume text using OpenAI or OpenAI-compatible LLM endpoint."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    try:
        from openai import OpenAI
        base_url = os.getenv("OPENAI_BASE_URL")
        client = OpenAI(api_key=api_key, base_url=base_url if base_url else None)

        prompt = (
            "You are a strict resume parsing agent. Extract structured profile data from the resume text.\n"
            "Return JSON ONLY matching the following schema. Unknown fields MUST be null or empty list, NEVER guessed.\n"
            "Schema:\n"
            "{\n"
            '  "name": "string",\n'
            '  "grad_year": 2026,\n'
            '  "degree": "string",\n'
            '  "skills": ["string"],\n'
            '  "projects": [{"title": "string", "summary": "string", "tech": ["string"]}],\n'
            '  "experience": [{"org": "string", "role": "string", "months": 0}],\n'
            '  "target_roles": ["sde" | "data" | "ml" | "qa" | "product"],\n'
            '  "locations": ["string"]\n'
            "}\n\n"
            f"Resume Text:\n{text[:4000]}"
        )

        resp = client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )

        content = resp.choices[0].message.content
        if not content:
            return None

        # Clean potential markdown fences ```json ... ```
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

        merged = dict(form_answers or {})
        for k, v in data.items():
            if v:
                merged[k] = v
        merged["user_id"] = str(user_id)

        return Profile.from_dict(merged)
    except Exception as exc:
        log.warning("LLM resume parsing failed, will use heuristic: %s", exc)
        return None


def parse_resume(
    pdf_source: bytes | io.BytesIO | str,
    user_id: str,
    form_answers: Optional[Dict[str, Any]] = None
) -> Profile:
    """Master entry point for Agent 3: extracts text from PDF and parses into Profile."""
    text = extract_text_from_pdf(pdf_source)
    if not text:
        log.warning("No text could be extracted from PDF, using form answers only.")
        return heuristic_parse_resume("", user_id, form_answers)

    # Try LLM if configured
    profile = parse_resume_with_llm(text, user_id, form_answers)
    if profile:
        return profile

    # Fallback to heuristic parser
    return heuristic_parse_resume(text, user_id, form_answers)
