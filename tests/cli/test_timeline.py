"""CLI behaviour for the read-only `pctx timeline` chronology."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.app.insights import (
    MAX_TIMELINE_LIMIT,
    PERSON_TIMELINE_FORMAT,
    PERSON_TIMELINE_VERSION,
)
from people_context.app.records import (
    RecordFact,
    RecordFactInput,
    RecordInteraction,
    RecordInteractionInput,
    RecordObservation,
    RecordObservationInput,
    SetAffiliation,
    SetAffiliationInput,
)
from people_context.cli.insights import TIMELINE_SENSITIVE_WARNING
from people_context.domain.person import Person
from people_context.domain.shared import Sensitivity
from people_context.ports.clock import SystemClock

ALICE = "Alice Zhang"


def _seed(db_path: Path) -> dict[str, Person]:
    """Seed one store with an ordinary history plus one elevated record."""
    conn = open_db(db_path)
    try:
        repo = SqlitePeopleRepository(conn)
        records = SqliteRecordStore(conn)
        audit = SqliteAuditLog(conn)
        clock = SystemClock()
        people = {
            "alice": Person(canonical_name=ALICE),
            "bob": Person(canonical_name="Bob"),
            "quiet": Person(canonical_name="Quiet Person"),
        }
        for person in people.values():
            repo.save_person(person)
        RecordInteraction(repo, records, audit, clock).execute(
            RecordInteractionInput(
                summary="Quarterly sync",
                participant_ids=[people["alice"].id, people["bob"].id],
                occurred_at=datetime(2026, 5, 1, tzinfo=UTC),
                channel="video",
            )
        )
        RecordObservation(repo, records, audit, clock).execute(
            RecordObservationInput(
                person_id=people["alice"].id,
                text="Prefers written updates",
                observed_at=datetime(2026, 4, 1, tzinfo=UTC),
            )
        )
        RecordObservation(repo, records, audit, clock).execute(
            RecordObservationInput(
                person_id=people["alice"].id,
                text="A restricted matter",
                observed_at=datetime(2026, 6, 1, tzinfo=UTC),
                sensitivity=Sensitivity.RESTRICTED,
            )
        )
        RecordFact(repo, records, audit, clock).execute(
            RecordFactInput(
                person_id=people["alice"].id,
                predicate="city",
                value="Berlin",
                valid_from=date(2026, 1, 15),
            )
        )
        SetAffiliation(repo, SqliteOrganizationStore(conn), records, audit, clock).execute(
            SetAffiliationInput(
                person_id=people["alice"].id,
                org="Acme",
                role="Engineer",
                valid_from=date(2025, 6, 1),
            )
        )
        return people
    finally:
        conn.close()


def test_the_human_timeline_is_newest_first_and_excludes_elevated_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    people = _seed(db_file)

    code = cli.main(["--db", str(db_file), "timeline", ALICE])

    captured = capsys.readouterr()
    assert code == 0
    assert people["alice"].id in captured.out
    lines = [line for line in captured.out.splitlines() if line.strip()]
    body = [line for line in lines if "2026-" in line or "2025-" in line]
    # Every row leads with the durable record's own id, then the instant it is placed at.
    assert [line.split()[1] for line in body] == [
        "2026-05-01T00:00:00+00:00",
        "2026-04-01T00:00:00+00:00",
        "2026-01-15T00:00:00+00:00",
        "2025-06-01T00:00:00+00:00",
    ]
    assert all(len(line.split()[0]) == 26 for line in body)
    assert "A restricted matter" not in captured.out
    assert captured.err == ""


def test_the_explicit_opt_in_widens_the_report_and_warns_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "timeline", ALICE, "--include-sensitive"])

    captured = capsys.readouterr()
    assert code == 0
    assert "A restricted matter" in captured.out
    assert TIMELINE_SENSITIVE_WARNING in captured.err


def test_the_json_document_is_the_whole_of_stdout_and_carries_its_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    people = _seed(db_file)

    code = cli.main(["--db", str(db_file), "timeline", ALICE, "--json"])

    captured = capsys.readouterr()
    assert code == 0
    document = json.loads(captured.out)
    assert document["format"] == PERSON_TIMELINE_FORMAT
    assert document["version"] == PERSON_TIMELINE_VERSION
    assert document["person_id"] == people["alice"].id
    assert document["include_sensitive"] is False
    assert [entry["entry_type"] for entry in document["entries"]] == [
        "interaction",
        "observation",
        "fact",
        "affiliation",
    ]
    assert all(entry["entry_id"] for entry in document["entries"])


def test_the_json_document_stays_byte_identical_across_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    cli.main(["--db", str(db_file), "timeline", ALICE, "--json"])
    first = capsys.readouterr().out
    cli.main(["--db", str(db_file), "timeline", ALICE, "--json"])
    second = capsys.readouterr().out

    assert first == second
    assert first.endswith("\n")


def test_the_sensitive_warning_does_not_contaminate_the_json_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A redirected `--json` run must stay a parseable document, warning or not."""
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "timeline", ALICE, "--include-sensitive", "--json"])

    captured = capsys.readouterr()
    assert code == 0
    document = json.loads(captured.out)
    assert document["include_sensitive"] is True
    assert TIMELINE_SENSITIVE_WARNING in captured.err


def test_a_bounded_page_says_that_more_entries_exist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "timeline", ALICE, "--limit", "2"])

    captured = capsys.readouterr()
    assert code == 0
    assert "More entries exist" in captured.out


def test_an_out_of_range_limit_exits_two_without_printing_a_partial_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "timeline", ALICE, "--limit", str(MAX_TIMELINE_LIMIT + 1)])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "limit must be between" in captured.err


def test_an_unknown_person_exits_one_and_an_ambiguous_one_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)
    conn = open_db(db_file)
    try:
        repo = SqlitePeopleRepository(conn)
        for _ in range(2):
            repo.save_person(Person(canonical_name="Twin Person"))
    finally:
        conn.close()

    unknown = cli.main(["--db", str(db_file), "timeline", "Nobody At All"])
    unknown_err = capsys.readouterr().err
    ambiguous = cli.main(["--db", str(db_file), "timeline", "Twin Person"])
    ambiguous_err = capsys.readouterr().err

    assert unknown == 1
    assert "No person found" in unknown_err
    assert ambiguous == 2
    assert "Ambiguous match" in ambiguous_err


def test_a_person_with_no_records_reports_an_empty_timeline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "timeline", "Quiet Person"])

    captured = capsys.readouterr()
    assert code == 0
    assert "No timeline entries." in captured.out


def test_a_shared_interaction_appears_on_every_participants_timeline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "timeline", "Bob"])

    captured = capsys.readouterr()
    assert code == 0
    assert "Quarterly sync" in captured.out


def test_the_timeline_records_nothing_it_reads(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)
    conn = open_db(db_file)
    try:
        before = (
            conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0],
        )
    finally:
        conn.close()

    cli.main(["--db", str(db_file), "timeline", ALICE, "--include-sensitive"])
    capsys.readouterr()

    conn = open_db(db_file)
    try:
        after = (
            conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0],
        )
    finally:
        conn.close()
    assert after == before
