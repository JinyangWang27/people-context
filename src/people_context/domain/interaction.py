"""Concise interaction summary involving one or more persons."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from people_context.domain.shared import Provenance, Sensitivity, new_id, utc_now


class Interaction(BaseModel):
    """A summary of an interaction (never a transcript)."""

    id: str = Field(default_factory=new_id)
    summary: str
    occurred_at: datetime = Field(default_factory=utc_now)
    channel: str | None = None
    participant_ids: list[str] = Field(default_factory=list)
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    provenance: Provenance
