"""Prompt construction for LLM job scoring.

The candidate-specific parts (target roles, standout skills, résumé) come from
the user's config so no personal data lives in the code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

SYSTEM_PROMPT_TEMPLATE = """You are an expert ATS screening candidates for {market}.

STRICTNESS: Most jobs are 35-65. 70+ is genuinely good. 85+ is rare. Do NOT inflate.

TARGET ROLES: {target_roles}

RUBRIC:
90-100: All must-haves + {standout_skills}
75-89: Most must-haves, 1 minor gap
50-74: Some core but missing 1-2 critical
25-49: Weak
1-24: No match

RULES: Weigh must-haves > nice-to-haves. If 5+ years/Sr/Lead, score <=40. \
Summary must name 2-3 skills from the resume.
"""

USER_PROMPT_TEMPLATE = """
resume: {resume}
---
JOB:
Title: {title}
Company: {company}
Location: {location}
Description: {description}
---
Task: Score 1-100. Provide Summary of Reasoning
"""


@dataclass
class CandidateProfile:
    market: str = "junior software roles"
    target_roles: list[str] = field(default_factory=list)
    standout_skills: str = "skills that strongly differentiate the candidate"


def build_system_prompt(profile: CandidateProfile) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        market=profile.market,
        target_roles=", ".join(profile.target_roles) or "any role matching the resume",
        standout_skills=profile.standout_skills,
    )


def build_user_prompt(job: Mapping[str, str | None], resume: str) -> str:
    return USER_PROMPT_TEMPLATE.format(
        resume=resume.strip(),
        title=job["title"],
        company=job["company"],
        location=job["location"],
        description=job["description"],
    )
