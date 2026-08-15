"""The fictional world: a strict fixture schema and its deterministic materializer.

The fixture is data, not code, so a reviewer can read exactly what an evaluated
agent is able to learn. Materializing it goes through the ordinary audited use
cases, so the evaluated store is the same shape a real one would be.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from evals.harness.errors import EvalHarnessError
from evals.harness.suite import read_json_document
from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.app.context import SetCommunicationPhilosophyInput
from people_context.app.people import AliasInput, RememberPersonInput
from people_context.app.records import RecordFactInput, RecordInteractionInput, SetAffiliationInput
from people_context.app.relationships import SetRelationshipInput
from people_context.config import resolve_db_path
from people_context.domain.person import AliasKind, Person

#: Every fixture write is attributed to this source, so an evaluated store is
#: distinguishable from a real one by inspection alone.
FIXTURE_SOURCE = "evals/fixture"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorldPerson(_StrictModel):
    """One fictional person."""

    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=400)
    handles: tuple[str, ...] = Field(default=(), max_length=8)
    is_self: bool = False


class WorldAffiliation(_StrictModel):
    """One fictional organizational affiliation."""

    person_key: str
    organization: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=200)


class WorldFact(_StrictModel):
    """One fictional time-aware fact."""

    person_key: str
    predicate: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=400)
    valid_from: date | None = None


class WorldInteraction(_StrictModel):
    """One fictional summary-only interaction at a fixed instant."""

    summary: str = Field(min_length=1, max_length=400)
    participant_keys: tuple[str, ...] = Field(min_length=1, max_length=20)
    occurred_at: datetime
    channel: str = Field(min_length=1, max_length=50)

    @field_validator("occurred_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("interaction timestamps must be timezone-aware")
        return value.astimezone(UTC)


class WorldRelationship(_StrictModel):
    """One fictional relationship edge."""

    subject_key: str
    object_key: str
    relationship_type: str = Field(min_length=1, max_length=100)


class World(_StrictModel):
    """A complete fictional world, valid only if every reference resolves."""

    format: str = Field(pattern=r"^people-context\.eval-world$")
    version: int = Field(ge=1, le=1)
    world_id: str = Field(min_length=1, max_length=64)
    as_of: datetime
    communication_philosophy: str = Field(min_length=1, max_length=400)
    people: tuple[WorldPerson, ...] = Field(min_length=1, max_length=100)
    affiliations: tuple[WorldAffiliation, ...] = Field(default=(), max_length=200)
    facts: tuple[WorldFact, ...] = Field(default=(), max_length=400)
    interactions: tuple[WorldInteraction, ...] = Field(default=(), max_length=400)
    relationships: tuple[WorldRelationship, ...] = Field(default=(), max_length=400)

    @field_validator("as_of")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _references_resolve(self) -> World:
        keys = [person.key for person in self.people]
        if len(set(keys)) != len(keys):
            raise ValueError("person keys must be unique")
        selves = [person.key for person in self.people if person.is_self]
        if len(selves) != 1:
            raise ValueError("exactly one person must be marked is_self")
        known = set(keys)
        referenced: list[str] = []
        for affiliation in self.affiliations:
            referenced.append(affiliation.person_key)
        for fact in self.facts:
            referenced.append(fact.person_key)
        for interaction in self.interactions:
            referenced.extend(interaction.participant_keys)
        for relationship in self.relationships:
            referenced.extend((relationship.subject_key, relationship.object_key))
        dangling = sorted(set(referenced) - known)
        if dangling:
            raise ValueError("references to unknown person keys: " + ", ".join(dangling))
        return self

    @property
    def self_key(self) -> str:
        """Return the key of the single person marked as self."""
        return next(person.key for person in self.people if person.is_self)


class FixedClock:
    """A clock frozen at the fixture's ``as_of`` instant.

    Materializing through a frozen clock is what makes two builds of the same
    fixture produce the same recorded timestamps, so a report can be compared
    across machines.
    """

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


def load_world(path: Path) -> World:
    """Load and validate one fictional world fixture."""
    document = read_json_document(path)
    try:
        return World.model_validate(document)
    except ValidationError as exc:
        raise EvalHarnessError(f"invalid evaluation world {path}: {exc}") from exc


def refuse_real_database(db_path: Path) -> Path:
    """Return the absolute evaluation database path, or refuse to use it.

    Two refusals, both deliberate: the harness will not write where the local
    configuration says a real store lives, and it will not open a database that
    already exists. Together they mean an evaluation run cannot read, migrate,
    or overwrite personal data even when it is pointed at the wrong directory.
    """
    resolved = Path(db_path).expanduser().resolve()
    configured = resolve_db_path(None).resolve()
    if resolved == configured:
        raise EvalHarnessError(
            f"refusing to evaluate against the configured people-context database at {resolved}; "
            "the harness only ever builds its own fictional store"
        )
    for companion in (resolved, Path(f"{resolved}-wal"), Path(f"{resolved}-shm")):
        if companion.exists() or companion.is_symlink():
            raise EvalHarnessError(f"refusing to reuse an existing database file at {companion}")
    return resolved


def build_world_database(world: World, db_path: Path) -> Path:
    """Materialize ``world`` into a new SQLite database and return its path."""
    target = refuse_real_database(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    runtime = build_runtime(target, clock=FixedClock(world.as_of))
    try:
        people = _seed_people(runtime, world)
        _seed_records(runtime, world, people)
    finally:
        runtime.close()
    return target


def _seed_people(runtime: ApplicationRuntime, world: World) -> dict[str, Person]:
    """Create every fictional person, self first so identity resolution is stable."""
    ordered = sorted(world.people, key=lambda person: (not person.is_self, person.key))
    people: dict[str, Person] = {}
    for seed in ordered:
        aliases = [AliasInput(value=handle, kind=AliasKind.HANDLE) for handle in seed.handles]
        people[seed.key] = runtime.use_cases.remember_person.execute(
            RememberPersonInput(
                name=seed.name,
                aliases=aliases,
                summary=seed.summary,
                is_self=seed.is_self,
                source=FIXTURE_SOURCE,
            )
        ).person
    return people


def _seed_records(runtime: ApplicationRuntime, world: World, people: dict[str, Person]) -> None:
    """Attach affiliations, facts, interactions, relationships, and the philosophy."""
    use_cases = runtime.use_cases
    use_cases.set_communication_philosophy.execute(
        SetCommunicationPhilosophyInput(text=world.communication_philosophy, source=FIXTURE_SOURCE)
    )
    for affiliation in world.affiliations:
        use_cases.set_affiliation.execute(
            SetAffiliationInput(
                person_id=people[affiliation.person_key].id,
                org=affiliation.organization,
                role=affiliation.role,
                source=FIXTURE_SOURCE,
            )
        )
    for fact in world.facts:
        use_cases.record_fact.execute(
            RecordFactInput(
                person_id=people[fact.person_key].id,
                predicate=fact.predicate,
                value=fact.value,
                valid_from=fact.valid_from,
                source=FIXTURE_SOURCE,
            )
        )
    for interaction in world.interactions:
        use_cases.record_interaction.execute(
            RecordInteractionInput(
                summary=interaction.summary,
                participant_ids=[people[key].id for key in interaction.participant_keys],
                occurred_at=interaction.occurred_at,
                channel=interaction.channel,
                source=FIXTURE_SOURCE,
            )
        )
    for relationship in world.relationships:
        use_cases.set_relationship.execute(
            SetRelationshipInput(
                subject_id=people[relationship.subject_key].id,
                object_id=people[relationship.object_key].id,
                type=relationship.relationship_type,
                source=FIXTURE_SOURCE,
            )
        )
