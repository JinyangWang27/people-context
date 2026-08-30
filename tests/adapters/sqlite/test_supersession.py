"""Fact supersession against real SQLite: transaction grouping, atomicity, and what it is not.

The application tests pin the boundary math against fakes. What only a real database can show is
that both durable rows, both audit rows, and both replay rows commit as one unit under one logical
`transaction_id` — and that neither half can survive alone when the other fails.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteChangelog,
    SqliteCurationReader,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.app.people import RememberPerson, RememberPersonInput
from people_context.app.records import (
    SUPERSEDE_OP,
    CorrectRecord,
    CorrectRecordInput,
    InvalidSupersessionError,
    RecordFact,
    RecordFactInput,
    ReportDoctorFindings,
    SupersedeFact,
    SupersedeFactInput,
)
from people_context.domain.fact import Fact
from people_context.ports.audit_log import KNOWN_AUDIT_OPERATIONS

_NOW = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _Store:
    """One person, one fact, and the record use cases wired to a real connection."""

    def __init__(self) -> None:
        self.conn = open_db(":memory:")
        people = SqlitePeopleRepository(self.conn)
        records = SqliteRecordStore(self.conn)
        self.audit = SqliteAuditLog(self.conn)
        self.records = records
        self.people = people
        clock = _Clock()
        self.person = RememberPerson(people, people, self.audit, clock).execute(
            RememberPersonInput(name="Alice Rivera")
        ).person
        self.record_fact = RecordFact(people, records, self.audit, clock)
        self.correct = CorrectRecord(records, records, self.audit, clock, people=people)
        self.supersede = SupersedeFact(records, records, self.audit, clock, people=people)

    def fact(
        self,
        *,
        predicate: str = "employer",
        value: str = "Acme",
        valid_from: date | None = None,
        valid_to: date | None = None,
    ) -> Fact:
        return self.record_fact.execute(
            RecordFactInput(
                person_id=self.person.id,
                predicate=predicate,
                value=value,
                valid_from=valid_from,
                valid_to=valid_to,
            )
        )

    def clear_history(self) -> None:
        """Drop the setup writes so a later assertion sees only the operation under test."""
        self.conn.execute("DELETE FROM changelog")
        self.conn.execute("DELETE FROM audit_log")
        self.conn.commit()

    def fact_rows(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM facts ORDER BY id").fetchall())


def test_both_durable_rows_commit_together() -> None:
    store = _Store()
    original = store.fact(valid_from=date(2024, 1, 1), valid_to=date(2026, 12, 31))

    result = store.supersede.execute(
        SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
    )

    rows = {row["id"]: row for row in store.fact_rows()}
    assert len(rows) == 2
    assert (rows[original.id]["value"], rows[original.id]["valid_to"]) == ("Acme", "2026-06-30")
    assert rows[original.id]["valid_from"] == "2024-01-01"
    replacement = rows[result.replacement.id]
    assert (replacement["value"], replacement["valid_from"], replacement["valid_to"]) == (
        "Globex",
        "2026-07-01",
        "2026-12-31",
    )
    assert replacement["person_id"] == rows[original.id]["person_id"] == store.person.id
    assert replacement["predicate"] == "employer"


def test_the_two_row_effects_share_one_non_empty_transaction_id() -> None:
    """SQLite atomicity is not grouping metadata; the sync contract needs the shared id."""
    store = _Store()
    original = store.fact()
    store.clear_history()

    result = store.supersede.execute(
        SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
    )

    entries = SqliteChangelog(store.conn).list_entries(limit=100)
    assert len(entries) == 2
    assert {entry.transaction_id for entry in entries} == {result.transaction_id}
    assert result.transaction_id


def test_the_two_row_effects_stay_distinct_operations_on_distinct_entities() -> None:
    store = _Store()
    original = store.fact()
    store.clear_history()

    result = store.supersede.execute(
        SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
    )

    by_op = {entry.op_kind: entry for entry in SqliteChangelog(store.conn).list_entries(limit=100)}
    assert set(by_op) == {SUPERSEDE_OP, "create"}
    assert by_op[SUPERSEDE_OP].entity_id == original.id
    assert by_op["create"].entity_id == result.replacement.id
    assert by_op[SUPERSEDE_OP].entity_type == by_op["create"].entity_type == "fact"
    assert by_op[SUPERSEDE_OP].changed_fields == ["valid_to"]
    # The replay payload of the closure is the closed row, so a replayer applies the new endpoint.
    assert by_op[SUPERSEDE_OP].payload["period"]["valid_to"] == "2026-06-30"
    assert by_op[SUPERSEDE_OP].payload["value"] == "Acme"


def test_supersession_does_not_masquerade_as_a_correction() -> None:
    """A replayer and an inspector must be able to tell a transition from an in-place repair."""
    store = _Store()
    superseded = store.fact(predicate="employer")
    corrected = store.fact(predicate="city", value="Lisban")
    store.clear_history()

    store.supersede.execute(
        SupersedeFactInput(fact_id=superseded.id, new_value="Globex", effective_from=date(2026, 7, 1))
    )
    store.correct.execute(CorrectRecordInput(entity_type="fact", entity_id=corrected.id, fields={"value": "Lisbon"}))

    by_entity = {
        (entry.entity_id, entry.op_kind): entry for entry in SqliteChangelog(store.conn).list_entries(limit=100)
    }
    assert (superseded.id, SUPERSEDE_OP) in by_entity
    assert (superseded.id, "correct") not in by_entity
    # `correct_record` keeps its released op kind and its value-replacement meaning.
    assert (corrected.id, "correct") in by_entity
    assert by_entity[(corrected.id, "correct")].changed_fields == ["value"]
    assert by_entity[(corrected.id, "correct")].payload["value"] == "Lisbon"


def test_the_audit_row_records_the_old_value_on_both_sides_of_the_closure() -> None:
    store = _Store()
    original = store.fact(value="Acme")
    store.clear_history()

    store.supersede.execute(
        SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
    )

    closure = next(entry for entry in store.audit.list_entries() if entry.op == SUPERSEDE_OP)
    assert closure.payload["before"]["value"] == closure.payload["after"]["value"] == "Acme"
    assert closure.payload["before"]["period"]["valid_to"] is None
    assert closure.payload["after"]["period"]["valid_to"] == "2026-06-30"


def test_a_failure_closing_the_old_row_leaves_nothing_behind() -> None:
    def fail(checkpoint: str) -> None:
        assert checkpoint == "before_append"
        raise RuntimeError("injected closure failure")

    store = _Store()
    original = store.fact()
    store.clear_history()
    # The audit is replaced only now, so the failing hook sees the closure as its first append.
    store.supersede = SupersedeFact(
        store.records,
        store.records,
        SqliteAuditLog(store.conn, changelog_failure_hook=fail),
        _Clock(),
        people=store.people,
    )

    with pytest.raises(RuntimeError, match="injected closure failure"):
        store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
        )

    rows = store.fact_rows()
    assert len(rows) == 1
    assert rows[0]["valid_to"] is None
    assert store.conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_a_failure_creating_the_replacement_rolls_the_closure_back_too() -> None:
    """Neither "old closed, new missing" nor a one-sided changelog group may survive."""
    appends: list[str] = []

    def fail(checkpoint: str) -> None:
        appends.append(checkpoint)
        if len(appends) > 1:
            raise RuntimeError("injected replacement failure")

    store = _Store()
    original = store.fact()
    store.clear_history()
    store.supersede = SupersedeFact(
        store.records,
        store.records,
        SqliteAuditLog(store.conn, changelog_failure_hook=fail),
        _Clock(),
        people=store.people,
    )

    with pytest.raises(RuntimeError, match="injected replacement failure"):
        store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
        )

    rows = store.fact_rows()
    assert len(rows) == 1
    assert (rows[0]["id"], rows[0]["value"], rows[0]["valid_to"]) == (original.id, "Acme", None)
    assert store.conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_a_refused_supersession_writes_nothing_at_all() -> None:
    store = _Store()
    original = store.fact(valid_from=date(2020, 1, 1), valid_to=date(2020, 12, 31))
    store.clear_history()

    with pytest.raises(InvalidSupersessionError):
        store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
        )

    assert len(store.fact_rows()) == 1
    assert store.conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0] == 0


def test_supersession_leaves_the_doctor_report_free_of_a_contradiction() -> None:
    """M15 is unchanged: a well-formed transition is disjoint history, not a conflict."""
    store = _Store()
    original = store.fact(predicate="employer", value="Acme", valid_from=date(2024, 1, 1))
    doctor = ReportDoctorFindings(SqliteCurationReader(store.conn), _Clock())
    before = doctor.execute()

    store.supersede.execute(
        SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
    )

    after = doctor.execute()
    assert [finding.code for finding in after.findings] == [finding.code for finding in before.findings]
    assert all(finding.code != "contradictory_fact" for finding in after.findings)


def test_the_closure_is_a_known_operation_rather_than_an_unrecognized_one() -> None:
    """`other` means "restored from an origin we did not write"; a local closure is not that."""
    store = _Store()
    original = store.fact()

    store.supersede.execute(
        SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
    )

    operations = {row[0] for row in store.conn.execute("SELECT DISTINCT op FROM audit_log").fetchall()}
    assert SUPERSEDE_OP in operations
    assert operations <= KNOWN_AUDIT_OPERATIONS


def test_the_doctor_still_reports_an_overlapping_pair_recorded_without_supersession() -> None:
    """The clean report above is the transition's doing, not a weakened doctor."""
    store = _Store()
    store.fact(predicate="employer", value="Acme", valid_from=date(2024, 1, 1))
    store.fact(predicate="employer", value="Globex", valid_from=date(2026, 7, 1))

    report = ReportDoctorFindings(SqliteCurationReader(store.conn), _Clock()).execute()

    assert any(finding.code == "contradictory_fact" for finding in report.findings)
