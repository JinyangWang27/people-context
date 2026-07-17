"""Person-linked reminder / follow-up."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from people_context.domain.shared import new_id, utc_now


class ReminderKind(StrEnum):
    """Kind of reminder."""

    FOLLOW_UP = "follow_up"
    OCCASION = "occasion"
    COMMUNICATION_NOTE = "communication_note"


class ReminderStatus(StrEnum):
    """Lifecycle status of a reminder."""

    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Reminder(BaseModel):
    """A follow-up, occasion, or communication note linked to a person."""

    id: str = Field(default_factory=new_id)
    person_id: str
    text: str
    kind: ReminderKind
    due_at: datetime | None = None
    recurrence: str | None = None
    status: ReminderStatus = ReminderStatus.ACTIVE
    created_at: datetime = Field(default_factory=utc_now)
