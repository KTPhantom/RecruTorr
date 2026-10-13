import threading

from src import db
from src.orchestrator import fetch_and_verify_all
from src.schemas import Job


def test_job_board_fetches_run_concurrently(tmp_path, monkeypatch):
    barrier = threading.Barrier(2)

    def make_fetch(source):
        def fetch(company_name, slug):
            barrier.wait(timeout=3)
            return [
                Job(
                    company=company_name,
                    title="Graduate Software Engineer",
                    location="Remote",
                    description="Entry-level software engineering.",
                    apply_url=f"https://{source}.example/jobs/1",
                    source=source,
                    external_id="1",
                    remote=True,
                )
            ]
        return fetch

    monkeypatch.setattr("src.orchestrator.greenhouse.fetch", make_fetch("greenhouse"))
    lever_fetch = make_fetch("lever")
    monkeypatch.setattr(
        "src.orchestrator.lever.fetch",
        lambda company_name, slug, eu=False: lever_fetch(company_name, slug),
    )
    config = tmp_path / "companies.yaml"
    config.write_text(
        "companies:\n"
        "  - name: Green Co\n"
        "    ats: greenhouse\n"
        "    slug: green\n"
        "    domains: [greenhouse.example]\n"
        "  - name: Lever Co\n"
        "    ats: lever\n"
        "    slug: lever\n"
        "    domains: [lever.example]\n",
        encoding="utf-8",
    )
    rss_config = tmp_path / "rss_sources.yaml"
    rss_config.write_text("rss_sources: []\n", encoding="utf-8")
    conn = db.get_db(tmp_path / "test.db")
    try:
        result = fetch_and_verify_all(conn, config, rss_config)
    finally:
        conn.close()

    assert result["fetched"] == 2
    assert result["verified"] == 2
    assert result["errors"] == []


def test_rss_sources_are_fetched_verified_and_stored(tmp_path, monkeypatch):
    def fake_rss_fetch(source):
        if source["name"] == "Broken Feed":
            raise RuntimeError("feed unavailable")
        return [
            Job(
                company="Example Co",
                title="Graduate Software Engineer",
                location="Remote",
                description="Entry-level Python position.",
                apply_url="https://careers.example/jobs/1",
                source="rss",
                external_id="item-1",
                remote=True,
            )
        ]

    monkeypatch.setattr("src.orchestrator.rss.fetch", fake_rss_fetch)
    companies = tmp_path / "companies.yaml"
    companies.write_text("companies: []\n", encoding="utf-8")
    rss_config = tmp_path / "rss_sources.yaml"
    rss_config.write_text(
        "rss_sources:\n"
        "  - name: Example Feed\n"
        "    url: https://example.com/jobs.xml\n"
        "    domains: [careers.example]\n"
        "  - name: Broken Feed\n"
        "    url: https://example.org/jobs.xml\n"
        "    domains: [careers.example]\n",
        encoding="utf-8",
    )
    conn = db.get_db(tmp_path / "rss.db")
    try:
        result = fetch_and_verify_all(conn, companies, rss_config)
        jobs = db.get_verified_jobs(conn)
    finally:
        conn.close()

    assert result["fetched"] == 1
    assert result["verified"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["company"] == "Broken Feed"
    assert [job.source for job in jobs] == ["rss"]
