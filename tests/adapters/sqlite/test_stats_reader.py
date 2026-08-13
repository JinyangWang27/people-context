"""Real-SQLite aggregation behaviour for the stats inventory port."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, date, datetime
from pathlib import Path

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqlitePeopleRepository,
    SqliteRecordStore,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    SqliteStatsReader,
    SqliteUnitOfWork,
    open_db,
)
from people_context.adapters.sqlite.stats_reader import _COMPANION_SUFFIXES
from people_context.app.records import (
    RecordFact,
    RecordFactInput,
    RecordInteraction,
    RecordInteractionInput,
    RecordObservation,
    RecordObservationInput,
)
from people_context.app.relationships import (
    AddRelationshipType,
    AddRelationshipTypeInput,
    SetRelationship,
    SetRelationshipInput,
)
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import Sensitivity
from people_context.ports.audit_log import KNOWN_AUDIT_OPERATIONS
from people_context.ports.clock import SystemClock
from people_context.ports.stats import (
    CUSTOM_RELATIONSHIP_CATEGORY,
    DOCUMENTED_TABLES,
    OTHER_AUDIT_OPERATION,
    SEEDED_RELATIONSHIP_CATEGORIES,
    STORAGE_FILE,
    STORAGE_MEMORY,
    STORAGE_UNAVAILABLE,
    UNCATEGORIZED_RELATIONSHIP,
    StatsReader,
)


class _Fixture:
    """A live SQLite database with the writers needed to populate every aggregate."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = path
        self.conn: sqlite3.Connection = open_db(path)
        self.people = SqlitePeopleRepository(self.conn)
        self.records = SqliteRecordStore(self.conn)
        self.audit = SqliteAuditLog(self.conn)
        self.clock = SystemClock()
        self.reader: StatsReader = SqliteStatsReader(self.conn, path)

    def person(self, name: str, *, is_self: bool = False, aliases: list[Alias] | None = None) -> Person:
        person = Person(canonical_name=name, is_self=is_self, aliases=aliases or [])
        self.people.save_person(person)
        return person

    def soft_delete(self, person: Person) -> Person:
        deleted = person.model_copy(update={"deleted_at": datetime.now(UTC)})
        self.people.save_person(deleted)
        return deleted

    def fact(self, person: Person, predicate: str, value: str, sensitivity: Sensitivity) -> None:
        RecordFact(self.people, self.records, self.audit, self.clock).execute(
            RecordFactInput(
                person_id=person.id,
                predicate=predicate,
                value=value,
                sensitivity=sensitivity,
            )
        )

    def observation(self, person: Person, text: str, sensitivity: Sensitivity) -> None:
        RecordObservation(self.people, self.records, self.audit, self.clock).execute(
            RecordObservationInput(person_id=person.id, text=text, sensitivity=sensitivity)
        )

    def relationship(self, subject: Person, object_: Person, type_: str) -> None:
        SetRelationship(
            self.people,
            SqliteRelationshipStore(self.conn),
            self.audit,
            self.clock,
            SqliteRelationshipVocabularyStore(self.conn),
        ).execute(SetRelationshipInput(subject_id=subject.id, object_id=object_.id, type=type_))

    def interaction(self, summary: str, participants: list[Person]) -> None:
        RecordInteraction(self.people, self.records, self.audit, self.clock).execute(
            RecordInteractionInput(
                summary=summary,
                participant_ids=[person.id for person in participants],
                occurred_at=datetime.now(UTC),
            )
        )

    def close(self) -> None:
        self.conn.close()


def test_people_are_counted_by_lifecycle_state_not_as_one_table_total() -> None:
    fixture = _Fixture()
    try:
        fixture.person("Me", is_self=True)
        fixture.person("Ada")
        fixture.soft_delete(fixture.person("Ghost"))

        inventory = fixture.reader.read_inventory()

        assert inventory.active_people == 2
        assert inventory.soft_deleted_people == 1
        assert inventory.self_people == 1
        # The raw table still holds the soft-deleted row, which is why the split exists.
        assert inventory.table_rows["persons"] == 3
    finally:
        fixture.close()


