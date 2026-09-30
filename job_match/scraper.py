"""Scrape job cards from LinkedIn's public (guest) job-search endpoint."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup, Tag
from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

from .throttle import Throttle, raise_if_blocked

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
PAGE_SIZE = 10
RETRYABLE_STATUS = {500, 502, 503, 504}


@dataclass
class Search:
    """One saved LinkedIn search. Filter codes follow LinkedIn's URL parameters."""

    name: str
    keywords: str
    location: str
    experience: str = ""     # f_E:   1=Internship 2=Entry 3=Associate 4=Mid-Senior
    posted_within: str = ""  # f_TPR: r86400=24h, r604800=7 days
    job_type: str = ""       # f_JT:  F=Full-time, P=Part-time, C=Contract

    def params(self, start: int) -> dict[str, Any]:
        return {
            "keywords": self.keywords,
            "location": self.location,
            "f_E": self.experience,
            "f_TPR": self.posted_within,
            "f_JT": self.job_type,
            "start": start,
        }


@dataclass
class ScraperSettings:
    max_pages_per_search: int = 40
    max_pages_without_new_jobs: int = 2
    max_retries_per_page: int = 3
    max_consecutive_errors: int = 6  # across searches, aborts the whole run
    request_timeout_s: float = 20


def fetch_cards(
    session: requests.Session,
    search: Search,
    start: int,
    throttle: Throttle,
    timeout: float = 20,
) -> list[Tag] | None:
    """Fetch one results page.

    Returns the job cards, ``[]`` when there are no more results (or the page
    should be skipped), or ``None`` on a transient failure worth retrying.
    """
    while True:
        try:
            r = session.get(SEARCH_URL, params=search.params(start), timeout=timeout)
        except RequestException as e:
            log.warning("Request failed: %s", e)
            return None

        log.debug("GET start=%d -> %d (%d chars)", start, r.status_code, len(r.text))
        raise_if_blocked(r.status_code)

        if r.status_code == 429:
            throttle.record_rate_limit()
            continue
        if r.status_code in RETRYABLE_STATUS:
            log.warning("Server error %d", r.status_code)
            return None
        if r.status_code != 200:
            log.warning("Unexpected status %d, skipping page", r.status_code)
            return []

        throttle.record_success()
        return BeautifulSoup(r.text, "html.parser").find_all("div", class_="base-card")


def _text(card: Tag, *args: Any, **kwargs: Any) -> str:
    tag = card.find(*args, **kwargs)
    return tag.get_text(strip=True) if tag else ""


def parse_card(card: Tag, scraped_at: str | None = None) -> dict[str, Any] | None:
    """Extract job fields from a search-result card. Returns None if it has no job id."""
    urn = card.get("data-entity-urn") or ""
    job_id = urn.rsplit(":", 1)[-1] if urn else ""
    if not job_id:
        return None

    link_tag = card.find("a", class_="base-card__full-link")
    time_tag = card.find("time")
    return {
        "job_id": job_id,
        "title": _text(card, "h3"),
        "company": _text(card, "h4"),
        "location": _text(card, "span", class_="job-search-card__location"),
        "link": link_tag["href"].split("?")[0] if link_tag and link_tag.get("href") else "",
        "posted_iso": time_tag.get("datetime") if time_tag else None,
        "scraped_at": scraped_at or datetime.now().astimezone().isoformat(),
    }


def load_seen_ids(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT job_id FROM jobs")}


def save_jobs(conn: sqlite3.Connection, jobs: list[dict[str, Any]], search_name: str) -> None:
    """Insert new jobs; for ones already stored, only refresh ``last_seen_at``."""
    conn.executemany(
        """
        INSERT INTO jobs (job_id, title, company, link, location, posted_iso,
                          source_search, first_seen_at, last_seen_at)
        VALUES (:job_id, :title, :company, :link, :location, :posted_iso,
                :source_search, :scraped_at, :scraped_at)
        ON CONFLICT(job_id) DO UPDATE SET last_seen_at = excluded.last_seen_at
        """,
        [{**job, "source_search": search_name} for job in jobs],
    )
    conn.commit()


def scrape_all(
    conn: sqlite3.Connection,
    searches: list[Search],
    settings: ScraperSettings,
    throttle: Throttle,
    session: requests.Session | None = None,
) -> int:
    """Run every search, paginating until results dry up. Returns new jobs saved."""
    session = session or requests.Session(impersonate="chrome")
    seen = load_seen_ids(conn)
    total_new = 0
    run_errors = 0

    for search in searches:
        log.info("Search '%s'", search.name)
        start = pages = pages_without_new = retries = 0

        while pages < settings.max_pages_per_search:
            if not retries:
                throttle.wait()
            cards = fetch_cards(session, search, start, throttle, settings.request_timeout_s)

            if cards is None:
                retries += 1
                run_errors += 1
                if run_errors >= settings.max_consecutive_errors:
                    log.error("Aborting run after %d consecutive errors", run_errors)
                    return total_new
                if retries >= settings.max_retries_per_page:
                    log.warning("Giving up on '%s' after %d failed attempts", search.name, retries)
                    break
                throttle.pause(60 * retries, f"Retry {retries}/{settings.max_retries_per_page}")
                continue

            retries = run_errors = 0
            if not cards:
                log.info("No more results")
                break

            jobs = [job for job in map(parse_card, cards) if job]
            new_jobs = [job for job in jobs if job["job_id"] not in seen]
            save_jobs(conn, jobs, search.name)
            seen.update(job["job_id"] for job in new_jobs)
            total_new += len(new_jobs)
            log.info("Page %d: %d cards, %d new", pages + 1, len(cards), len(new_jobs))

            pages += 1
            pages_without_new = 0 if new_jobs else pages_without_new + 1
            if pages_without_new >= settings.max_pages_without_new_jobs:
                log.info("%d pages without new jobs, moving on", pages_without_new)
                break
            start += PAGE_SIZE

    return total_new
