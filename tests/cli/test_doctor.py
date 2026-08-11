"""CLI behaviour for the report-only `pctx doctor` findings."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
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
from people_context.app.records import RecordFact, RecordFactInput, RecordInteraction, RecordInteractionInput
from people_context.app.relationships import SetRelationship, SetRelationshipInput
from people_context.domain.person import Alias, AliasKind, Person
from people_context.ports.clock import SystemClock


def _seed(db_path: Path) -> dict[str, Person]:
    """Seed one store holding every finding class exactly once."""
    conn = open_db(db_path)
    try:
        repo = SqlitePeopleRepository(conn)
        audit = SqliteAuditLog(conn)
        records = SqliteRecordStore(conn)
        clock = SystemClock()
        people = {
            "me": Person(canonical_name="Me", is_self=True),
            # Two active people share a handle, and the same pair also shares a name.
            "alice": Person(
                canonical_name="Alice Zhang",
                aliases=[Alias(value="azhang", kind=AliasKind.HANDLE)],
            ),
            "alias_twin": Person(
                canonical_name="alice  zhang",
                aliases=[Alias(value="AZhang", kind=AliasKind.HANDLE)],
            ),
            # A third person shares only the name, so handle precedence must not hide them.
            "name_twin": Person(canonical_name="ALICE ZHANG"),
            "ghost": Person(canonical_name="Ghost"),
        }
        for person in people.values():
            repo.save_person(person)

        facts = RecordFact(repo, records, audit, clock)
        facts.execute(
            RecordFactInput(
                person_id=people["alice"].id,
                predicate="city",
                value="Berlin",
                valid_from=date(2020, 1, 1),
                valid_to=date(2024, 1, 1),
            )
        )
        facts.execute(
            RecordFactInput(
                person_id=people["alice"].id,
                predicate="city",
                value="Paris",
                valid_from=date(2024, 1, 1),
            )
        )
        SetRelationship(
            repo,
            SqliteRelationshipStore(conn),
            audit,
            clock,
            SqliteRelationshipVocabularyStore(conn),
        ).execute(
            SetRelationshipInput(
                subject_id=people["me"].id,
                object_id=people["ghost"].id,
                type="friend of",
            )
        )
        RecordInteraction(repo, records, audit, clock).execute(
            RecordInteractionInput(
                summary="a private dinner",
                participant_ids=[people["ghost"].id, people["me"].id],
                occurred_at=datetime.now(UTC),
            )
        )
        # Soft-delete after the rows exist, which is the drift the finding is about.
        people["ghost"] = people["ghost"].model_copy(update={"deleted_at": datetime.now(UTC)})
        repo.save_person(people["ghost"])
        return people
    finally:
        conn.close()


def test_doctor_reports_every_finding_class_and_still_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "doctor"])

    out = capsys.readouterr().out
    assert code == 0
    assert "[duplicate_handle]" in out
    assert "[duplicate_alias]" in out
    assert "[contradictory_fact]" in out
    assert "[dangling_reference]" in out


def test_the_disclosure_notice_precedes_the_evidence_it_warns_about(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A warning printed after the values are already on screen cannot inform the decision."""
    db_file = tmp_path / "people.db"
    _seed(db_file)

    cli.main(["--db", str(db_file), "doctor"])

    out = capsys.readouterr().out
    assert out.splitlines()[0] == (
        "This report juxtaposes stored personal values, including elevated ones, and is outside "
        "the server's disclosure controls. Inspect it before sharing it anywhere."
    )
    assert out.index("Inspect it before sharing") < out.index("[duplicate_handle]")
    assert out.index("Inspect it before sharing") < out.index("Berlin")


