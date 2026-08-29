"""The trait-evidence relation's schema, and what hard forget does to it (M18.3)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteForgetStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.adapters.sqlite.context_reader import SqliteContextReader
from people_context.adapters.sqlite.trait_evidence import SqliteTraitEvidenceStore
from people_context.app.people import Forget, PreviewForget, RememberPerson, RememberPersonInput
from people_context.app.records import RecordInteraction, RecordObservation, RecordTrait
from people_context.app.records.interactions import RecordInteractionInput
from people_context.app.records.observations import RecordObservationInput
from people_context.app.records.trait_evidence import TraitEvidenceError
from people_context.app.records.traits import RecordTraitInput
from people_context.domain.shared import normalize_name

_MIGRATIONS = "people_context.adapters.sqlite.migrations"

#: The migration that introduced the relation, pinned so a later one cannot make this a test of
#: itself.
_EVIDENCE_MIGRATION = 8

_NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _Store:
    """A store with the trait/observation/interaction use cases wired to real SQLite."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.conn = open_db(path)
        people = SqlitePeopleRepository(self.conn)
        records = SqliteRecordStore(self.conn)
        self.audit = SqliteAuditLog(self.conn)
        self.evidence = SqliteTraitEvidenceStore(self.conn)
        self.people = people
        self.remember = RememberPerson(people, people, self.audit, _Clock())
        self.observe = RecordObservation(people, records, self.audit, _Clock())
        self.interact = RecordInteraction(people, records, self.audit, _Clock())
        self.trait = RecordTrait(people, records, self.audit, _Clock(), self.evidence)
        forget_store = SqliteForgetStore(self.conn)
        self.forget = Forget(people, forget_store, _Clock())
        self.preview = PreviewForget(people, forget_store)

    def person(self, name: str) -> str:
        return self.remember.execute(RememberPersonInput(name=name)).person.id

    def links(self) -> list[tuple[str, str, str]]:
        return [
            (row["trait_id"], row["evidence_type"], row["evidence_id"])
            for row in self.conn.execute(
                "SELECT trait_id, evidence_type, evidence_id FROM trait_evidence"
                " ORDER BY trait_id, evidence_type, evidence_id"
            )
        ]


def _legacy_database(path: Path, *, through: int) -> None:
    """Write the database a release shipping only the first `through` migrations would."""
    conn = sqlite3.connect(path)
    conn.create_function("people_normalize", 1, normalize_name, deterministic=True)
    try:
        for name in sorted(entry.name for entry in resources.files(_MIGRATIONS).iterdir()):
            if not name.endswith(".sql") or int(name.split("_", 1)[0]) > through:
                continue
            conn.executescript(resources.files(_MIGRATIONS).joinpath(name).read_text(encoding="utf-8"))
        conn.execute(f"PRAGMA user_version = {through}")
        conn.commit()
    finally:
        conn.close()


def _tables(conn: Any) -> set[str]:
    return {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


# -- schema ------------------------------------------------------------


def test_a_fresh_database_creates_the_relation_and_its_lookup_index(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "people.db")

    assert "trait_evidence" in _tables(conn)
    indexes = {row["name"] for row in conn.execute("PRAGMA index_list(trait_evidence)")}
    assert "idx_trait_evidence_target" in indexes


def test_a_legacy_database_upgrades_without_losing_its_traits(tmp_path: Path) -> None:
    path = tmp_path / "people.db"
    _legacy_database(path, through=_EVIDENCE_MIGRATION - 1)
    legacy = sqlite3.connect(path)
    legacy.row_factory = sqlite3.Row
    try:
        assert "trait_evidence" not in _tables(legacy)
        legacy.execute(
            """INSERT INTO persons (id, canonical_name, canonical_name_normalized, is_self,
                                    created_at, updated_at)
               VALUES ('p1', 'Alice', 'alice', 0, '2026-07-20T12:00:00+00:00',
                       '2026-07-20T12:00:00+00:00')"""
        )
        legacy.execute(
            """INSERT INTO traits (id, person_id, category, value, confidence, sensitivity,
                                   provenance_source, updated_at)
               VALUES ('t1', 'p1', 'other', 'Direct', 1.0, 'personal', 'user',
                       '2026-07-20T12:00:00+00:00')"""
        )
        legacy.commit()
    finally:
        legacy.close()

    upgraded = open_db(path)

    assert "trait_evidence" in _tables(upgraded)
    # A pre-M18.3 trait keeps its `evidence_note` and gains no invented link.
    assert upgraded.execute("SELECT COUNT(*) FROM traits").fetchone()[0] == 1
    assert upgraded.execute("SELECT COUNT(*) FROM trait_evidence").fetchone()[0] == 0


def test_the_relation_refuses_an_unsupported_evidence_type(tmp_path: Path) -> None:
    """A trait citing a trait would be a belief chain; the schema itself forbids it."""
    store = _Store(tmp_path / "people.db")
    person = store.person("Alice Rivera")
    trait = store.trait.execute(RecordTraitInput(person_id=person, category="other", value="Direct"))

    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            """INSERT INTO trait_evidence (trait_id, evidence_type, evidence_id, created_at)
               VALUES (?, 'trait', ?, ?)""",
            (trait.id, trait.id, _NOW.isoformat()),
        )


