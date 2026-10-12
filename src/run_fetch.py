"""Standalone CLI runner for fetching and verifying jobs.

Usage:
    python -m src.run_fetch
"""
from __future__ import annotations

import json
import logging
import sys
import time

from src import db
from src.orchestrator import fetch_and_verify_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("run_fetch")


def main() -> int:
    conn = db.get_db()
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    log.info("Starting daily fetch and verification run...")

    res = fetch_and_verify_all(conn)

    finished = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        """INSERT INTO runs (started_at, finished_at, fetched, verified, rejected, sent, errors_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (started, finished, res["fetched"], res["verified"], res["rejected"], 0, json.dumps(res["errors"])),
    )
    conn.commit()

    log.info(
        "Run finished! Fetched: %d | Verified: %d | Rejected: %d | Errors: %d",
        res["fetched"], res["verified"], res["rejected"], len(res["errors"])
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
