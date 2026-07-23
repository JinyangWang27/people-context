"""Integration tests for atomic SQLite lifecycle operations."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteForgetStore,
    SqliteMergeStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.app.people import Forget, MergePeople, MergePeopleError
from people_context.domain.fact import Fact
from people_context.domain.interaction import Interaction
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.relationship import Relationship
from people_context.domain.shared import Provenance
from people_context.ports.audit_log import AuditEntry

_NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
_PROVENANCE = Provenance(source="test")


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _person(name: str, *, is_self: bool = False, summary: str | None = None) -> Person:
    return Person(canonical_name=name, is_self=is_self, summary=summary, created_at=_NOW, updated_at=_NOW)


def test_merge_people_reparents_rows_resolves_collisions_and_refreshes_search() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    primary = _person("Alice", summary="")
    primary.aliases.append(Alias(value="Ally", kind=AliasKind.NICKNAME))
    duplicate = _person("Alice Smith", summary="Engineer")
    duplicate.aliases.extend([Alias(value="ally"), Alias(value="A. Smith")])
    other = _person("Bob")
    for person in (primary, duplicate, other):
        people.save_person(person)

    fact = Fact(person_id=duplicate.id, predicate="location", value="Dubai", provenance=_PROVENANCE)
    records.save_fact(fact)
    surviving = Relationship(
        subject_id=duplicate.id,
        object_id=other.id,
        type="friend_of",
        provenance=_PROVENANCE,
        created_at=_NOW,
    )
    self_loop = Relationship(
        subject_id=duplicate.id,
        object_id=primary.id,
        type="same_team",
        provenance=_PROVENANCE,
        created_at=_NOW,
    )
    records.save_relationship(surviving)
    records.save_relationship(self_loop)
    interaction = Interaction(
        summary="Met",
        occurred_at=_NOW,
        participant_ids=[primary.id, duplicate.id],
        provenance=_PROVENANCE,
    )
    records.save_interaction(interaction)

    result = MergePeople(people, SqliteMergeStore(conn), _Clock()).execute(primary.id, duplicate.id)

    assert result.person.summary == "Engineer"
    assert [alias.value for alias in result.person.aliases] == ["Ally", "Alice Smith", "A. Smith"]
    assert result.person.aliases[1].kind == AliasKind.FORMER_NAME
    assert result.moved.facts == 1
    assert result.moved.relationships == 1
    assert result.moved.interaction_participations == 1
    assert result.self_loops_removed == 1
    assert records.get_record("fact", fact.id).person_id == primary.id  # type: ignore[union-attr]
    assert records.get_record("relationship", surviving.id).subject_id == primary.id  # type: ignore[union-attr]
    assert records.get_record("relationship", self_loop.id) is None
    assert records.get_record("interaction", interaction.id).participant_ids == [primary.id]  # type: ignore[union-attr]
    assert people.get(duplicate.id).deleted_at == _NOW  # type: ignore[union-attr]
    assert people.search_names("Alice Smith")[0].person.id == primary.id
    audit_rows = conn.execute("SELECT op, entity_id, payload_json FROM audit_log").fetchall()
    assert [(row["op"], row["entity_id"]) for row in audit_rows] == [("merge", primary.id)]
    assert json.loads(audit_rows[0]["payload_json"])["aliases_added"] == ["Alice Smith", "A. Smith"]


def test_merge_people_rolls_back_every_change_when_audit_checkpoint_fails() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    primary = _person("Primary")
    duplicate = _person("Duplicate")
    people.save_person(primary)
    people.save_person(duplicate)

    def fail(_: str) -> None:
        raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        MergePeople(people, SqliteMergeStore(conn, fail), _Clock()).execute(primary.id, duplicate.id)

    assert people.get(duplicate.id).deleted_at is None  # type: ignore[union-attr]
    assert people.get(primary.id).aliases == []  # type: ignore[union-attr]
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_merge_people_validates_same_person_and_self_direction() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    self_person = _person("Me", is_self=True)
    other = _person("Other")
    people.save_person(self_person)
    people.save_person(other)
    merge = MergePeople(people, SqliteMergeStore(conn), _Clock())

    with pytest.raises(MergePeopleError, match="different") as same:
        merge.execute(other.id, other.id)
    assert same.value.code == "same_person"
    with pytest.raises(MergePeopleError, match="primary") as direction:
        merge.execute(other.id, self_person.id)
    assert direction.value.code == "self_merge_direction"


def test_forget_person_deletes_graph_orphan_interactions_and_redacts_audits() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    audit = SqliteAuditLog(conn)
    forgotten = _person("Me", is_self=True)
    forgotten.aliases.append(Alias(value="my-handle", kind=AliasKind.HANDLE))
    other = _person("Other")
    people.save_person(forgotten)
    people.save_person(other)
    records.save_fact(Fact(person_id=forgotten.id, predicate="secret", value="x", provenance=_PROVENANCE))
    relationship = Relationship(
        subject_id=other.id,
        object_id=forgotten.id,
        type="knows",
        provenance=_PROVENANCE,
        created_at=_NOW,
    )
    records.save_relationship(relationship)
    orphaned = Interaction(
        summary="Only me", occurred_at=_NOW, participant_ids=[forgotten.id], provenance=_PROVENANCE
    )
    shared = Interaction(
        summary="Both", occurred_at=_NOW, participant_ids=[forgotten.id, other.id], provenance=_PROVENANCE
    )
    records.save_interaction(orphaned)
    records.save_interaction(shared)
    audit.append(
        AuditEntry(
            ts=_NOW,
            op="create",
            entity_type="interaction",
            entity_id=shared.id,
            payload={"nested": [{"person": forgotten.id}]},
            source="test",
        )
    )

    result = Forget(people, SqliteForgetStore(conn), _Clock()).execute(forgotten.id, "person")

    assert result.deleted["persons"] == 1
    assert result.deleted["aliases"] == 1
    assert result.deleted["facts"] == 1
    assert result.deleted["relationships"] == 1
    assert result.deleted["interaction_participations"] == 2
    assert result.deleted["interactions"] == 1
    assert people.get(forgotten.id) is None
    assert records.get_record("interaction", orphaned.id) is None
    assert records.get_record("interaction", shared.id).participant_ids == [other.id]  # type: ignore[union-attr]
    rows = conn.execute("SELECT op, payload_json FROM audit_log ORDER BY rowid").fetchall()
    assert rows[0]["payload_json"] == '{"redacted": true}'
    assert rows[1]["op"] == "forget"
    assert forgotten.id not in rows[1]["payload_json"]


def test_forget_record_removes_only_target_and_interaction_participations() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    person = _person("Alice")
    people.save_person(person)
    interaction = Interaction(
        summary="Meeting", occurred_at=_NOW, participant_ids=[person.id], provenance=_PROVENANCE
    )
    records.save_interaction(interaction)

    result = Forget(people, SqliteForgetStore(conn), _Clock()).execute(
        f"interaction:{interaction.id}", "record"
    )

    assert result.deleted == {"interactions": 1, "interaction_participations": 1}
    assert people.get(person.id) is not None
    assert records.get_record("interaction", interaction.id) is None


def test_forget_rolls_back_deletion_and_redaction_on_failure() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    person = _person("Alice")
    people.save_person(person)
    SqliteAuditLog(conn).append(
        AuditEntry(
            ts=_NOW,
            op="create",
            entity_type="person",
            entity_id=person.id,
            payload={"name": "Alice"},
            source="test",
        )
    )

    def fail(_: str) -> None:
        raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        Forget(people, SqliteForgetStore(conn, fail), _Clock()).execute(person.id, "person")

    assert people.get(person.id) is not None
    assert conn.execute("SELECT payload_json FROM audit_log").fetchone()[0] == '{"name": "Alice"}'


def test_forget_accepts_soft_deleted_person_and_removes_fts_rows() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    person = _person("Alice")
    people.save_person(person)
    person.deleted_at = _NOW
    people.save_person(person)
    # Simulate a stale out-of-band FTS row to prove hard deletion cleans derived state.
    with conn:
        conn.execute("INSERT INTO person_search (name, person_id) VALUES ('stale', ?)", (person.id,))

    result = Forget(people, SqliteForgetStore(conn), _Clock()).execute(person.id, "person")

    assert result.deleted["persons"] == 1
    assert conn.execute("SELECT COUNT(*) FROM person_search WHERE person_id = ?", (person.id,)).fetchone()[0] == 0