def test_citing_the_same_record_twice_asserts_one_link(tmp_path: Path) -> None:
    store = _Store(tmp_path / "people.db")
    person = store.person("Alice Rivera")
    observation = store.observe.execute(RecordObservationInput(person_id=person, text="Asked for metrics"))
    trait = store.trait.execute(
        RecordTraitInput(person_id=person, category="other", value="Direct", evidence_ids=[observation.id])
    )

    store.evidence.link_trait_evidence(store.evidence.list_links(trait.id))

    assert store.links() == [(trait.id, "observation", observation.id)]


# -- the write boundary ------------------------------------------------


def test_a_trait_cannot_cite_a_record_about_another_person(tmp_path: Path) -> None:
    store = _Store(tmp_path / "people.db")
    alice = store.person("Alice Rivera")
    bob = store.person("Bob Chen")
    observation = store.observe.execute(RecordObservationInput(person_id=bob, text="Bob's remark"))

    with pytest.raises(TraitEvidenceError) as excinfo:
        store.trait.execute(
            RecordTraitInput(person_id=alice, category="other", value="Direct", evidence_ids=[observation.id])
        )

    assert excinfo.value.code == "evidence_subject_mismatch"
    # The refusal happens before the trait is written, so nothing ungrounded survives.
    assert store.conn.execute("SELECT COUNT(*) FROM traits").fetchone()[0] == 0


def test_a_trait_may_cite_an_interaction_its_subject_joined(tmp_path: Path) -> None:
    store = _Store(tmp_path / "people.db")
    alice = store.person("Alice Rivera")
    bob = store.person("Bob Chen")
    interaction = store.interact.execute(
        RecordInteractionInput(summary="Planning meeting", participant_ids=[alice, bob], occurred_at=_NOW)
    )

    trait = store.trait.execute(
        RecordTraitInput(person_id=alice, category="other", value="Direct", evidence_ids=[interaction.id])
    )

    assert store.links() == [(trait.id, "interaction", interaction.id)]


def test_evidence_note_remains_the_additive_human_half(tmp_path: Path) -> None:
    store = _Store(tmp_path / "people.db")
    person = store.person("Alice Rivera")
    observation = store.observe.execute(RecordObservationInput(person_id=person, text="Asked for metrics"))

    trait = store.trait.execute(
        RecordTraitInput(
            person_id=person,
            category="other",
            value="Direct",
            evidence_note="Twice in one week.",
            evidence_ids=[observation.id],
        )
    )

    assert trait.evidence_note == "Twice in one week."
    assert store.links() == [(trait.id, "observation", observation.id)]


def test_the_context_reader_reports_each_link_with_the_cited_records_own_level(
    tmp_path: Path,
) -> None:
    """The disclosure decision belongs to the evidence, so the reader carries its level."""
    store = _Store(tmp_path / "people.db")
    alice = store.person("Alice Rivera")
    open_observation = store.observe.execute(
        RecordObservationInput(person_id=alice, text="Asked for metrics")
    )
    private = store.observe.execute(
        RecordObservationInput(person_id=alice, text="Mentioned the reorganisation", sensitivity="restricted")
    )
    trait = store.trait.execute(
        RecordTraitInput(
            person_id=alice,
            category="other",
            value="Direct",
            evidence_ids=[open_observation.id, private.id],
        )
    )

    records = SqliteContextReader(store.conn).list_trait_evidence(alice)

    assert [(record.trait_id, record.evidence_id, record.sensitivity.value) for record in records] == sorted(
        [
            (trait.id, open_observation.id, "personal"),
            (trait.id, private.id, "restricted"),
        ],
        key=lambda row: row[1],
    )


