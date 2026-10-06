"""Score jobs against the candidate's résumé with an LLM."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .llm_gateway import LLMClient
from .prompts import build_user_prompt
from .schemas import JobReview

log = logging.getLogger(__name__)


@dataclass
class LLMSettings:
    models: list[str] = field(default_factory=list)  # tried in order until one succeeds
    request_timeout_s: float = 45
    overall_timeout_s: float = 90
    attempts_per_model: int = 2
    batch_size: int = 100
    log_path: str = "data/llm_debug.jsonl"


def pending_jobs(conn: sqlite3.Connection, posted_since: str, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT job_id, title, company, location, description FROM jobs
           WHERE status = 'pending_ai' AND posted_iso >= ?
           ORDER BY posted_iso DESC LIMIT ?""",
        (posted_since, limit),
    ).fetchall()


def review_job(
    client: LLMClient, models: list[str], system_prompt: str, user_prompt: str
) -> tuple[JobReview, str] | None:
    """Try each model in turn; return the first valid review."""
    for model in models:
        result = client.structured_completion(model, JobReview, system_prompt, user_prompt)
        if result:
            return result
    return None


def save_review(conn: sqlite3.Connection, job_id: str, review: JobReview, model: str) -> None:
    conn.execute(
        """UPDATE jobs
           SET ai_score = ?, ai_reason = ?, ai_model = ?, ai_reviewed_at = ?,
               status = 'ai_review_complete', is_reviewed = 1
           WHERE job_id = ?""",
        (review.match_score, review.summary, model, datetime.now(timezone.utc).isoformat(), job_id),
    )
    conn.commit()


def review_all(
    conn: sqlite3.Connection,
    client: LLMClient,
    settings: LLMSettings,
    system_prompt: str,
    resume: str,
    posted_since: str,
) -> int:
    """Review up to ``batch_size`` pending jobs. Returns how many were scored."""
    if not settings.models:
        raise ValueError("No models configured under [llm].models")

    jobs = pending_jobs(conn, posted_since, settings.batch_size)
    log.info("%d jobs to review (posted since %s)", len(jobs), posted_since)

    reviewed = 0
    for i, job in enumerate(jobs, 1):
        log.info("[%d/%d] %s @ %s", i, len(jobs), job["title"], job["company"])
        result = review_job(client, settings.models, system_prompt, build_user_prompt(job, resume))
        if not result:
            log.warning("All models failed for %s; will retry next run", job["job_id"])
            continue
        review, model = result
        save_review(conn, job["job_id"], review, model)
        reviewed += 1
        log.info("Score %d (%s): %s", review.match_score, model, review.summary)

    return reviewed