def test_an_empty_store_reports_zero_for_every_documented_table() -> None:
    """A missing table and a table with no rows must not look the same to a reader."""
    fixture = _Fixture()
    try:
        inventory = fixture.reader.read_inventory()

        assert set(inventory.table_rows) == set(DOCUMENTED_TABLES)
        assert inventory.active_people == 0
        # The seeded vocabulary and the local device identity are the only non-zero rows.
        assert inventory.table_rows["devices"] == 1
        assert inventory.table_rows["relationship_types"] > 0
        assert inventory.table_rows["facts"] == 0
    finally:
        fixture.close()


def test_every_documented_table_actually_exists_in_the_schema() -> None:
    fixture = _Fixture()
    try:
        rows = fixture.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        existing = {row["name"] for row in rows}

        assert set(DOCUMENTED_TABLES) <= existing
    finally:
        fixture.close()


def test_alias_kinds_fact_and_observation_sensitivity_are_distributed() -> None:
    fixture = _Fixture()
    try:
        ada = fixture.person(
            "Ada Lovelace",
            aliases=[
                Alias(value="ada", kind=AliasKind.HANDLE),
                Alias(value="Ada L", kind=AliasKind.NICKNAME),
            ],
        )
        grace = fixture.person("Grace Hopper", aliases=[Alias(value="grace", kind=AliasKind.HANDLE)])
        fixture.fact(ada, "city", "London", Sensitivity.PERSONAL)
        fixture.fact(grace, "diagnosis", "confidential", Sensitivity.RESTRICTED)
        fixture.observation(ada, "prefers email", Sensitivity.PERSONAL)
        fixture.observation(grace, "prefers email", Sensitivity.PERSONAL)

        inventory = fixture.reader.read_inventory()

        assert inventory.alias_kinds == {"handle": 2, "nickname": 1}
        assert inventory.fact_sensitivity == {"personal": 1, "restricted": 1}
        assert inventory.observation_sensitivity == {"personal": 2}
    finally:
        fixture.close()


def test_a_relationship_whose_type_has_no_vocabulary_row_is_grouped_not_dropped() -> None:
    fixture = _Fixture()
    try:
        me = fixture.person("Me", is_self=True)
        ada = fixture.person("Ada")
        grace = fixture.person("Grace")
        fixture.relationship(me, ada, "friend of")
        fixture.relationship(me, grace, "colleague")
        # Drift a stored type out of the vocabulary, which is what `normalize-relationships`
        # exists to resolve; the count must still add up to the number of stored rows.
        fixture.conn.execute("UPDATE relationships SET type = 'invented_type' WHERE subject_id = ?", (me.id,))

        inventory = fixture.reader.read_inventory()

        assert inventory.relationship_categories == {UNCATEGORIZED_RELATIONSHIP: 2}
        assert sum(inventory.relationship_categories.values()) == inventory.table_rows["relationships"]
    finally:
        fixture.close()


def test_an_operator_authored_category_is_counted_but_never_named() -> None:
    """`relationship-types add --category` takes free text, which must not become a bucket key."""
    fixture = _Fixture()
    try:
        me = fixture.person("Me", is_self=True)
        AddRelationshipType(
            SqliteRelationshipVocabularyStore(fixture.conn),
            SqliteRelationshipVocabularyStore(fixture.conn),
            fixture.audit,
            fixture.clock,
        ).execute(
            AddRelationshipTypeInput(
                type="confided_in",
                category="Ada's private support network",
                symmetric=True,
            )
        )
        fixture.relationship(me, fixture.person("Ada"), "confided_in")
        fixture.relationship(me, fixture.person("Grace"), "friend of")

        inventory = fixture.reader.read_inventory()

        assert inventory.relationship_categories == {"social": 1, CUSTOM_RELATIONSHIP_CATEGORY: 1}
        assert not any("ada" in key.casefold() for key in inventory.relationship_categories)
        assert sum(inventory.relationship_categories.values()) == inventory.table_rows["relationships"]
    finally:
        fixture.close()


