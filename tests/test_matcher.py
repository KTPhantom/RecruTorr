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


def test_custom_location_role_work_mode_and_job_type_preferences():
    profile = Profile(
        user_id="user456",
        locations=["Pune"],
        custom_titles=["iOS Developer"],
        work_modes=["hybrid"],
        job_types=["internship"],
    )
    matching_job = Job(
        company="Example Co",
        title="iOS Developer Intern",
        location="Pune, India",
        description="Hybrid internship using Swift.",
        apply_url="https://jobs.lever.co/example/1",
        source="lever",
        external_id="hybrid-intern",
        verified=True,
    )
    remote_job = Job(
        company="Example Co",
        title="iOS Developer Intern",
        location="Remote",
        description="Remote internship using Swift.",
        apply_url="https://jobs.lever.co/example/2",
        source="lever",
        external_id="remote-intern",
        remote=True,
        verified=True,
    )
    full_time_job = Job(
        company="Example Co",
        title="iOS Developer",
        location="Pune, India",
        description="Hybrid full-time role using Swift.",
        apply_url="https://jobs.lever.co/example/3",
        source="lever",
        external_id="hybrid-fulltime",
        verified=True,
    )

    matches = match_for_user(profile, [matching_job, remote_job, full_time_job])

    assert [match[0].external_id for match in matches] == ["hybrid-intern"]


def test_positive_feedback_boosts_similar_company():
    from src.matcher import score_jobs

    profile = Profile(user_id="feedback-user", skills=["python"], target_roles=["sde"])
    liked = Job(
        company="Liked Co",
        title="Software Engineer",
        location="Remote",
        description="Python software engineer",
        apply_url="https://jobs.lever.co/liked/1",
        source="lever",
        external_id="liked",
        remote=True,
        verified=True,
    )
    other = Job(
        company="Other Co",
        title="Software Engineer",
        location="Remote",
        description="Python software engineer",
        apply_url="https://jobs.lever.co/other/1",
        source="lever",
        external_id="other",
        remote=True,
        verified=True,
    )

    scores = score_jobs(
        profile,
        [liked, other],
        negative_feedback={"liked_companies": {"liked co"}},
    )

    assert scores[0][0].company == "Liked Co"
    assert scores[0][1] > scores[1][1]
