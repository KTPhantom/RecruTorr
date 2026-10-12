"""Unit tests for Resume Parser, Explainer, and Delivery formatters."""
from src.delivery import build_job_keyboard, format_job_card
from src.explainer import explain_match
from src.resume_parser import heuristic_parse_resume
from src.schemas import Job, Profile


def test_heuristic_resume_parser():
    sample_text = """
    Rohan Sharma
    B.Tech in Computer Science and Engineering, 2026 graduate.
    Proficient in Python, SQL, Docker, React, and FastApi.
    Built an end-to-end full stack web application with FastAPI and PostgreSQL.
    Interested in Machine Learning and Backend Software Engineering roles.
    """
    profile = heuristic_parse_resume(sample_text, user_id="user_99")
    assert profile.name == "Rohan Sharma"
    assert profile.grad_year == 2026
    assert profile.degree == "B.Tech"
    assert "python" in profile.skills
    assert "sql" in profile.skills
    assert "docker" in profile.skills
    assert "react" in profile.skills
    assert "sde" in profile.target_roles or "ml" in profile.target_roles or "backend" in profile.target_roles


def test_explainer_honest_rules():
    profile = Profile(
        user_id="user_1",
        name="Ananya",
        skills=["python", "fastapi", "react"],
        target_roles=["sde"],
    )
    job = Job(
        company="Razorpay",
        title="Associate Software Engineer",
        location="Bengaluru",
        description="We are seeking developers with Python and React experience to build APIs.",
        apply_url="https://jobs.lever.co/razorpay/1",
        source="lever",
        external_id="1",
        verified=True,
    )

    fit = explain_match(profile, job, matched_skills=["python", "react"], missing_skills=["docker"])
    assert "python" in fit.matched_skills
    assert "react" in fit.matched_skills
    assert len(fit.fit_summary) > 10
    assert any(term in fit.fit_summary.lower() for term in ["python", "react", "skills", "razorpay", "bengaluru"])


def test_delivery_formatting():
    job = Job(
        company="Postman",
        title="Software Engineer Intern",
        location="Bengaluru",
        description="Internship for pre-final and final year students.",
        apply_url="https://jobs.ashbyhq.com/postman/1",
        source="ashby",
        external_id="p1",
        remote=True,
        verified=True,
    )
    card = format_job_card(job)
    assert "Software Engineer Intern" in card
    assert "Postman" in card
    assert "[Remote]" in card

    kb = build_job_keyboard(job)
    # Check that keyboard has Apply link button and callback buttons
    buttons = kb.inline_keyboard
    assert buttons[0][0].url == "https://jobs.ashbyhq.com/postman/1"
    assert buttons[1][0].callback_data.startswith("act:applied:")
    assert buttons[1][1].callback_data.startswith("act:not_relevant:")
    assert buttons[2][0].callback_data.startswith("act:remind:")