def test_custom_categories_collapse_into_one_bucket_without_losing_the_total() -> None:
    fixture = _Fixture()
    try:
        me = fixture.person("Me", is_self=True)
        vocabulary = SqliteRelationshipVocabularyStore(fixture.conn)
        adder = AddRelationshipType(vocabulary, vocabulary, fixture.audit, fixture.clock)
        for index, category in enumerate(("first private label", "second private label")):
            adder.execute(AddRelationshipTypeInput(type=f"custom_{index}", category=category, symmetric=True))
            fixture.relationship(me, fixture.person(f"Person {index}"), f"custom_{index}")

        inventory = fixture.reader.read_inventory()

        assert inventory.relationship_categories == {CUSTOM_RELATIONSHIP_CATEGORY: 2}
    finally:
        fixture.close()


def test_a_missing_vocabulary_row_stays_distinguishable_from_a_custom_category() -> None:
    fixture = _Fixture()
    try:
        me = fixture.person("Me", is_self=True)
        vocabulary = SqliteRelationshipVocabularyStore(fixture.conn)
        AddRelationshipType(vocabulary, vocabulary, fixture.audit, fixture.clock).execute(
            AddRelationshipTypeInput(type="confided_in", category="a private label", symmetric=True)
        )
        fixture.relationship(me, fixture.person("Ada"), "confided_in")
        fixture.relationship(me, fixture.person("Grace"), "friend of")
        # Drift a second relationship's type out of the vocabulary entirely.
        fixture.conn.execute(
            "UPDATE relationships SET type = 'invented_type' WHERE type = 'friend_of'",
        )

        inventory = fixture.reader.read_inventory()

        assert inventory.relationship_categories == {
            CUSTOM_RELATIONSHIP_CATEGORY: 1,
            UNCATEGORIZED_RELATIONSHIP: 1,
        }
    finally:
        fixture.close()


def test_an_unrecognized_audit_operation_is_counted_without_being_named() -> None:
    """Restore carries an origin's audit rows verbatim, so `op` is not this release's to trust."""
    fixture = _Fixture()
    try:
        ada = fixture.person("Ada")
        fixture.fact(ada, "city", "London", Sensitivity.PERSONAL)
        fixture.conn.execute(
            "INSERT INTO audit_log (id, ts, op, entity_type, entity_id, payload_json, source)"
            " VALUES ('audit-1', '2026-01-01T00:00:00+00:00', ?, 'person', ?, '{}', 'restore')",
            ("blackmailed Ada about her diagnosis", ada.id),
        )

        inventory = fixture.reader.read_inventory()

        assert inventory.audit_operations[OTHER_AUDIT_OPERATION] == 1
        assert not any("ada" in key.casefold() for key in inventory.audit_operations)
        assert sum(inventory.audit_operations.values()) == inventory.table_rows["audit_log"]
    finally:
        fixture.close()


def test_the_operations_this_release_writes_are_all_recognized() -> None:
    """A new op that nobody adds to the known set would silently fold into `other`."""
    fixture = _Fixture()
    try:
        me = fixture.person("Me", is_self=True)
        ada = fixture.person("Ada")
        fixture.fact(ada, "city", "London", Sensitivity.PERSONAL)
        fixture.observation(ada, "prefers email", Sensitivity.PERSONAL)
        fixture.relationship(me, ada, "friend of")
        fixture.interaction("lunch", [me, ada])

        operations = fixture.reader.read_inventory().audit_operations

        assert operations
        assert OTHER_AUDIT_OPERATION not in operations
        assert set(operations) <= KNOWN_AUDIT_OPERATIONS
    finally:
        fixture.close()


def test_relationship_categories_come_from_the_vocabulary() -> None:
    fixture = _Fixture()
    try:
        me = fixture.person("Me", is_self=True)
        fixture.relationship(me, fixture.person("Ada"), "friend of")
        fixture.relationship(me, fixture.person("Grace"), "colleague")

        inventory = fixture.reader.read_inventory()

        assert inventory.relationship_categories == {"social": 1, "professional": 1}
        # A seeded category is named as itself, so the sentinel is reserved for authored ones.
        assert {"social", "professional"} <= SEEDED_RELATIONSHIP_CATEGORIES
    finally:
        fixture.close()