def test_a_clean_store_prints_no_disclosure_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no findings there is no stored personal value on screen to warn about."""
    db_file = tmp_path / "people.db"
    conn = open_db(db_file)
    SqlitePeopleRepository(conn).save_person(Person(canonical_name="Me", is_self=True))
    conn.close()

    cli.main(["--db", str(db_file), "doctor"])

    assert "Inspect it before sharing" not in capsys.readouterr().out


def test_an_incomplete_suggestion_says_what_the_operator_must_supply(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    cli.main(["--db", str(db_file), "doctor", "--only", "contradictory_fact"])

    out = capsys.readouterr().out
    assert "mcp  correct_record" in out
    assert "(you supply: fields)" in out
    # A complete suggestion is not annotated, so the two are distinguishable at a glance.
    capsys.readouterr()
    cli.main(["--db", str(db_file), "doctor", "--only", "duplicate_handle"])
    assert "you supply" not in capsys.readouterr().out


def test_doctor_reports_nothing_on_a_clean_store(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    conn = open_db(db_file)
    SqlitePeopleRepository(conn).save_person(Person(canonical_name="Me", is_self=True))
    conn.close()

    code = cli.main(["--db", str(db_file), "doctor"])

    assert code == 0
    assert capsys.readouterr().out.strip() == "No findings."


def test_the_human_report_renders_copyable_actions_addressed_by_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    people = _seed(db_file)

    cli.main(["--db", str(db_file), "doctor", "--only", "duplicate_handle"])

    out = capsys.readouterr().out
    ids = sorted([people["alice"].id, people["alias_twin"].id])
    assert f"action   cli  pctx show {ids[0]}" in out
    assert f'action   mcp  merge_people {{"primary_id": "{ids[0]}", "duplicate_id": "{ids[1]}"}}' in out
    action_lines = [line for line in out.splitlines() if line.strip().startswith("action")]
    assert action_lines
    for line in action_lines:
        assert not {"Alice", "Zhang", "alice", "zhang"}.intersection(line.split())


def test_handle_precedence_reports_the_shared_pair_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    people = _seed(db_file)

    cli.main(["--db", str(db_file), "doctor", "--only", "duplicate_handle,duplicate_alias"])

    out = capsys.readouterr().out
    assert out.count("[duplicate_handle]") == 1
    # The handle pair is suppressed, leaving only the pairs formed with the name-only twin.
    assert out.count("[duplicate_alias]") == 2
    assert people["name_twin"].id in out


def test_only_filters_the_report_and_an_unknown_code_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "doctor", "--only", "contradictory_fact"])
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("[contradictory_fact]") == 1
    assert "[duplicate_handle]" not in out

    code = cli.main(["--db", str(db_file), "doctor", "--only", "typo_code"])
    captured = capsys.readouterr()
    assert code == 2
    assert "unknown finding code(s): typo_code" in captured.err
    assert captured.out == ""


def test_the_json_document_is_the_whole_of_stdout_and_the_notice_goes_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    code = cli.main(["--db", str(db_file), "doctor", "--json"])

    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert code == 0
    assert document["format"] == "people-context-doctor"
    assert document["version"] == 1
    assert {finding["code"] for finding in document["findings"]} == {
        "duplicate_handle",
        "duplicate_alias",
        "contradictory_fact",
        "dangling_reference",
    }
    assert "inspect it before sharing" in captured.err.casefold()


def test_the_report_never_prints_interaction_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    cli.main(["--db", str(db_file), "doctor"])
    human = capsys.readouterr().out
    cli.main(["--db", str(db_file), "doctor", "--json"])
    machine = capsys.readouterr().out

    assert "private dinner" not in human
    assert "private dinner" not in machine


def test_the_report_writes_nothing_to_the_store(tmp_path: Path) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)
    conn = open_db(db_file)
    before = (
        conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0],
    )
    conn.close()

    assert cli.main(["--db", str(db_file), "doctor"]) == 0

    conn = open_db(db_file)
    after = (
        conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0],
    )
    conn.close()
    assert after == before


def test_repeated_runs_produce_identical_findings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_file = tmp_path / "people.db"
    _seed(db_file)

    cli.main(["--db", str(db_file), "doctor", "--json"])
    first = json.loads(capsys.readouterr().out)
    cli.main(["--db", str(db_file), "doctor", "--json"])
    second = json.loads(capsys.readouterr().out)

    assert first["findings"] == second["findings"]
