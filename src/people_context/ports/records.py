"""Narrow persistence ports for non-person records and preferences."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, TypeAlias, runtime_checkable

from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.observation import Observation
from people_context.domain.organization import Affiliation, Organization
from people_context.domain.relationship import Relationship
from people_context.domain.reminder import Reminder, ReminderStatus
from people_context.domain.trait import Trait

Record: TypeAlias = Relationship | Affiliation | Fact | Observation | Trait | Interaction | Reminder


@runtime_checkable
class RecordWriter(Protocol):
    """Persist assertive records and reminders."""

    def save_relationship(self, relationship: Relationship) -> None: ...

    def save_affiliation(self, affiliation: Affiliation) -> None: ...

    def save_fact(self, fact: Fact) -> None: ...

    def save_observation(self, observation: Observation) -> None: ...

    def save_trait(self, trait: Trait) -> None: ...

    def save_interaction(self, interaction: Interaction) -> None: ...

    def save_reminder(self, reminder: Reminder) -> None: ...

    def update_record_fields(self, entity_type: str, entity_id: str, fields: dict[str, Any]) -> Record | None: ...


@runtime_checkable
class RecordReader(Protocol):
    """Read individual records and filtered reminders for curation use cases."""

    def get_record(self, entity_type: str, entity_id: str) -> Record | None: ...

    def list_reminders(
        self,
        person_id: str | None = None,
        due_before: datetime | None = None,
        status: ReminderStatus | None = ReminderStatus.ACTIVE,
    ) -> list[Reminder]: ...


@runtime_checkable
class OrganizationStore(Protocol):
    """Resolve and persist organizations used by affiliations."""

    def get(self, org_id: str) -> Organization | None: ...

    def get_by_normalized_name(self, normalized_name: str) -> Organization | None: ...

    def save(self, organization: Organization) -> None: ...


@runtime_checkable
class PreferencesStore(Protocol):
    """Persist string-valued user preferences."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...
