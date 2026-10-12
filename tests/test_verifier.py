"""Unit tests for Agent 2: Verifier."""
from datetime import datetime, timedelta, timezone

from src.schemas import Job
from src.verifier import (
    check_domain_authenticity,
    check_fee_keywords,
    is_fresher_friendly,
    is_job_stale,
    verify_and_deduplicate,
    verify_job,
)


def test_domain_authenticity():
    assert check_domain_authenticity("https://boards.greenhouse.io/stripe/jobs/123") is True
    assert check_domain_authenticity("https://jobs.lever.co/figma/abc") is True
    assert check_domain_authenticity("https://jobs.ashbyhq.com/postman/xyz") is True
    assert check_domain_authenticity("https://careers.google.com/jobs/1", allowed_domains=["google.com"]) is True
    # Non-authentic / third-party scraping domains
    assert check_domain_authenticity("https://sketchy-job-board.xyz/jobs/1") is False


def test_fee_keywords_rejection():
    assert check_fee_keywords("Please pay a registration fee of $50") == "registration fee"
    assert check_fee_keywords("Security deposit required prior to training") == "security deposit"
    assert check_fee_keywords("Standard software engineer compensation and benefits") is None


def test_staleness_check():
    now = datetime.now(timezone.utc)
    fresh_date = now - timedelta(days=5)
    stale_date = now - timedelta(days=45)

    assert is_job_stale(fresh_date, max_days=30) is False
    assert is_job_stale(stale_date, max_days=30) is True


def test_fresher_friendly_check():
    # Senior roles should fail
    friendly, reason = is_fresher_friendly("Senior Backend Engineer", "Build services")
    assert friendly is False

    friendly, reason = is_fresher_friendly("Staff Software Engineer", "Tech leadership")
    assert friendly is False

    friendly, reason = is_fresher_friendly("Software Engineer", "Requires at least 5 years of experience")
    assert friendly is False

    # Fresher-friendly roles should pass
    friendly, reason = is_fresher_friendly("Graduate Software Engineer", "Looking for new graduates in computer science")
    assert friendly is True

    friendly, reason = is_fresher_friendly("Associate Product Manager", "Entry-level role for university graduates")
    assert friendly is True


def test_verify_job_end_to_end():
    now = datetime.now(timezone.utc)

    # Valid job
    valid_job = Job(
        company="Stripe",
        title="Software Engineer - University Graduate",
        location="Bengaluru",
        description="Join Stripe as a new grad engineer.",
        apply_url="https://boards.greenhouse.io/stripe/jobs/101",
        source="greenhouse",
        external_id="101",
        posted_at=now - timedelta(days=2),
    )
    res = verify_job(valid_job)
    assert res.verified is True
    assert res.reject_reason is None

    # Fee scam job
    scam_job = Job(
        company="Shady Corp",
        title="Python Trainee",
        location="Remote",
        description="Requires refundable registration fee of 5000 INR.",
        apply_url="https://boards.greenhouse.io/shady/jobs/102",
        source="greenhouse",
        external_id="102",
        posted_at=now,
    )
    res_scam = verify_job(scam_job)
    assert res_scam.verified is False
    assert "fees/deposits detected" in res_scam.reject_reason


def test_deduplication():
    now = datetime.now(timezone.utc)
    j1 = Job(
        company="Razorpay",
        title="Frontend Engineer",
        location="Bengaluru",
        description="Work on checkout.",
        apply_url="https://jobs.lever.co/razorpay/1",
        source="lever",
        external_id="1",
        posted_at=now,
    )
    j2 = Job(
        company="Razorpay",
        title="frontend engineer",
        location="Bengaluru",
        description="Duplicate entry.",
        apply_url="https://jobs.lever.co/razorpay/2",
        source="lever",
        external_id="2",
        posted_at=now,
    )

    batch = verify_and_deduplicate([j1, j2])
    assert batch[0].verified is True
    assert batch[1].verified is False
    assert "Duplicate" in batch[1].reject_reason
