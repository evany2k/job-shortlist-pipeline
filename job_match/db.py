"""SQLite storage for scraped jobs.

Every job moves through a status pipeline, so each stage can be re-run or
resumed independently:

    new ──► rule_rejected
     │
     └──► pending_desc ──► pending_ai ──► ai_review_complete
                      └──► description_missing / description_empty /
                           description_failed_404 / description_failed_status_error /
                           request_exception
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Column name -> SQL definition. Also used to migrate older databases.
COLUMNS: dict[str, str] = {
    "job_id": "TEXT PRIMARY KEY",
    "title": "TEXT",
    "company": "TEXT",
    "link": "TEXT",
    "location": "TEXT",
    "posted_iso": "TEXT",
    "description": "TEXT",
    "source_search": "TEXT",
    "first_seen_at": "TEXT",
    "last_seen_at": "TEXT",
    "is_reviewed": "INTEGER DEFAULT 0",
    "status": "TEXT DEFAULT 'new'",
    "filter_reason": "TEXT",
    "ai_score": "INTEGER",
    "ai_reason": "TEXT",
    "ai_model": "TEXT",
    "ai_reviewed_at": "TEXT",
    # Optional manual tracking of what you did with a job.
    "human_status": "TEXT CHECK (human_status IN ('Applied', 'Passed'))",
}


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open the database (creating its folder if needed) with dict-like rows."""
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create the jobs table, adding any columns missing from older databases."""
    columns_sql = ",\n    ".join(f"{name} {definition}" for name, definition in COLUMNS.items())
    conn.execute(f"CREATE TABLE IF NOT EXISTS jobs (\n    {columns_sql}\n)")

    existing = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    for name, definition in COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
    conn.commit()


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Number of jobs in each pipeline status."""
    rows = conn.execute(
        "SELECT status, COUNT(*) FROM jobs GROUP BY status ORDER BY COUNT(*) DESC"
    ).fetchall()
    return {row[0]: row[1] for row in rows}