def test_audit_operations_are_grouped_by_operation() -> None:
    fixture = _Fixture()
    try:
        ada = fixture.person("Ada")
        fixture.fact(ada, "city", "London", Sensitivity.PERSONAL)
        fixture.fact(ada, "role", "engineer", Sensitivity.PERSONAL)

        inventory = fixture.reader.read_inventory()

        assert sum(inventory.audit_operations.values()) == inventory.table_rows["audit_log"]
        assert inventory.audit_operations
        assert all(count > 0 for count in inventory.audit_operations.values())
    finally:
        fixture.close()


def test_changelog_entries_are_keyed_by_opaque_device_id_never_a_display_name() -> None:
    fixture = _Fixture()
    try:
        ada = fixture.person("Ada")
        fixture.fact(ada, "city", "London", Sensitivity.PERSONAL)
        device = fixture.conn.execute(
            "SELECT id, display_name FROM devices WHERE retired_at IS NULL"
        ).fetchone()
        # The seeded display name is the machine hostname; that is exactly what must not
        # appear as a grouping key.
        fixture.conn.execute("UPDATE devices SET display_name = ? WHERE id = ?", ("laptop-of-ada", device["id"]))

        inventory = fixture.reader.read_inventory()

        assert set(inventory.changelog_devices) == {device["id"]}
        assert sum(inventory.changelog_devices.values()) == inventory.table_rows["changelog"]
        assert "laptop-of-ada" not in inventory.changelog_devices
    finally:
        fixture.close()


def test_the_inventory_carries_no_stored_record_text() -> None:
    fixture = _Fixture()
    try:
        ada = fixture.person("Ada Lovelace", aliases=[Alias(value="ada", kind=AliasKind.HANDLE)])
        fixture.fact(ada, "city", "a private address", Sensitivity.RESTRICTED)
        fixture.observation(ada, "a private observation", Sensitivity.RESTRICTED)
        fixture.interaction("a private dinner", [ada])

        rendered = repr(fixture.reader.read_inventory())

        for secret in ("Ada Lovelace", "ada", "a private address", "a private observation", "a private dinner"):
            assert secret not in rendered
    finally:
        fixture.close()


def test_storage_sums_the_main_file_and_its_wal_companions(tmp_path: Path) -> None:
    db_file = tmp_path / "people.db"
    fixture = _Fixture(db_file)
    try:
        # Writes that stay in the write-ahead log are the reason the main file alone would
        # understate the footprint.
        for index in range(50):
            fixture.person(f"Person {index}")

        storage = fixture.reader.read_inventory().storage

        assert storage.storage_kind == STORAGE_FILE
        assert storage.main_bytes is not None and storage.main_bytes > 0
        assert storage.wal_bytes is not None and storage.wal_bytes > 0
        assert storage.shm_bytes is not None
        assert storage.database_bytes == storage.main_bytes + storage.wal_bytes + storage.shm_bytes
        measured = {
            suffix: (db_file.parent / f"{db_file.name}{suffix}").stat().st_size
            for suffix in _COMPANION_SUFFIXES
        }
        assert storage.wal_bytes == measured["-wal"]
        assert storage.shm_bytes == measured["-shm"]
    finally:
        fixture.close()


def test_a_checkpointed_database_with_no_wal_companion_still_reports_a_total(tmp_path: Path) -> None:
    db_file = tmp_path / "people.db"
    fixture = _Fixture(db_file)
    fixture.person("Ada")
    fixture.close()
    # Closing checkpoints and removes the companions; their absence is genuinely zero bytes.
    reopened = open_db(db_file)
    try:
        for suffix in _COMPANION_SUFFIXES:
            (db_file.parent / f"{db_file.name}{suffix}").unlink(missing_ok=True)
        storage = SqliteStatsReader(reopened, db_file).read_inventory().storage

        assert storage.storage_kind == STORAGE_FILE
        assert storage.wal_bytes == 0
        assert storage.shm_bytes == 0
        assert storage.database_bytes == storage.main_bytes
    finally:
        reopened.close()