# -- hard forget -------------------------------------------------------


def _grounded(store: _Store) -> tuple[str, str, str, str]:
    """Return `(alice, bob, trait_id, interaction_id)` for a shared-interaction fixture."""
    alice = store.person("Alice Rivera")
    bob = store.person("Bob Chen")
    interaction = store.interact.execute(
        RecordInteractionInput(summary="Planning meeting", participant_ids=[alice, bob], occurred_at=_NOW)
    )
    trait = store.trait.execute(
        RecordTraitInput(person_id=alice, category="other", value="Direct", evidence_ids=[interaction.id])
    )
    return alice, bob, trait.id, interaction.id


def test_forgetting_a_trait_removes_its_links_and_counts_them(tmp_path: Path) -> None:
    store = _Store(tmp_path / "people.db")
    _alice, _bob, trait_id, _interaction = _grounded(store)

    result = store.forget.execute(f"trait:{trait_id}", "record")

    assert result.deleted["trait_evidence"] == 1
    assert store.links() == []


def test_forgetting_the_cited_interaction_removes_the_link_the_trait_asserted(tmp_path: Path) -> None:
    store = _Store(tmp_path / "people.db")
    _alice, _bob, trait_id, interaction = _grounded(store)

    result = store.forget.execute(f"interaction:{interaction}", "record")

    assert result.deleted["trait_evidence"] == 1
    assert store.links() == []
    # The trait itself is untouched: erasing evidence does not rewrite an inference.
    assert store.conn.execute("SELECT COUNT(*) FROM traits").fetchone()[0] == 1


def test_a_link_to_a_shared_interaction_survives_forgetting_the_other_participant(
    tmp_path: Path,
) -> None:
    store = _Store(tmp_path / "people.db")
    _alice, bob, trait_id, interaction = _grounded(store)

    store.forget.execute(bob, "person")

    # The interaction still has Alice, so it is durable and her trait still cites it.
    assert store.links() == [(trait_id, "interaction", interaction)]


def test_forgetting_the_subject_removes_the_trait_and_its_links(tmp_path: Path) -> None:
    store = _Store(tmp_path / "people.db")
    alice, _bob, _trait_id, _interaction = _grounded(store)

    preview = store.preview.execute(alice)
    result = store.forget.execute(alice, "person")

    assert preview.deleted["trait_evidence"] == 1
    assert result.deleted["trait_evidence"] == 1
    assert store.links() == []


def test_a_removed_link_is_named_in_the_replay_manifest_and_its_history_redacted(
    tmp_path: Path,
) -> None:
    store = _Store(tmp_path / "people.db")
    _alice, _bob, trait_id, interaction = _grounded(store)
    link_entity = f"{trait_id}:{interaction}"

    store.forget.execute(f"trait:{trait_id}", "record")

    tombstone = store.conn.execute(
        "SELECT payload_json FROM changelog WHERE op_kind = 'forget'"
    ).fetchone()["payload_json"]
    assert link_entity in tombstone
    # The link's own accountability and replay history no longer describes what it cited.
    payloads = [
        row["payload_json"]
        for row in store.conn.execute("SELECT payload_json FROM audit_log WHERE entity_type = 'trait_evidence'")
    ]
    assert payloads and all(payload == '{"redacted": true}' for payload in payloads)
    covered = store.conn.execute(
        "SELECT payload_json FROM changelog WHERE entity_type = 'trait_evidence' AND op_kind <> 'forget'"
    ).fetchall()
    assert covered and all(row["payload_json"] == '{"redacted": true}' for row in covered)


def test_a_database_with_no_links_reports_no_evidence_count(tmp_path: Path) -> None:
    """An ordinary forget keeps exactly the deletion summary it always had."""
    store = _Store(tmp_path / "people.db")
    person = store.person("Alice Rivera")

    assert "trait_evidence" not in store.preview.execute(person).deleted
    assert "trait_evidence" not in store.forget.execute(person, "person").deleted
