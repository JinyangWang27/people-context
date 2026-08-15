"""Read-side port for the stored evidence the data-quality report reasons about.

The reader finds *candidate* evidence and nothing else. It never decides that two rows are a
problem, never assigns a finding code, and never orders or phrases anything: handle precedence,
period overlap, grouping, messages, and suggested actions are all application policy, so the
whole report stays testable against fakes and stays identical across storage backends.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

#: `source` value used for a person's own canonical name.
CANONICAL_NAME_SOURCE = "canonical_name"
#: Prefix used for alias-backed name material, completed with the stored alias kind.
ALIAS_SOURCE_PREFIX = "alias:"

#: Reference tables that can still point at a soft-deleted person.
RELATIONSHIP_REFERENCE = "relationship"
AFFILIATION_REFERENCE = "affiliation"
INTERACTION_REFERENCE = "interaction"


@dataclass(frozen=True)
class PersonRef:
    """A stored person addressed by stable id.

    `is_self` travels with the reference because a suggested merge has a required direction:
    the self person must be the primary target, and the report cannot propose a merge that the
    merge use case would refuse.
    """

    person_id: str
    name: str
    is_self: bool = False


@dataclass(frozen=True)
class NameUsage:
    """One stored name value belonging to one active person.

    The reader returns usages only for normalized values that at least two distinct active
    people share, so the application never has to scan the whole store to find a collision.
    """

    person: PersonRef
    value: str
    normalized: str
    source: str


@dataclass(frozen=True)
class FactAssertion:
    """One stored fact belonging to an active person, with its validity bounds.

    Only facts whose (person, predicate) group holds more than one distinct value are
    returned; whether any two of them actually contradict is a period question the
    application answers with `ValidityPeriod.overlaps()`.
    """

    person: PersonRef
    fact_id: str
    predicate: str
    value: str
    sensitivity: str
    valid_from: date | None = None
    valid_to: date | None = None


@dataclass(frozen=True)
class DeletedPersonReference:
    """One stored row that still points at a soft-deleted person.

    Only the reference's table and id travel with it. Relationship labels, affiliation roles,
    and interaction summaries are record content the report has no need for, so they are never
    read.
    """

    person: PersonRef
    entity_type: str
    entity_id: str


@runtime_checkable
class CurationReader(Protocol):
    """Find candidate data-quality evidence without interpreting it."""

    def list_shared_handles(self) -> list[NameUsage]: ...

    def list_shared_names(self) -> list[NameUsage]: ...

    def list_conflicting_facts(self) -> list[FactAssertion]: ...

    def list_deleted_person_references(self) -> list[DeletedPersonReference]: ...