def test_an_in_memory_database_reports_an_explicit_state_rather_than_zero_bytes() -> None:
    fixture = _Fixture()
    try:
        storage = fixture.reader.read_inventory().storage

        assert storage.storage_kind == STORAGE_MEMORY
        assert storage.database_bytes is None
        assert storage.main_bytes is None
        assert storage.wal_bytes is None
        assert storage.shm_bytes is None
    finally:
        fixture.close()


def test_an_unmeasurable_path_reports_unavailable_rather_than_zero_bytes(tmp_path: Path) -> None:
    """An empty database and one whose file cannot be measured are different statements."""
    fixture = _Fixture(tmp_path / "people.db")
    try:
        reader = SqliteStatsReader(fixture.conn, tmp_path / "missing" / "people.db")

        storage = reader.read_inventory().storage

        assert storage.storage_kind == STORAGE_UNAVAILABLE
        assert storage.database_bytes is None
    finally:
        fixture.close()


def test_reading_the_inventory_writes_nothing(tmp_path: Path) -> None:
    db_file = tmp_path / "people.db"
    fixture = _Fixture(db_file)
    try:
        ada = fixture.person("Ada")
        fixture.fact(ada, "city", "London", Sensitivity.PERSONAL)
        before = (
            fixture.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
            fixture.conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0],
        )

        fixture.reader.read_inventory()

        after = (
            fixture.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
            fixture.conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0],
        )
        assert after == before
    finally:
        fixture.close()


def test_repeated_reads_over_unchanged_data_are_identical() -> None:
    fixture = _Fixture()
    try:
        me = fixture.person("Me", is_self=True)
        fixture.relationship(me, fixture.person("Ada"), "friend of")
        fixture.fact(me, "city", "London", Sensitivity.PERSONAL)

        assert fixture.reader.read_inventory() == fixture.reader.read_inventory()
    finally:
        fixture.close()


def test_every_count_describes_the_same_snapshot_despite_a_concurrent_writer(tmp_path: Path) -> None:
    """Read in autocommit these counts tear: a commit between two of them contradicts the report."""
    db_file = tmp_path / "people.db"
    fixture = _Fixture(db_file)
    for index in range(50):
        fixture.person(f"Person {index}")

    stop = threading.Event()
    failures: list[BaseException] = []

    def churn() -> None:
        # A separate connection, because the MCP server writing beside this CLI is supported.
        writer = open_db(db_file)
        try:
            repo = SqlitePeopleRepository(writer)
            index = 1000
            while not stop.is_set():
                repo.save_person(Person(canonical_name=f"Concurrent {index}"))
                index += 1
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test thread below
            failures.append(exc)
        finally:
            writer.close()

    thread = threading.Thread(target=churn)
    thread.start()
    try:
        for _ in range(200):
            inventory = fixture.reader.read_inventory()
            assert (
                inventory.active_people + inventory.soft_deleted_people
                == inventory.table_rows["persons"]
            )
            assert sum(inventory.alias_kinds.values()) == inventory.table_rows["aliases"]
    finally:
        stop.set()
        thread.join()
        fixture.close()
    assert not failures


def test_reading_joins_an_outer_transaction_instead_of_fighting_it() -> None:
    fixture = _Fixture()
    try:
        fixture.person("Ada")
        with SqliteUnitOfWork(fixture.conn):
            inventory = fixture.reader.read_inventory()
            # The caller's transaction is still the live one; the reader did not commit it.
            assert fixture.conn.in_transaction

        assert inventory.active_people == 1
    finally:
        fixture.close()


def test_the_reader_satisfies_the_port() -> None:
    fixture = _Fixture()
    try:
        assert isinstance(fixture.reader, StatsReader)
    finally:
        fixture.close()


def test_dates_are_not_needed_to_read_an_inventory() -> None:
    """The inventory is a point-in-time count, so nothing here depends on a clock."""
    fixture = _Fixture()
    try:
        ada = fixture.person("Ada")
        fixture.fact(ada, "born", str(date(1815, 12, 10)), Sensitivity.PERSONAL)

        assert fixture.reader.read_inventory().table_rows["facts"] == 1
    finally:
        fixture.close()
