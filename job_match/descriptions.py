"""Fetch full job descriptions for jobs that passed the rule filter."""

from __future__ import annotations

import logging
import sqlite3

from bs4 import BeautifulSoup
from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from .throttle import Throttle, raise_if_blocked

log = logging.getLogger(__name__)

DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"


def parse_description(html: str) -> str | None:
    markup = BeautifulSoup(html, "html.parser").find("div", class_="show-more-less-html__markup")
    return markup.get_text("\n", strip=True) if markup else None


def fetch_description(
    session: requests.Session, job_id: str, timeout: float = 17
) -> tuple[str | None, int | None]:
    """Return ``(description, status_code)``; status is None if the request itself failed."""
    try:
        resp = session.get(DETAIL_URL.format(job_id), timeout=timeout)
    except RequestException as e:
        log.warning("Request failed for %s: %s", job_id, e)
        return None, None
    if resp.status_code != 200:
        return None, resp.status_code
    return parse_description(resp.text), 200


def classify(description: str | None, status_code: int | None) -> str:
    """Map a fetch result to the job's next pipeline status."""
    if status_code is None:
        return "request_exception"
    if status_code == 404:
        return "description_failed_404"
    if status_code != 200:
        return "description_failed_status_error"
    if description is None:
        return "description_missing"
    if not description.strip():
        return "description_empty"
    return "pending_ai"


def pending_jobs(conn: sqlite3.Connection, posted_since: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT job_id, title FROM jobs
           WHERE status = 'pending_desc' AND posted_iso >= ?
           ORDER BY posted_iso DESC""",
        (posted_since,),
    ).fetchall()


def fetch_all(
    conn: sqlite3.Connection,
    posted_since: str,
    throttle: Throttle,
    timeout: float = 17,
    session: requests.Session | None = None,
) -> int:
    """Fetch descriptions for pending jobs. Returns how many are now ready for review."""
    session = session or requests.Session(impersonate="chrome")
    jobs = pending_jobs(conn, posted_since)
    log.info("%d jobs need descriptions (posted since %s)", len(jobs), posted_since)

    ready = 0
    for i, job in enumerate(jobs, 1):
        throttle.wait()
        log.info("[%d/%d] %s - %s", i, len(jobs), job["job_id"], job["title"])
        while True:
            description, status_code = fetch_description(session, job["job_id"], timeout)
            if status_code != 429:
                break
            throttle.record_rate_limit()

        raise_if_blocked(status_code)
        throttle.record_success()

        status = classify(description, status_code)
        if status != "pending_ai":
            log.warning("%s -> %s (HTTP %s)", job["job_id"], status, status_code)
            description = None
        ready += status == "pending_ai"
        conn.execute(
            "UPDATE jobs SET status = ?, description = COALESCE(?, description) WHERE job_id = ?",
            (status, description, job["job_id"]),
        )
        conn.commit()

    return ready
