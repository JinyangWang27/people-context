"""CLI behaviour for the `pctx stale` recency report."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from people_context import cli
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqlitePeopleRepository,
    SqliteRecordStore,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    open_db,
)
from people_context.app.records import RecordInteraction, RecordInteractionInput
from people_context.app.relationships import SetRelationship, SetRelationshipInput
from people_context.domain.person import Person
from people_context.domain.shared import Sensitivity
from people_context.ports.clock import SystemClock


def _seed(db_path: Path) -> dict[str, Person]:
    conn = open_db(db_path)
    try:
        repo = SqlitePeopleRepository(conn)
        audit = SqliteAuditLog(conn)
        clock = SystemClock()
        people = {
            "me": Person(canonical_name="Me", is_self=True),
            "alice": Person(canonical_name="Alice"),
            "bob": Person(canonical_name="Bob"),
            "carol": Person(canonical_name="Carol"),
        }
        for person in people.values():
            repo.save_person(person)
        relationships = SetRelationship(
            repo,
            SqliteRelationshipStore(conn),
            audit,
            clock,
            SqliteRelationshipVocabularyStore(conn),
        )
        relationships.execute(
            SetRelationshipInput(subject_id=people["me"].id, object_id=people["alice"].id, type="colleague of")
        )
        relationships.execute(
            SetRelationshipInput(subject_id=people["me"].id, object_id=people["bob"].id, type="friend of")
        )
        interactions = RecordInteraction(repo, SqliteRecordStore(conn), audit, clock)
        interactions.execute(
            RecordInteractionInput(
                summary="quarterly sync",
                participant_ids=[people["alice"].id],
                occurred_at=datetime.now(UTC) - timedelta(days=200),
            )
        )
        interactions.execute(
            RecordInteractionInput(
                summary="coffee",
                participant_ids=[people["bob"].id],
                occurred_at=datetime.now(UTC) - timedelta(days=2),
            )
        )
        interactions.execute(
            RecordInteractionInput(
                summary="private matter",
                participant_ids=[people["carol"].id],
                occurred_at=datetime.now(UTC) - timedelta(days=1),
                sensitivity=Sensitivity.RESTRICTED,
            )
        )
        return people
    finally:
        conn.close()


def test_stale_lists_never_contacted_people_first(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    people = _seed(db_file)

    code = cli.main(["--db", str(db_file), "stale"])

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert code == 0
    assert lines[0].split() == ["ID", "NAME", "CATEGORIES", "LAST", "INTERACTION", "DAYS", "COUNT"]
    assert [line.split()[1] for line in lines[1:]] == ["Carol", "Alice"]
    assert "never" in lines[1]
    assert people["bob"].id not in out
    assert people["me"].id not in out
    assert "private matter" not in out


def test_stale_reports_categories_and_ordinary_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "stale", "--category", "Professional"])

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert code == 0
    assert len(lines) == 2
    assert lines[1].split()[1:3] == ["Alice", "professional"]
    assert lines[1].split()[-2:] == ["200", "1"]


def test_stale_truncates_and_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "stale", "--limit", "1"])

    out = capsys.readouterr().out
    assert code == 0
    assert "More people qualify; raise --limit to see them." in out


def test_stale_reports_nothing_when_no_person_qualifies(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "stale", "--category", "family"])

    assert code == 0
    assert capsys.readouterr().out.strip() == "No stale relationships."


def test_a_high_threshold_still_reports_people_with_no_ordinary_interaction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "stale", "--threshold-days", "36500"])

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert code == 0
    assert [line.split()[1] for line in lines[1:]] == ["Carol"]


@pytest.mark.parametrize(
    "arguments",
    [["--threshold-days", "36501"], ["--threshold-days", "-1"], ["--limit", "0"], ["--limit", "101"]],
)
def test_stale_refuses_out_of_range_parameters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "stale", *arguments])

    assert code == 2
    assert "must be between" in capsys.readouterr().err
