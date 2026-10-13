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
    conn = db.get_db(tmp_path / "test.db")
    try:
        result = fetch_and_verify_all(conn, config)
    finally:
        conn.close()

    assert result["fetched"] == 2
    assert result["verified"] == 2
    assert result["errors"] == []
