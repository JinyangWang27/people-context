"""Real-SQLite bootstrap restore: baseline refusal, verbatim writes, and rollback."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from people_context.adapters.runtime import build_runtime
from people_context.adapters.sqlite import (
    SqliteBootstrapRestorer,
    SqliteHybridLogicalClock,
    SqlitePeopleRepository,
    open_db,
)
from people_context.app.context import SetCommunicationPhilosophyInput
from people_context.app.people.remember import AliasInput, RememberPersonInput
from people_context.app.records import (
    RecordFactInput,
    RecordInteractionInput,
    RecordObservationInput,
    RecordTraitInput,
    SetAffiliationInput,
    SetReminderInput,
)
from people_context.app.relationships import AddRelationshipTypeInput, SetRelationshipInput
from people_context.domain.person import AliasKind, Person
from people_context.domain.reminder import ReminderKind
from people_context.domain.shared import normalize_name
from people_context.domain.sync_bundle import (
    InvalidBundleError,
    RestoreUnavailableError,
    SyncBundleDocument,
    TargetNotEmptyError,
)
from people_context.domain.trait import TraitCategory

_NOW = datetime(2026, 6, 7, 8, 9, tzinfo=UTC)
_DELETED_PERSON_ID = "01J0000000000000000GHOST01"

_MUTABLE_TABLES = (
    "persons",
    "aliases",
    "organizations",
    "affiliations",
    "relationships",
    "facts",
    "observations",
    "traits",
    "interactions",
    "interaction_participants",
    "reminders",
    "user_preferences",
    "import_staging",
    "audit_log",
    "changelog",
    "sync_conflicts",
    "person_search",
)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _source_bundle(db_path: Path) -> SyncBundleDocument:
    """Seed one realistic origin device and return its exported bundle."""
    runtime = build_runtime(db_path, clock=_Clock())
    use_cases = runtime.use_cases
    me = use_cases.remember_person.execute(RememberPersonInput(name="Me", is_self=True)).person
    alice = use_cases.remember_person.execute(
        RememberPersonInput(
            name="Alice Zhang",
            aliases=[AliasInput(value="Ali", kind=AliasKind.NICKNAME)],
            summary="Long-time colleague",
        )
    ).person
    bob = use_cases.remember_person.execute(RememberPersonInput(name="Bob Meyer")).person
    use_cases.add_relationship_type.execute(
        AddRelationshipTypeInput(
            type="co_founder_of",
            symmetric=True,
            category="professional",
            synonyms=["cofounder"],
        )
    )
    use_cases.set_relationship.execute(
        SetRelationshipInput(subject_id=me.id, object_id=alice.id, type="co_founder_of")
    )
    use_cases.set_affiliation.execute(
        SetAffiliationInput(person_id=alice.id, org="Acme", role="Engineer", valid_from=date(2024, 1, 1))
    )
    use_cases.record_fact.execute(RecordFactInput(person_id=alice.id, predicate="city", value="Berlin"))
    use_cases.record_observation.execute(RecordObservationInput(person_id=alice.id, text="Prefers async updates"))
    use_cases.record_trait.execute(
        RecordTraitInput(person_id=alice.id, category=TraitCategory.COMMUNICATION_STYLE, value="direct")
    )
    use_cases.record_interaction.execute(
        RecordInteractionInput(summary="Coffee catch-up", participant_ids=[alice.id, bob.id])
    )
    use_cases.set_reminder.execute(
        SetReminderInput(
            person_id=alice.id,
            text="Follow up on the proposal",
            kind=ReminderKind.FOLLOW_UP,
            due_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
    )
    use_cases.set_communication_philosophy.execute(SetCommunicationPhilosophyInput(text="Be direct."))
    # A soft-deleted person must round-trip while staying out of the rebuilt search index.
    runtime.repo.save_person(
        Person(
            id=_DELETED_PERSON_ID,
            canonical_name="Ghost",
            created_at=_NOW,
            updated_at=_NOW,
            deleted_at=_NOW,
        )
    )
    document = use_cases.export_sync_bundle.execute()
    runtime.close()
    return document


def _restorer(
    conn: sqlite3.Connection,
    phase_hook: Callable[[str], None] | None = None,
) -> SqliteBootstrapRestorer:
    return SqliteBootstrapRestorer(
        conn,
        SqlitePeopleRepository(conn),
        SqliteHybridLogicalClock(conn),
        phase_hook=phase_hook,
    )


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608 - fixed constants


def _local_device_id(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT id FROM devices WHERE retired_at IS NULL").fetchone()["id"])


def _assert_untouched(conn: sqlite3.Connection, local_device_id: str) -> None:
    """Assert the destination is still the freshly initialized database it was."""
    assert [table for table in _MUTABLE_TABLES if _count(conn, table)] == []
    assert [row["id"] for row in conn.execute("SELECT id FROM devices").fetchall()] == [local_device_id]
    assert _count(conn, "relationship_types") == 14
    assert _count(conn, "relationship_type_synonyms") == 20


@pytest.fixture
def bundle(tmp_path: Path) -> SyncBundleDocument:
    return _source_bundle(tmp_path / "source.db")


# -- successful restore --------------------------------------------------


def test_restore_writes_every_collection_verbatim(tmp_path: Path, bundle: SyncBundleDocument) -> None:
    conn = open_db(tmp_path / "target.db")

    outcome = _restorer(conn).restore(bundle)

    snapshot = bundle.snapshot
    assert _count(conn, "persons") == len(snapshot.people)
    assert _count(conn, "organizations") == len(snapshot.organizations)
    assert _count(conn, "affiliations") == len(snapshot.affiliations)
    assert _count(conn, "relationships") == len(snapshot.relationships)
    assert _count(conn, "facts") == len(snapshot.facts)
    assert _count(conn, "observations") == len(snapshot.observations)
    assert _count(conn, "traits") == len(snapshot.traits)
    assert _count(conn, "interactions") == len(snapshot.interactions)
    assert _count(conn, "reminders") == len(snapshot.reminders)
    assert _count(conn, "user_preferences") == len(snapshot.user_preferences)
    assert outcome.people == len(snapshot.people)

    person = conn.execute(
        "SELECT * FROM persons WHERE id = ?", (snapshot.people[0].id,)
    ).fetchone()
    assert person["canonical_name"] == snapshot.people[0].canonical_name
    assert person["created_at"] == snapshot.people[0].created_at.isoformat()
    assert _count(conn, "aliases") == sum(len(row.aliases) for row in snapshot.people)
    assert _count(conn, "interaction_participants") == sum(
        len(row.participant_ids) for row in snapshot.interactions
    )
    conn.close()


def test_a_restored_database_re_exports_the_same_snapshot_and_history(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    """The strongest verbatim check: every portable row and every field survives unchanged."""
    target = tmp_path / "target.db"
    conn = open_db(target)
    _restorer(conn).restore(bundle)
    conn.close()

    runtime = build_runtime(target, clock=_Clock())
    reexported = runtime.use_cases.export_sync_bundle.execute()
    runtime.close()

    assert reexported.snapshot.model_dump(mode="json") == bundle.snapshot.model_dump(mode="json")
    assert reexported.relationship_vocabulary.model_dump(mode="json") == (
        bundle.relationship_vocabulary.model_dump(mode="json")
    )
    assert [entry.model_dump(mode="json") for entry in reexported.changelog] == [
        entry.model_dump(mode="json") for entry in bundle.changelog
    ]
    # The new device is the origin of its own bundle and carries the imported history forward.
    assert reexported.origin_device_id != bundle.origin_device_id
    assert {device.id for device in reexported.devices} >= {device.id for device in bundle.devices}


def test_restore_populates_every_derived_normalized_column(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    """Normalized columns are how lookup finds a row; a NULL one silently duplicates it."""
    conn = open_db(tmp_path / "target.db")

    _restorer(conn).restore(bundle)

    for table, source, derived in (
        ("persons", "canonical_name", "canonical_name_normalized"),
        ("aliases", "value", "value_normalized"),
        ("organizations", "name", "name_normalized"),
    ):
        rows = conn.execute(f"SELECT {source} AS source, {derived} AS derived FROM {table}").fetchall()  # noqa: S608
        assert rows, f"{table} should have been restored"
        assert [row["derived"] for row in rows] == [normalize_name(row["source"]) for row in rows]
    conn.close()


def test_a_restored_organization_is_reused_rather_than_duplicated(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    """The user-visible consequence of an unpopulated `name_normalized`."""
    target = tmp_path / "target.db"
    conn = open_db(target)
    _restorer(conn).restore(bundle)
    conn.close()

    restored = bundle.snapshot.organizations[0]
    runtime = build_runtime(target, clock=_Clock())
    person = runtime.use_cases.remember_person.execute(RememberPersonInput(name="Carol Lin")).person
    runtime.use_cases.set_affiliation.execute(
        SetAffiliationInput(person_id=person.id, org=restored.name, role="Designer")
    )
    organizations = runtime.conn.execute("SELECT id, name FROM organizations").fetchall()
    runtime.close()

    assert [row["id"] for row in organizations] == [restored.id]


def test_restore_reinstates_audit_and_changelog_without_minting_new_rows(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    """Restore is the sole ``audit_mutation`` exception: it copies history, never adds to it."""
    conn = open_db(tmp_path / "target.db")

    _restorer(conn).restore(bundle)

    audit_ids = [row["id"] for row in conn.execute("SELECT id FROM audit_log ORDER BY id").fetchall()]
    op_ids = [row["op_id"] for row in conn.execute("SELECT op_id FROM changelog ORDER BY op_id").fetchall()]
    assert audit_ids == sorted(entry.id for entry in bundle.snapshot.audit_log)
    assert op_ids == sorted(entry.op_id for entry in bundle.changelog)
    original = {entry.op_id: entry for entry in bundle.changelog}
    for row in conn.execute("SELECT op_id, device_id, transaction_id, inserted_at FROM changelog").fetchall():
        source = original[row["op_id"]]
        assert row["device_id"] == source.device_id
        assert row["transaction_id"] == source.transaction_id
        assert row["inserted_at"] == source.inserted_at.isoformat()
    conn.close()


def test_restore_retires_every_imported_device_and_keeps_the_local_identity(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    conn = open_db(tmp_path / "target.db")
    local = _local_device_id(conn)

    outcome = _restorer(conn).restore(bundle)

    rows = conn.execute("SELECT id, retired_at FROM devices").fetchall()
    active = [row["id"] for row in rows if row["retired_at"] is None]
    assert active == [local]
    assert outcome.devices == len(bundle.devices)
    imported = {row["id"]: row["retired_at"] for row in rows if row["id"] != local}
    assert set(imported) == {device.id for device in bundle.devices}
    assert all(retired_at is not None for retired_at in imported.values())
    conn.close()


def test_restore_stamps_an_unretired_origin_with_the_bundle_creation_time(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    conn = open_db(tmp_path / "target.db")

    _restorer(conn).restore(bundle)

    retired_at = conn.execute(
        "SELECT retired_at FROM devices WHERE id = ?", (bundle.origin_device_id,)
    ).fetchone()["retired_at"]
    assert retired_at == bundle.created_at.isoformat()
    conn.close()


def test_restore_advances_the_local_clock_past_all_imported_history(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    conn = open_db(tmp_path / "target.db")

    outcome = _restorer(conn).restore(bundle)

    local = (outcome.local_watermark.physical_ms, outcome.local_watermark.logical_counter)
    assert local > (bundle.watermark.hlc_physical_ms, bundle.watermark.hlc_logical)
    assert all(local > (entry.hlc_physical_ms, entry.hlc_logical) for entry in bundle.changelog)
    persisted = conn.execute(
        "SELECT hlc_physical_ms, hlc_logical FROM devices WHERE retired_at IS NULL"
    ).fetchone()
    assert (persisted["hlc_physical_ms"], persisted["hlc_logical"]) == local
    conn.close()


def test_restore_rebuilds_search_rows_for_active_people_only(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    conn = open_db(tmp_path / "target.db")

    outcome = _restorer(conn).restore(bundle)

    active_names = sum(len(row.aliases) + 1 for row in bundle.snapshot.people if row.deleted_at is None)
    assert outcome.indexed_names == active_names
    assert _count(conn, "person_search") == active_names
    indexed = {row["person_id"] for row in conn.execute("SELECT person_id FROM person_search").fetchall()}
    assert _DELETED_PERSON_ID not in indexed
    assert [hit.person.canonical_name for hit in SqlitePeopleRepository(conn).search_names("Ali")] == [
        "Alice Zhang"
    ]
    conn.close()


def test_restore_skips_seeded_vocabulary_and_inserts_custom_rows(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    conn = open_db(tmp_path / "target.db")

    outcome = _restorer(conn).restore(bundle)

    assert outcome.relationship_types == 1
    assert outcome.relationship_synonyms == 1
    assert _count(conn, "relationship_types") == len(bundle.relationship_vocabulary.types)
    assert _count(conn, "relationship_type_synonyms") == len(bundle.relationship_vocabulary.synonyms)
    custom = conn.execute("SELECT * FROM relationship_types WHERE type = 'co_founder_of'").fetchone()
    assert (bool(custom["symmetric"]), custom["category"]) == (True, "professional")
    conn.close()


# -- refusals ------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "sql", "params"),
    [
        ("organizations", "INSERT INTO organizations (id, name, kind) VALUES ('o1', 'Acme', NULL)", ()),
        (
            "user_preferences",
            "INSERT INTO user_preferences (key, value_json, updated_at)"
            " VALUES ('k', '\"v\"', '2026-01-01T00:00:00+00:00')",
            (),
        ),
        (
            "import_staging",
            "INSERT INTO import_staging (id, batch_id, source, candidate_json, status, created_at)"
            " VALUES ('s1', 'b1', 'email', '{}', 'pending', '2026-01-01T00:00:00+00:00')",
            (),
        ),
        (
            "audit_log",
            "INSERT INTO audit_log (id, ts, op, entity_type, entity_id, payload_json, source)"
            " VALUES ('a1', '2026-01-01T00:00:00+00:00', 'create', 'person', 'p1', '{}', 'cli')",
            (),
        ),
        (
            "sync_conflicts",
            "INSERT INTO sync_conflicts (id, entity_type, entity_id, conflict_kind, candidate_ops_json, created_at)"
            " VALUES ('c1', 'person', 'p1', 'concurrent_update', '[]', '2026-01-01T00:00:00+00:00')",
            (),
        ),
        ("person_search", "INSERT INTO person_search (name, person_id) VALUES ('Ghost', 'p1')", ()),
    ],
)
def test_any_occupied_mutable_table_refuses_the_restore(
    tmp_path: Path,
    bundle: SyncBundleDocument,
    table: str,
    sql: str,
    params: tuple[object, ...],
) -> None:
    conn = open_db(tmp_path / "target.db")
    conn.execute(sql, params)
    conn.commit()
    before = _count(conn, table)

    with pytest.raises(TargetNotEmptyError) as error:
        _restorer(conn).restore(bundle)

    assert error.value.code == "target_not_empty"
    assert any(detail.startswith(f"{table}:") for detail in error.value.details)
    assert _count(conn, table) == before
    assert _count(conn, "persons") == 0
    assert _count(conn, "changelog") == 0
    conn.close()


def test_an_occupied_person_graph_refuses_the_restore(tmp_path: Path, bundle: SyncBundleDocument) -> None:
    conn = open_db(tmp_path / "target.db")
    SqlitePeopleRepository(conn).save_person(Person(canonical_name="Existing", created_at=_NOW, updated_at=_NOW))

    with pytest.raises(TargetNotEmptyError) as error:
        _restorer(conn).restore(bundle)

    assert any(detail.startswith("persons:") for detail in error.value.details)
    assert _count(conn, "persons") == 1
    assert _count(conn, "changelog") == 0
    conn.close()


@pytest.mark.parametrize("retired", [False, True])
def test_an_additional_device_refuses_the_restore(
    tmp_path: Path,
    bundle: SyncBundleDocument,
    retired: bool,
) -> None:
    conn = open_db(tmp_path / "target.db")
    local = _local_device_id(conn)
    conn.execute(
        """INSERT INTO devices (id, display_name, public_key, created_at, retired_at, hlc_physical_ms, hlc_logical)
           VALUES ('extra', 'other', NULL, '2026-01-01T00:00:00+00:00', ?, 0, 0)""",
        ("2026-02-01T00:00:00+00:00" if retired else None,),
    )
    conn.commit()

    with pytest.raises(TargetNotEmptyError) as error:
        _restorer(conn).restore(bundle)

    assert any(detail.startswith("devices:") for detail in error.value.details)
    assert {row["id"] for row in conn.execute("SELECT id FROM devices").fetchall()} == {local, "extra"}
    conn.close()


def test_drifted_relationship_vocabulary_refuses_the_restore(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    conn = open_db(tmp_path / "target.db")
    conn.execute(
        "INSERT INTO relationship_types (type, inverse, symmetric, category, canonical)"
        " VALUES ('rival_of', NULL, 1, 'social', 1)"
    )
    conn.commit()
    local = _local_device_id(conn)

    with pytest.raises(TargetNotEmptyError) as error:
        _restorer(conn).restore(bundle)

    assert any(detail.startswith("relationship_types:") for detail in error.value.details)
    assert not any("rival_of" in detail for detail in error.value.details)
    assert _count(conn, "persons") == 0
    assert [row["id"] for row in conn.execute("SELECT id FROM devices").fetchall()] == [local]
    conn.close()


def test_a_drifted_seeded_synonym_refuses_the_restore(tmp_path: Path, bundle: SyncBundleDocument) -> None:
    conn = open_db(tmp_path / "target.db")
    conn.execute("DELETE FROM relationship_type_synonyms WHERE synonym = 'friend'")
    conn.commit()

    with pytest.raises(TargetNotEmptyError) as error:
        _restorer(conn).restore(bundle)

    assert any(detail.startswith("relationship_type_synonyms:") for detail in error.value.details)
    conn.close()


def test_occupied_optional_vector_storage_refuses_the_restore(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    conn = open_db(tmp_path / "target.db")
    conn.execute("CREATE TABLE semantic_vectors (entity_id TEXT PRIMARY KEY, kind TEXT, embedding BLOB)")
    conn.execute("INSERT INTO semantic_vectors (entity_id, kind, embedding) VALUES ('p1', 'person', x'00')")
    conn.commit()

    with pytest.raises(TargetNotEmptyError) as error:
        _restorer(conn).restore(bundle)

    assert any(detail.startswith("semantic_vectors: 1 row(s)") for detail in error.value.details)
    assert _count(conn, "semantic_vectors") == 1
    conn.close()


def test_unreadable_vector_storage_refuses_rather_than_assuming_it_is_empty(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    db_path = tmp_path / "target.db"
    conn = open_db(db_path)
    conn.execute("CREATE VIRTUAL TABLE semantic_vectors USING fts5(entity_id)")
    conn.commit()
    conn.close()
    # Reopening without the module registered mirrors a connection that cannot load sqlite-vec.
    plain = sqlite3.connect(db_path)
    plain.row_factory = sqlite3.Row
    plain.enable_load_extension(False)
    plain.execute("PRAGMA writable_schema=ON")
    plain.execute("UPDATE sqlite_master SET sql = replace(sql, 'fts5', 'vec0') WHERE name = 'semantic_vectors'")
    plain.execute("PRAGMA writable_schema=OFF")
    plain.commit()
    plain.close()

    conn = open_db(db_path)
    with pytest.raises(TargetNotEmptyError) as error:
        _restorer(conn).restore(bundle)

    assert any("present but unreadable" in detail for detail in error.value.details)
    conn.close()


def test_a_bundled_device_matching_the_local_identity_is_rejected(tmp_path: Path) -> None:
    """Never retire or overwrite the destination's own device row."""
    conn = open_db(tmp_path / "target.db")
    local = _local_device_id(conn)
    source_path = tmp_path / "source.db"
    source = open_db(source_path)
    source.execute("UPDATE devices SET id = ? WHERE retired_at IS NULL", (local,))
    source.commit()
    source.close()
    bundle = _source_bundle(source_path)

    with pytest.raises(InvalidBundleError) as error:
        _restorer(conn).restore(bundle)

    assert any("own active device id" in detail for detail in error.value.details)
    _assert_untouched(conn, local)
    conn.close()


