"""Replayable changelog capture and lifecycle grouping tests."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteChangelog,
    SqliteForgetStore,
    SqliteMergeStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqlitePreferencesStore,
    SqliteRecordStore,
    open_db,
)
from people_context.app.context import SetCommunicationPhilosophy, SetCommunicationPhilosophyInput
from people_context.app.people import (
    AddAlias,
    AddAliasInput,
    EditPerson,
    EditPersonInput,
    MergePeople,
    RememberPerson,
    RememberPersonInput,
)
from people_context.app.records import (
    CompleteReminder,
    CompleteReminderInput,
    CorrectRecord,
    CorrectRecordInput,
    RecordFact,
    RecordFactInput,
    RecordInteraction,
    RecordInteractionInput,
    RecordObservation,
    RecordObservationInput,
    RecordTrait,
    RecordTraitInput,
    SetAffiliation,
    SetAffiliationInput,
    SetReminder,
    SetReminderInput,
)
from people_context.app.relationships import SetRelationship, SetRelationshipInput
from people_context.domain.preferences import PREF_COMMUNICATION_PHILOSOPHY
from people_context.domain.reminder import ReminderKind
from people_context.domain.trait import TraitCategory
from people_context.ports.changelog import ChangelogEntry
from people_context.ports.clock import Clock


class _Clock(Clock):
    def now(self) -> datetime:
        return datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def test_all_ordinary_write_paths_capture_full_replay_payloads() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    organizations = SqliteOrganizationStore(conn)
    preferences = SqlitePreferencesStore(conn)
    audit = SqliteAuditLog(conn)
    clock = _Clock()

    remember = RememberPerson(people, people, audit, clock)
    alice = remember.execute(RememberPersonInput(name="Alice", summary="Initial")).person
    bob = remember.execute(RememberPersonInput(name="Bob")).person
    AddAlias(people, people, audit, clock).execute(AddAliasInput(person_id=alice.id, value="Ally"))
    EditPerson(people, people, audit, clock).execute(EditPersonInput(person_id=alice.id, summary="Updated"))
    relationship = SetRelationship(people, records, audit, clock).execute(
        SetRelationshipInput(subject_id=alice.id, object_id=bob.id, type="colleague")
    )
    affiliation = SetAffiliation(people, organizations, records, audit, clock).execute(
        SetAffiliationInput(person_id=alice.id, org="Acme", role="Engineer")
    )
    fact = RecordFact(people, records, audit, clock).execute(
        RecordFactInput(person_id=alice.id, predicate="timezone", value="Asia/Dubai")
    )
    observation = RecordObservation(people, records, audit, clock).execute(
        RecordObservationInput(person_id=alice.id, text="Prefers concise updates")
    )
    trait = RecordTrait(people, records, audit, clock).execute(
        RecordTraitInput(person_id=alice.id, category=TraitCategory.PREFERENCE, value="Concise")
    )
    interaction = RecordInteraction(people, records, audit, clock).execute(
        RecordInteractionInput(summary="Planning call", participant_ids=[alice.id, bob.id])
    )
    reminder = SetReminder(people, records, audit, clock).execute(
        SetReminderInput(person_id=alice.id, text="Follow up", kind=ReminderKind.COMMUNICATION_NOTE)
    )
    completed = CompleteReminder(records, records, audit, clock, people=people).execute(
        CompleteReminderInput(reminder_id=reminder.id)
    )
    corrected = CorrectRecord(records, records, audit, clock, people=people).execute(
        CorrectRecordInput(entity_type="fact", entity_id=fact.id, fields={"value": "UTC+4"})
    )
    philosophy_text = "Be direct, kind, and concise."
    SetCommunicationPhilosophy(preferences, audit, clock).execute(SetCommunicationPhilosophyInput(text=philosophy_text))

    entries = SqliteChangelog(conn).list_entries(limit=100)
    by_entity = {(entry.entity_type, entry.entity_id, entry.op_kind): entry for entry in entries}
    assert by_entity[("person", alice.id, "create")].payload["canonical_name"] == "Alice"
    assert any(
        entry.entity_type == "person" and entry.entity_id == alice.id and "aliases" in entry.payload
        for entry in entries
    )
    assert by_entity[("relationship", relationship.id, "create")].payload["object_id"] == bob.id
    assert by_entity[("affiliation", affiliation.id, "create")].payload["role"] == "Engineer"
    assert by_entity[("observation", observation.id, "create")].payload["text"] == "Prefers concise updates"
    assert by_entity[("trait", trait.id, "create")].payload["value"] == "Concise"
    assert by_entity[("interaction", interaction.id, "create")].payload["participant_ids"] == [alice.id, bob.id]
    assert by_entity[("reminder", completed.id, "update")].payload["status"] == "completed"
    assert by_entity[("fact", corrected.id, "correct")].payload["value"] == "UTC+4"
    assert by_entity[("fact", corrected.id, "correct")].changed_fields == ["value"]

    preference = by_entity[("preference", PREF_COMMUNICATION_PHILOSOPHY, "prefs")]
    assert preference.payload["value"] == philosophy_text
    audit_entry = next(
        entry
        for entry in audit.list_entries()
        if entry.entity_type == "preference" and entry.entity_id == PREF_COMMUNICATION_PHILOSOPHY
    )
    assert audit_entry.payload == {"before_length": None, "after_length": len(philosophy_text)}
    assert philosophy_text not in str(audit_entry.payload)

    org_entry = next(entry for entry in entries if entry.entity_type == "organization")
    affiliation_entry = by_entity[("affiliation", affiliation.id, "create")]
    assert org_entry.transaction_id == affiliation_entry.transaction_id
    assert all(entry.schema_version == 1 and entry.actor["source"] for entry in entries)


def test_plain_write_rolls_back_primary_audit_hlc_and_changelog_on_capture_failure() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)

    def fail(checkpoint: str) -> None:
        assert checkpoint == "before_append"
        raise RuntimeError("injected changelog failure")

    audit = SqliteAuditLog(conn, changelog_failure_hook=fail, wall_clock_ms=lambda: 1000)
    with pytest.raises(RuntimeError, match="injected changelog failure"):
        RememberPerson(people, people, audit, _Clock()).execute(RememberPersonInput(name="Atomic Alice"))

    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == 0
    device = conn.execute("SELECT hlc_physical_ms, hlc_logical FROM devices WHERE retired_at IS NULL").fetchone()
    assert tuple(device) == (0, 0)


def test_merge_emits_child_operations_and_parent_manifest_in_one_transaction() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    audit = SqliteAuditLog(conn)
    clock = _Clock()
    remember = RememberPerson(people, people, audit, clock)
    primary = remember.execute(RememberPersonInput(name="Primary")).person
    duplicate = remember.execute(RememberPersonInput(name="Duplicate", summary="Sentinel summary")).person
    fact = RecordFact(people, records, audit, clock).execute(
        RecordFactInput(person_id=duplicate.id, predicate="note", value="Sentinel fact")
    )
    conn.execute("DELETE FROM changelog")
    conn.commit()

    MergePeople(people, SqliteMergeStore(conn), clock, audit).execute(primary.id, duplicate.id)

    entries = SqliteChangelog(conn).list_entries(limit=100)
    assert len({entry.transaction_id for entry in entries}) == 1
    parent = next(entry for entry in entries if entry.op_kind == "merge" and entry.entity_type == "person")
    child_fact = next(entry for entry in entries if entry.entity_type == "fact" and entry.entity_id == fact.id)
    duplicate_child = next(
        entry for entry in entries if entry.entity_type == "person" and entry.entity_id == duplicate.id
    )
    assert parent.payload["primary_id"] == primary.id
    assert parent.payload["duplicate_id"] == duplicate.id
    assert child_fact.op_kind == "update"
    assert child_fact.payload["person_id"] == primary.id
    assert child_fact.changed_fields == ["person_id"]
    assert duplicate_child.payload["deleted_at"] is not None


def test_merge_rolls_back_when_child_changelog_capture_fails() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    seed_audit = SqliteAuditLog(conn)
    clock = _Clock()
    remember = RememberPerson(people, people, seed_audit, clock)
    primary = remember.execute(RememberPersonInput(name="Primary")).person
    duplicate = remember.execute(RememberPersonInput(name="Duplicate")).person
    fact = RecordFact(people, records, seed_audit, clock).execute(
        RecordFactInput(person_id=duplicate.id, predicate="city", value="Abu Dhabi")
    )
    audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    changelog_count = conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0]

    def fail(_: str) -> None:
        raise RuntimeError("merge changelog failure")

    lifecycle = SqliteMergeStore(conn, changelog_failure_hook=fail)
    with pytest.raises(RuntimeError, match="merge changelog failure"):
        MergePeople(people, lifecycle, clock).execute(primary.id, duplicate.id)

    assert people.get(duplicate.id) is not None and people.get(duplicate.id).deleted_at is None
    assert records.get_record("fact", fact.id).person_id == duplicate.id
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == audit_count
    assert conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == changelog_count


def test_merge_then_forget_redacts_all_person_content_and_retains_id_only_tombstone() -> None:
    sentinel = "FORGOTTEN-SENTINEL-7f42"
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    audit = SqliteAuditLog(conn)
    clock = _Clock()
    remember = RememberPerson(people, people, audit, clock)
    primary = remember.execute(RememberPersonInput(name="Primary", summary=sentinel)).person
    duplicate = remember.execute(RememberPersonInput(name="Duplicate")).person
    fact = RecordFact(people, records, audit, clock).execute(
        RecordFactInput(person_id=duplicate.id, predicate="private", value=sentinel)
    )
    MergePeople(people, SqliteMergeStore(conn), clock, audit).execute(primary.id, duplicate.id)

    from people_context.app.people import Forget

    Forget(people, SqliteForgetStore(conn), clock, audit).execute(primary.id, "person")

    payloads = [row["payload_json"] for row in conn.execute("SELECT payload_json FROM changelog").fetchall()]
    assert all(sentinel not in payload for payload in payloads)
    tombstone = next(entry for entry in SqliteChangelog(conn).list_entries() if entry.op_kind == "forget")
    assert tombstone.entity_type == "person"
    assert tombstone.entity_id == primary.id
    assert tombstone.payload["target_id"] == primary.id
    assert {item["entity_id"] for item in tombstone.payload["affected_entities"]} >= {primary.id, fact.id}
    assert not ({"name", "canonical_name", "summary", "value", "text"} & _nested_keys(tombstone.payload))
    merge_rows = conn.execute(
        "SELECT payload_json FROM changelog WHERE transaction_id IN (?)",
        (next(entry.transaction_id for entry in SqliteChangelog(conn).list_entries() if entry.op_kind == "merge"),),
    ).fetchall()
    assert merge_rows and all(row["payload_json"] == '{"redacted": true}' for row in merge_rows)

    before_count = conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0]
    from people_context.app._mutation import PersonNotFoundError

    with pytest.raises(PersonNotFoundError):
        RecordFact(people, records, audit, clock).execute(
            RecordFactInput(person_id=primary.id, predicate="late", value=sentinel)
        )
    assert conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == before_count


def test_record_then_person_forget_keeps_both_tombstones_and_no_record_content() -> None:
    sentinel = "RECORD-SENTINEL-9182"
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    audit = SqliteAuditLog(conn)
    clock = _Clock()
    person = RememberPerson(people, people, audit, clock).execute(RememberPersonInput(name="Alice")).person
    fact = RecordFact(people, records, audit, clock).execute(
        RecordFactInput(person_id=person.id, predicate="secret", value=sentinel)
    )
    from people_context.app.people import Forget

    forget = Forget(people, SqliteForgetStore(conn), clock, audit)
    forget.execute(f"fact:{fact.id}", "record")
    forget.execute(person.id, "person")

    tombstones = [entry for entry in SqliteChangelog(conn).list_entries() if entry.op_kind == "forget"]
    assert {(entry.entity_type, entry.entity_id) for entry in tombstones} == {
        ("fact", fact.id),
        ("person", person.id),
    }
    assert all(sentinel not in row["payload_json"] for row in conn.execute("SELECT payload_json FROM changelog"))


def test_forget_rolls_back_deletion_redaction_audit_hlc_and_tombstone_on_capture_failure() -> None:
    sentinel = "ROLLBACK-SENTINEL-54ab"
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    audit = SqliteAuditLog(conn)
    clock = _Clock()
    person = (
        RememberPerson(people, people, audit, clock)
        .execute(RememberPersonInput(name="Atomic Alice", summary=sentinel))
        .person
    )
    fact = RecordFact(people, records, audit, clock).execute(
        RecordFactInput(person_id=person.id, predicate="secret", value=sentinel)
    )
    before_audit = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    before_changelog = conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0]
    before_hlc = tuple(
        conn.execute("SELECT hlc_physical_ms, hlc_logical FROM devices WHERE retired_at IS NULL").fetchone()
    )

    def fail(_: str) -> None:
        raise RuntimeError("forget changelog failure")

    from people_context.app.people import Forget

    lifecycle = SqliteForgetStore(conn, changelog_failure_hook=fail)
    with pytest.raises(RuntimeError, match="forget changelog failure"):
        Forget(people, lifecycle, clock).execute(person.id, "person")

    assert people.get(person.id) is not None
    assert records.get_record("fact", fact.id) is not None
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before_audit
    assert conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == before_changelog
    assert (
        tuple(conn.execute("SELECT hlc_physical_ms, hlc_logical FROM devices WHERE retired_at IS NULL").fetchone())
        == before_hlc
    )
    assert any(sentinel in row["payload_json"] for row in conn.execute("SELECT payload_json FROM changelog"))


def test_unbounded_list_entries_returns_every_row_in_the_same_descending_order() -> None:
    conn = open_db(":memory:")
    changelog = SqliteChangelog(conn)
    appended = _append_synthetic_entries(conn, changelog, count=150)
    expected = [entry.op_id for entry in sorted(appended, key=lambda entry: entry.comparison_key(), reverse=True)]

    default_page = changelog.list_entries()
    unbounded = changelog.list_entries(limit=None)

    assert len(default_page) == 100
    assert [entry.op_id for entry in default_page] == expected[:100]
    assert [entry.op_id for entry in unbounded] == expected
    assert [entry.op_id for entry in changelog.list_entries(limit=None)] == expected


def test_unbounded_list_entries_applies_the_entity_filter() -> None:
    conn = open_db(":memory:")
    changelog = SqliteChangelog(conn)
    appended = _append_synthetic_entries(conn, changelog, count=180)
    target = "person-1"
    expected = [
        entry.op_id
        for entry in sorted(
            (entry for entry in appended if entry.entity_id == target),
            key=lambda entry: entry.comparison_key(),
            reverse=True,
        )
    ]

    entries = changelog.list_entries(limit=None, entity_id=target)

    assert len(expected) > 100
    assert [entry.op_id for entry in entries] == expected
    assert {entry.entity_id for entry in entries} == {target}
    assert [entry.op_id for entry in changelog.list_entries(entity_id=target)] == expected[:100]


def _append_synthetic_entries(
    conn: sqlite3.Connection, changelog: SqliteChangelog, count: int
) -> list[ChangelogEntry]:
    """Append deterministic entries, including HLC ties broken by ``op_id``."""
    device_id = conn.execute("SELECT id FROM devices WHERE retired_at IS NULL").fetchone()["id"]
    entries = [
        ChangelogEntry(
            op_id=f"op-{index:04d}",
            device_id=device_id,
            hlc_physical_ms=1_700_000_000_000 + index // 4,
            hlc_logical=index % 2,
            transaction_id=f"txn-{index:04d}",
            entity_type="person",
            entity_id="person-1" if index % 3 else "person-2",
            op_kind="update",
            payload={"index": index},
            inserted_at=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
        )
        for index in range(count)
    ]
    for entry in entries:
        changelog.append(entry)
    return entries


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _nested_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _nested_keys(item)}
    return set()


def test_list_entries_after_a_none_cursor_replays_everything_oldest_first() -> None:
    conn = open_db(":memory:")
    changelog = SqliteChangelog(conn)
    appended = _append_synthetic_entries(conn, changelog, count=150)
    expected = [entry.op_id for entry in sorted(appended, key=ChangelogEntry.comparison_key)]

    entries = changelog.list_entries_after(None, limit=len(appended))

    assert [entry.op_id for entry in entries] == expected
    # The default bound still applies when the caller does not widen it.
    assert [entry.op_id for entry in changelog.list_entries_after(None)] == expected[:100]


def test_list_entries_after_is_bounded_and_resumable_batch_by_batch() -> None:
    conn = open_db(":memory:")
    changelog = SqliteChangelog(conn)
    appended = _append_synthetic_entries(conn, changelog, count=120)
    expected = [entry.op_id for entry in sorted(appended, key=ChangelogEntry.comparison_key)]

    drained: list[str] = []
    cursor: tuple[int, int, str, str] | None = None
    while True:
        batch = changelog.list_entries_after(cursor, limit=25)
        if not batch:
            break
        assert len(batch) <= 25
        drained.extend(entry.op_id for entry in batch)
        cursor = batch[-1].comparison_key()

    assert drained == expected


def test_list_entries_after_excludes_the_cursor_entry_itself() -> None:
    conn = open_db(":memory:")
    changelog = SqliteChangelog(conn)
    appended = _append_synthetic_entries(conn, changelog, count=10)
    ordered = sorted(appended, key=ChangelogEntry.comparison_key)

    entries = changelog.list_entries_after(ordered[3].comparison_key(), limit=100)

    assert [entry.op_id for entry in entries] == [entry.op_id for entry in ordered[4:]]
    # The newest entry's own key is the end of the tail.
    assert changelog.list_entries_after(ordered[-1].comparison_key(), limit=100) == []


def test_list_entries_after_breaks_hlc_ties_by_device_and_op_id() -> None:
    conn = open_db(":memory:")
    changelog = SqliteChangelog(conn)
    local_device = conn.execute("SELECT id FROM devices WHERE retired_at IS NULL").fetchone()["id"]
    other_device = "device-zzzz"
    conn.execute(
        "INSERT INTO devices (id, created_at, retired_at) VALUES (?, ?, ?)",
        (other_device, "2026-07-01T00:00:00+00:00", "2026-07-01T00:00:00+00:00"),
    )
    conn.commit()
    # One shared HLC pair across two devices, plus a same-device pair split only by op_id.
    tied = [
        _tied_entry(changelog, op_id="op-b", device_id=other_device),
        _tied_entry(changelog, op_id="op-a", device_id=local_device),
        _tied_entry(changelog, op_id="op-c", device_id=local_device),
    ]
    ordered = sorted(tied, key=ChangelogEntry.comparison_key)
    assert [entry.op_id for entry in ordered] == ["op-a", "op-c", "op-b"]

    from_beginning = changelog.list_entries_after(None, limit=10)
    after_first = changelog.list_entries_after(ordered[0].comparison_key(), limit=10)
    after_second = changelog.list_entries_after(ordered[1].comparison_key(), limit=10)

    assert [entry.op_id for entry in from_beginning] == ["op-a", "op-c", "op-b"]
    # A cursor on one device must not hide the tied entry recorded by the other one.
    assert [entry.op_id for entry in after_first] == ["op-c", "op-b"]
    assert [entry.op_id for entry in after_second] == ["op-b"]


def test_list_entries_after_reverses_the_order_list_entries_returns() -> None:
    conn = open_db(":memory:")
    changelog = SqliteChangelog(conn)
    _append_synthetic_entries(conn, changelog, count=40)

    ascending = changelog.list_entries_after(None, limit=100)
    descending = changelog.list_entries(limit=100)

    assert [entry.op_id for entry in ascending] == [entry.op_id for entry in reversed(descending)]


def _tied_entry(changelog: SqliteChangelog, *, op_id: str, device_id: str) -> ChangelogEntry:
    """Append one entry sharing a fixed HLC pair with every other tied entry."""
    entry = ChangelogEntry(
        op_id=op_id,
        device_id=device_id,
        hlc_physical_ms=1_700_000_000_000,
        hlc_logical=7,
        transaction_id=f"txn-{op_id}",
        entity_type="person",
        entity_id="person-1",
        op_kind="update",
        payload={"op": op_id},
        inserted_at=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
    )
    changelog.append(entry)
    return entry
