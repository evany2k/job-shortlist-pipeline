"""Command-line entry point for the job-matching pipeline.

    python main.py run        # scrape -> filter -> fetch -> review
    python main.py scrape     # or run any stage on its own
    python main.py status     # jobs per pipeline status
    python main.py top        # best-matching jobs
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

from job_match import db
from job_match.config import ConfigError, load_config, resolve_posted_since, section
from job_match.descriptions import fetch_all
from job_match.filters import FilterSettings, filter_new_jobs
from job_match.llm_gateway import FATAL_ERRORS, LLMClient, MissingAPIKey
from job_match.prompts import CandidateProfile, build_system_prompt
from job_match.review import LLMSettings, review_all
from job_match.scraper import ScraperSettings, Search, scrape_all
from job_match.throttle import Blocked, RateLimitExceeded, Throttle, ThrottleSettings

log = logging.getLogger("job_match")

STAGES = ("scrape", "filter", "fetch", "review")


def load_resume(path: str | Path) -> str:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Résumé not found at {path}. Copy resume.example.txt to {path} and edit it.")
    return path.read_text(encoding="utf-8")


def run_stage(stage: str, conn: sqlite3.Connection, cfg: dict[str, Any], throttle: Throttle) -> None:
    general = cfg.get("general", {})

    if stage == "scrape":
        searches = [section(Search, s) for s in cfg.get("searches", [])]
        if not searches:
            raise ConfigError("No [[searches]] defined in config")
        new = scrape_all(conn, searches, section(ScraperSettings, cfg.get("scraper")), throttle)
        log.info("Scrape done: %d new jobs", new)

    elif stage == "filter":
        filter_new_jobs(conn, section(FilterSettings, cfg.get("filters")))

    elif stage == "fetch":
        ready = fetch_all(conn, resolve_posted_since(general), throttle)
        log.info("Fetch done: %d jobs ready for review", ready)

    elif stage == "review":
        settings = section(LLMSettings, cfg.get("llm"))
        profile = section(CandidateProfile, cfg.get("candidate"))
        client = LLMClient.from_env(
            request_timeout_s=settings.request_timeout_s,
            overall_timeout_s=settings.overall_timeout_s,
            attempts=settings.attempts_per_model,
            log_path=settings.log_path,
        )
        reviewed = review_all(
            conn,
            client,
            settings,
            build_system_prompt(profile),
            load_resume(general.get("resume_path", "resume.txt")),
            resolve_posted_since(general),
        )
        log.info("Review done: %d jobs scored", reviewed)


def print_status(conn: sqlite3.Connection) -> None:
    counts = db.status_counts(conn)
    width = max((len(s or "") for s in counts), default=6)
    for status, count in counts.items():
        print(f"{status or '(none)':<{width}}  {count:>6}")
    print(f"{'total':<{width}}  {sum(counts.values()):>6}")


def print_top(conn: sqlite3.Connection, limit: int, min_score: int) -> None:
    rows = conn.execute(
        """SELECT ai_score, title, company, location, link, ai_reason FROM jobs
           WHERE status = 'ai_review_complete' AND ai_score >= ? AND human_status IS NULL
           ORDER BY ai_score DESC, posted_iso DESC LIMIT ?""",
        (min_score, limit),
    ).fetchall()
    for r in rows:
        print(f"[{r['ai_score']:>3}] {r['title']} @ {r['company']} ({r['location']})")
        print(f"      {r['link']}")
        print(f"      {r['ai_reason']}\n")
    if not rows:
        print("No reviewed jobs match.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape, filter and LLM-rank LinkedIn jobs.")
    parser.add_argument("-c", "--config", default="config.toml", help="path to config file")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="run every stage in order")
    sub.add_parser("scrape", help="collect job cards from LinkedIn searches")
    sub.add_parser("filter", help="apply rule-based pre-screen to new jobs")
    sub.add_parser("fetch", help="fetch full descriptions for filtered jobs")
    sub.add_parser("review", help="score jobs against your résumé with an LLM")
    sub.add_parser("status", help="show how many jobs are in each status")
    top = sub.add_parser("top", help="list the best-scoring jobs")
    top.add_argument("-n", "--limit", type=int, default=15)
    top.add_argument("--min-score", type=int, default=70)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        cfg = load_config(args.config)
        conn = db.connect(cfg.get("general", {}).get("db_path", "data/jobs.db"))
    except ConfigError as e:
        log.error("%s", e)
        return 1

    try:
        db.init_db(conn)
        if args.command == "status":
            print_status(conn)
        elif args.command == "top":
            print_top(conn, args.limit, args.min_score)
        else:
            throttle = Throttle(section(ThrottleSettings, cfg.get("throttle")))
            stages = STAGES if args.command == "run" else (args.command,)
            for stage in stages:
                run_stage(stage, conn, cfg, throttle)
    except (ConfigError, MissingAPIKey, RateLimitExceeded, Blocked, *FATAL_ERRORS) as e:
        log.error("%s: %s", type(e).__name__, e)
        return 1
    except KeyboardInterrupt:
        log.warning("Interrupted; progress so far is saved")
        return 130
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
