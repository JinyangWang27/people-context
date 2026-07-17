"""User preferences, including communication philosophy."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from people_context.domain.shared import utc_now

PREF_COMMUNICATION_PHILOSOPHY = "communication_philosophy"


class CommunicationPhilosophy(BaseModel):
    """The user's free-text communication guidance framework."""

    text: str
    updated_at: datetime = Field(default_factory=utc_now)