def test_a_conflicting_seeded_vocabulary_row_rejects_the_whole_bundle(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    conn = open_db(tmp_path / "target.db")
    local = _local_device_id(conn)
    conflicting = next(row for row in bundle.relationship_vocabulary.types if row.type == "friend_of")
    conflicting.category = "professional"

    with pytest.raises(InvalidBundleError) as error:
        _restorer(conn).restore(bundle)

    assert any("conflicts with the destination vocabulary: friend_of" in detail for detail in error.value.details)
    _assert_untouched(conn, local)
    conn.close()


def test_a_conflicting_synonym_rejects_the_whole_bundle(tmp_path: Path, bundle: SyncBundleDocument) -> None:
    conn = open_db(tmp_path / "target.db")
    local = _local_device_id(conn)
    conflicting = next(row for row in bundle.relationship_vocabulary.synonyms if row.synonym == "friend")
    conflicting.type = "colleague_of"

    with pytest.raises(InvalidBundleError) as error:
        _restorer(conn).restore(bundle)

    assert any("conflicts with the destination vocabulary: friend" in detail for detail in error.value.details)
    _assert_untouched(conn, local)
    conn.close()


# -- rollback and concurrency --------------------------------------------


@pytest.mark.parametrize(
    "phase",
    ["reservation", "baseline", "vocabulary", "devices", "domain", "changelog", "fts", "hlc"],
)
def test_a_failure_at_any_phase_rolls_the_destination_back(
    tmp_path: Path,
    bundle: SyncBundleDocument,
    phase: str,
) -> None:
    conn = open_db(tmp_path / "target.db")
    local = _local_device_id(conn)

    def _fail(current: str) -> None:
        if current == phase:
            raise RuntimeError(f"forced failure at {phase}")

    with pytest.raises(RuntimeError, match=phase):
        _restorer(conn, _fail).restore(bundle)

    assert not conn.in_transaction
    _assert_untouched(conn, local)
    conn.close()


def test_a_writer_committed_before_the_reservation_is_seen_by_the_baseline_check(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    db_path = tmp_path / "target.db"
    conn = open_db(db_path)
    concurrent = open_db(db_path)
    SqlitePeopleRepository(concurrent).save_person(Person(canonical_name="Late", created_at=_NOW, updated_at=_NOW))

    with pytest.raises(TargetNotEmptyError) as error:
        _restorer(conn).restore(bundle)

    assert any(detail.startswith("persons:") for detail in error.value.details)
    concurrent.close()
    conn.close()


def test_a_writer_starting_after_the_reservation_cannot_interleave(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    db_path = tmp_path / "target.db"
    conn = open_db(db_path)
    concurrent = open_db(db_path)
    concurrent.execute("PRAGMA busy_timeout=50")
    blocked: list[str] = []

    def _write_from_elsewhere(phase: str) -> None:
        if phase != "reservation":
            return
        try:
            SqlitePeopleRepository(concurrent).save_person(
                Person(canonical_name="Intruder", created_at=_NOW, updated_at=_NOW)
            )
        except sqlite3.OperationalError as exc:
            blocked.append(str(exc))

    _restorer(conn, _write_from_elsewhere).restore(bundle)

    assert blocked, "a second writer must not acquire the reserved write lock"
    assert not any(
        row["canonical_name"] == "Intruder"
        for row in conn.execute("SELECT canonical_name FROM persons").fetchall()
    )
    concurrent.close()
    conn.close()


def test_a_destination_already_reserved_by_another_writer_is_reported_structurally(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    """A concurrent writer — a running server, say — must not surface as a raw sqlite error."""
    db_path = tmp_path / "target.db"
    conn = open_db(db_path)
    conn.execute("PRAGMA busy_timeout=50")
    holder = open_db(db_path)
    holder.execute("BEGIN IMMEDIATE")

    with pytest.raises(RestoreUnavailableError) as error:
        _restorer(conn).restore(bundle)

    assert error.value.code == "restore_unavailable"
    assert any("cannot reserve the destination" in detail for detail in error.value.details)
    assert not conn.in_transaction
    holder.rollback()
    assert _count(conn, "persons") == 0
    holder.close()
    conn.close()


def test_restore_refuses_to_run_inside_an_outer_transaction(
    tmp_path: Path,
    bundle: SyncBundleDocument,
) -> None:
    conn = open_db(tmp_path / "target.db")
    conn.execute("BEGIN")

    with pytest.raises(Exception, match="outer transaction"):
        _restorer(conn).restore(bundle)

    conn.rollback()
    conn.close()
