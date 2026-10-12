"""Unit tests for Agent 4: Matcher."""
from src.matcher import match_for_user, matches_location, matches_role_family, has_dealbreaker
from src.schemas import Job, Profile


def test_hard_filters():
    job_remote = Job(
        company="GitLab",
        title="Backend Engineer",
        location="Remote",
        description="Python backend API engineering",
        apply_url="https://boards.greenhouse.io/gitlab/1",
        source="greenhouse",
        external_id="1",
        remote=True,
        verified=True,
    )
    job_blr = Job(
        company="Uber",
        title="Software Engineer I",
        location="Bengaluru, India",
        description="Distributed systems",
        apply_url="https://boards.greenhouse.io/uber/2",
        source="greenhouse",
        external_id="2",
        remote=False,
        verified=True,
    )

    # Location
    assert matches_location(job_remote, ["remote"]) is True
    assert matches_location(job_blr, ["bangalore", "bengaluru"]) is True
    assert matches_location(job_blr, ["london"]) is False

    # Role family
    assert matches_role_family(job_remote, ["sde", "backend"]) is True
    assert matches_role_family(job_remote, ["sales"]) is False

    # Dealbreakers
    job_bond = Job(
        company="TCS",
        title="Trainee",
        location="Bengaluru",
        description="Requires 2 year service agreement bond.",
        apply_url="https://boards.greenhouse.io/tcs/1",
        source="greenhouse",
        external_id="3",
        verified=True,
    )
    assert has_dealbreaker(job_bond, ["bond"]) is True
    assert has_dealbreaker(job_blr, ["bond"]) is False


def test_ranking_and_matching():
    profile = Profile(
        user_id="user123",
        name="Alex",
        degree="B.Tech",
        grad_year=2026,
        skills=["python", "fastapi", "docker", "sql"],
        target_roles=["sde", "backend"],
        locations=["remote", "bangalore"],
        avoid_companies=["AvoidCorp"],
    )

    j_good = Job(
        company="TechCorp",
        title="Junior Backend Engineer",
        location="Bengaluru",
        description="Looking for Python, FastAPI, and SQL developer to build scalable services.",
        apply_url="https://jobs.lever.co/techcorp/1",
        source="lever",
        external_id="g1",
        verified=True,
    )

    j_avoid = Job(
        company="AvoidCorp",
        title="Junior Python Engineer",
        location="Bengaluru",
        description="Python coding.",
        apply_url="https://jobs.lever.co/avoid/1",
        source="lever",
        external_id="a1",
        verified=True,
    )

    matches = match_for_user(profile, [j_good, j_avoid])
    # AvoidCorp should be filtered out
    assert len(matches) == 1
    matched_job, score, matched_skills, missing_skills = matches[0]
    assert matched_job.company == "TechCorp"
    assert "python" in matched_skills
    assert score > 0.0
