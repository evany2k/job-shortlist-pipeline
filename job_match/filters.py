"""Cheap rule-based pre-screen, run before fetching descriptions or calling an LLM.

Rejects senior/management titles, non-tech roles, (optionally) internships and
locations that are too far away. Matching is accent- and case-insensitive so
French and English postings are handled the same way.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Seniority markers, English + French. Patterns run on normalized (accent-free) text.
SENIORITY_PATTERNS = [
    r"\bsenior\b", r"\bsr\b", r"\bsnr\b",
    r"\blead\b", r"\bprincipal\b", r"\bstaff\b", r"\bmanager\b", r"\bdirector\b",
    r"\bhead of\b", r"\bchief\b",
    r"\bgestionnaire\b", r"\bdirecteur\b", r"\bdirectrice\b",
    r"\bchef de projet\b", r"\bchef d'equipe\b", r"\bresponsable principal\b",
]

# Word boundaries so e.g. "intern" doesn't match "international" or "internal".
INTERNSHIP_PATTERNS = [
    r"\binterns?\b", r"\binternship\b", r"\bstagiaire\b", r"\bstage\b",
    r"\bco-?op\b", r"\bco op\b", r"\bcooperatif\b",
]

NON_TECH_TERMS = [
    "preparateur de commandes", "order preparation",
    "technician - histology", "technicien - histologie", "histology",
    "infirmier", "infirmiere", "nurse", "pharmacien", "comptable", "accountant",
    "assistant laboratoire degustation", "store clerk", "busboy",
]

# A title must contain at least one of these to be worth a closer look.
TECH_KEYWORDS = [
    # software
    "software", "developer", "developpeur", "developpeuse", "logiciel", "programmeur",
    "programmer", "full stack", "fullstack", "backend", "front-end", "frontend", "application",
    # languages / tools
    "python", "java", "c++", "c#", "dotnet", ".net", "sql", "bash", "git",
    # data / AI
    "data", "donnees", "analyst", "analyste", "analytics", "scientist", "scientifique",
    "intelligence artificielle", "machine learning", "power bi", "algorithm", "algorithme",
    "cloud", "devops", "sre", "reliability", "integration", "integrateur",
    # systems / lab informatics
    "systems", "systemes", "informatique", "ingenieur", "bioinformatic", "lims", "erp",
    # entry-level markers
    "university grad", "new grad", "recent grad", "junior", "entry level",
    # short tokens
    "ai", "bi",
]


def normalize(text: str | None) -> str:
    """Lowercase and strip accents, e.g. 'Développeur' -> 'developpeur'."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    kw = re.escape(normalize(keyword))
    # \b doesn't work next to symbols like "+" or "#", so only anchor word characters.
    prefix = r"\b" if keyword[0].isalnum() else ""
    suffix = r"\b" if keyword[-1].isalnum() else ""
    return re.compile(prefix + kw + suffix)


_SENIORITY_RE = [re.compile(p) for p in SENIORITY_PATTERNS]
_INTERNSHIP_RE = [re.compile(p) for p in INTERNSHIP_PATTERNS]
_TECH_RE = [_keyword_pattern(k) for k in TECH_KEYWORDS]


@dataclass
class FilterSettings:
    allow_internships: bool = True
    allow_far_locations: bool = False
    strict_core_only: bool = False      # keep only jobs in core_locations (or remote)
    core_locations: list[str] = field(default_factory=list)
    far_locations: list[str] = field(default_factory=list)
    company_blacklist: list[str] = field(default_factory=list)


def rule_filter(job: Mapping[str, str | None], settings: FilterSettings) -> tuple[bool, str]:
    """Decide whether a job passes the pre-screen. Returns (passed, reason)."""
    title_raw = (job.get("title") or "").strip()
    location_raw = (job.get("location") or "").strip()
    title = normalize(title_raw)
    company = normalize(job.get("company"))
    location = normalize(location_raw)

    for name in settings.company_blacklist:
        if normalize(name) in company:
            return False, f"company blacklist '{name}'"

    for pattern in _SENIORITY_RE:
        if pattern.search(title):
            return False, f"seniority '{pattern.pattern}' in title: {title_raw}"

    if not settings.allow_internships:
        if any(p.search(title) for p in _INTERNSHIP_RE):
            return False, f"internship: {title_raw}"

    for term in NON_TECH_TERMS:
        if term in title:
            return False, f"non-tech domain '{term}'"

    if not any(p.search(title) for p in _TECH_RE):
        return False, f"no tech keyword in '{title_raw}'"

    is_remote = "remote" in title or "remote" in location or "teletravail" in location
    if not is_remote:
        if not settings.allow_far_locations:
            for far in settings.far_locations:
                if normalize(far) in location:
                    return False, f"far location '{far}': {location_raw}"
        if settings.strict_core_only and settings.core_locations:
            if not any(normalize(core) in location for core in settings.core_locations):
                return False, f"outside core locations: {location_raw}"

    return True, "passed rules"


def filter_new_jobs(conn: sqlite3.Connection, settings: FilterSettings) -> tuple[int, int]:
    """Apply the rules to every job with status 'new'. Returns (kept, total)."""
    jobs = conn.execute(
        "SELECT job_id, title, company, location FROM jobs WHERE status = 'new'"
    ).fetchall()

    kept = 0
    for job in jobs:
        passed, reason = rule_filter(dict(job), settings)
        kept += passed
        conn.execute(
            "UPDATE jobs SET status = ?, filter_reason = ? WHERE job_id = ?",
            ("pending_desc" if passed else "rule_rejected", reason, job["job_id"]),
        )
    conn.commit()

    if jobs:
        log.info("Kept %d / %d jobs (%.1f%%)", kept, len(jobs), 100 * kept / len(jobs))
    else:
        log.info("No new jobs to filter")
    return kept, len(jobs)
