"""Structured output schema for LLM job reviews."""

from pydantic import BaseModel, Field


class JobReview(BaseModel):
    match_score: int = Field(
        ge=1,
        le=100,
        description="90-100=excellent, 75-89=good, 50-74=partial, 1-49=not good. "
        "Be strict, most jobs are 35-65",
    )
    summary: str = Field(description="1-3 sentences explaining the score")
