"""CLI behaviour for the `pctx export-vcard` deterministic export (M14.2)."""

from __future__ import annotations

import os
import stat
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from people_context.adapters.importers.vcard import VCardImportExtractor
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.app.people import AliasInput, RememberPerson, RememberPersonInput
from people_context.app.records import (
    RecordFact,
    RecordFactInput,
    SetAffiliation,
    SetAffiliationInput,
)
from people_context.cli import main
from people_context.domain.person import AliasKind
from people_context.domain.shared import Sensitivity

_NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)
# Matches `tests/adapters/filesystem/test_private_file.py`: the stale-destination fixture must
# differ from 0o600 so a mode-retaining write is caught, while granting nothing to group or
# other, which CodeQL `security-extended` reports even in a test.
_STALE_FIXTURE_MODE = 0o700


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _seed(db_path: Path) -> None:
    """Create one fully described person, one bare person, and one elevated birthday."""
    conn = open_db(db_path)
    try:
        repository = SqlitePeopleRepository(conn)
        records = SqliteRecordStore(conn)
        audit = SqliteAuditLog(conn)
        clock = _Clock()
        remember = RememberPerson(repository, repository, audit, clock)
        affiliate = SetAffiliation(repository, SqliteOrganizationStore(conn), records, audit, clock)
        record_fact = RecordFact(repository, records, audit, clock)

        alice = remember.execute(
            RememberPersonInput(
                name="Alice Zhang",
                aliases=[
                    AliasInput(value="Ali", kind=AliasKind.NICKNAME),
                    AliasInput(value="alice@example.com", kind=AliasKind.HANDLE),
                    AliasInput(value="@alice", kind=AliasKind.HANDLE),
                ],
            )
        ).person
        affiliate.execute(SetAffiliationInput(person_id=alice.id, org="Acme", role="Engineer"))
        affiliate.execute(SetAffiliationInput(person_id=alice.id, org="Zenith", role="Advisor"))
        record_fact.execute(
            RecordFactInput(person_id=alice.id, predicate="birthday", value="1985-04-12")
        )
        record_fact.execute(
            RecordFactInput(person_id=alice.id, predicate="dietary", value="vegetarian")
        )

        bob = remember.execute(RememberPersonInput(name="Bob")).person
        record_fact.execute(RecordFactInput(person_id=bob.id, predicate="birthday", value="--07-01"))
        record_fact.execute(
            RecordFactInput(
                person_id=bob.id,
                predicate="birthday",
                value="1990-01-02",
                sensitivity=Sensitivity.SENSITIVE,
            )
        )
    finally:
        conn.close()


def _reimport(text: str) -> list[dict[str, object]]:
    extracted = VCardImportExtractor().extract("vcard", content=text, path=None, self_addresses=set())
    return list(extracted.candidates)


def test_writes_canonical_vcards_to_stdout_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path)

    assert main(["--db", str(db_path), "export-vcard"]) == 0

    captured = capsys.readouterr()
    assert captured.out.startswith("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Alice Zhang\r\n")
    assert captured.out.endswith("END:VCARD\r\n")
    assert captured.out.count("BEGIN:VCARD") == 2
    assert "BDAY:1985-04-12\r\n" in captured.out
    assert "dietary" not in captured.out
    # Counts and the notice stay off stdout so a redirected stream is a valid vCard file.
    assert "Exported 2 contact(s) as vCard 4.0." in captured.err
    assert "Omitted 1 additional active affiliation(s)" in captured.err
    assert "Skipped 1 recurring --MM-DD birthday value(s)" in captured.err
    assert "outside the server's disclosure controls" in captured.err


def test_writes_an_owner_only_file_and_reports_counts_on_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "people.vcf"
    _seed(db_path)

    assert main(["--db", str(db_path), "export-vcard", "--output", str(output)]) == 0

    assert stat.S_IMODE(os.stat(output).st_mode) == 0o600
    text = output.read_bytes().decode("utf-8")
    assert text.count("BEGIN:VCARD") == 2
    out = capsys.readouterr().out
    assert f"Exported 2 contact(s) as vCard 4.0 to {output}." in out
    assert "outside the server's disclosure controls" in out


def test_written_cards_round_trip_through_the_unchanged_importer(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "people.vcf"
    _seed(db_path)

    assert main(["--db", str(db_path), "export-vcard", "--output", str(output)]) == 0

    candidates = _reimport(output.read_bytes().decode("utf-8"))
    assert candidates[0]["name"] == "Alice Zhang"
    assert candidates[0]["aliases"] == [
        {"value": "Ali", "kind": "nickname"},
        {"value": "alice@example.com", "kind": "handle"},
    ]
    assert candidates[1] == {
        "type": "affiliation",
        "person_ref": "card-1",
        "org": "Acme",
        "role": "Engineer",
    }
    assert candidates[2] == {
        "type": "fact",
        "person_ref": "card-1",
        "predicate": "birthday",
        "value": "1985-04-12",
    }
    assert candidates[3]["name"] == "Bob"


def test_repeated_export_of_unchanged_data_is_byte_identical(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    first = tmp_path / "first.vcf"
    second = tmp_path / "second.vcf"
    _seed(db_path)

    assert main(["--db", str(db_path), "export-vcard", "--output", str(first)]) == 0
    assert main(["--db", str(db_path), "export-vcard", "--output", str(second)]) == 0

    assert first.read_bytes() == second.read_bytes()


def test_version_flag_selects_the_dialect(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "people.vcf"
    _seed(db_path)

    assert main(["--db", str(db_path), "export-vcard", "--version", "3.0", "--output", str(output)]) == 0

    text = output.read_bytes().decode("utf-8")
    assert "VERSION:3.0\r\n" in text
    assert "VERSION:4.0" not in text
    assert _reimport(text)[2]["value"] == "1985-04-12"


def test_include_sensitive_widens_the_birthday_gate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path)

    assert main(["--db", str(db_path), "export-vcard"]) == 0
    ordinary = capsys.readouterr().out
    assert main(["--db", str(db_path), "export-vcard", "--include-sensitive"]) == 0
    widened = capsys.readouterr().out

    assert "BDAY:1990-01-02" not in ordinary
    assert "BDAY:1990-01-02" in widened


def test_reports_every_omission_as_an_aggregate_count(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each reported count names a reason and a number, never a person or a value."""
    db_path = tmp_path / "people.db"
    output = tmp_path / "people.vcf"
    _seed(db_path)
    conn = open_db(db_path)
    try:
        repository = SqlitePeopleRepository(conn)
        alice = next(
            person for person in repository.list_people() if person.canonical_name == "Alice Zhang"
        )
        record_fact = RecordFact(repository, SqliteRecordStore(conn), SqliteAuditLog(conn), _Clock())
        record_fact.execute(
            RecordFactInput(person_id=alice.id, predicate="birthday", value="1985-04-13")
        )
        record_fact.execute(
            RecordFactInput(person_id=alice.id, predicate="birthday", value="sometime in April")
        )
    finally:
        conn.close()

    assert main(["--db", str(db_path), "export-vcard", "--output", str(output)]) == 0

    out = capsys.readouterr().out
    assert "Omitted 1 additional active affiliation(s)" in out
    assert "Omitted 1 additional full-date birthday(s); a card carries one BDAY." in out
    assert "Skipped 1 recurring --MM-DD birthday value(s)" in out
    assert "Skipped 1 birthday value(s) that are not a full calendar date." in out
    assert "Alice" not in out.replace(str(output), "")
    assert "1985-04-13" not in out


def test_a_soft_deleted_person_is_not_exported(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "people.vcf"
    _seed(db_path)
    conn = open_db(db_path)
    try:
        repository = SqlitePeopleRepository(conn)
        bob = next(person for person in repository.list_people() if person.canonical_name == "Bob")
        bob.deleted_at = _NOW
        repository.save_person(bob)
    finally:
        conn.close()

    assert main(["--db", str(db_path), "export-vcard", "--output", str(output)]) == 0

    text = output.read_bytes().decode("utf-8")
    assert "FN:Bob" not in text
    assert text.count("BEGIN:VCARD") == 1


def test_an_expired_affiliation_is_evaluated_as_of_today(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "people.vcf"
    conn = open_db(db_path)
    try:
        repository = SqlitePeopleRepository(conn)
        audit = SqliteAuditLog(conn)
        clock = _Clock()
        person = RememberPerson(repository, repository, audit, clock).execute(
            RememberPersonInput(name="Alice")
        ).person
        SetAffiliation(repository, SqliteOrganizationStore(conn), SqliteRecordStore(conn), audit, clock).execute(
            SetAffiliationInput(
                person_id=person.id,
                org="Acme",
                role="Intern",
                valid_to=date(2000, 1, 1),
            )
        )
    finally:
        conn.close()

    assert main(["--db", str(db_path), "export-vcard", "--output", str(output)]) == 0

    text = output.read_bytes().decode("utf-8")
    assert "ORG:" not in text
    assert "TITLE:" not in text


def test_pre_existing_file_mode_is_reset_rather_than_retained(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "people.vcf"
    _seed(db_path)
    output.write_text("stale\n", encoding="utf-8")
    os.chmod(output, _STALE_FIXTURE_MODE)

    assert main(["--db", str(db_path), "export-vcard", "--output", str(output)]) == 0

    assert stat.S_IMODE(os.stat(output).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(output).st_mode) & (stat.S_IRWXG | stat.S_IRWXO) == 0
    assert "stale" not in output.read_text(encoding="utf-8")


def test_an_output_symlink_is_replaced_without_touching_its_target(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path)
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("keep me\n", encoding="utf-8")
    output = tmp_path / "people.vcf"
    output.symlink_to(unrelated)

    assert main(["--db", str(db_path), "export-vcard", "--output", str(output)]) == 0

    assert unrelated.read_text(encoding="utf-8") == "keep me\n"
    assert not output.is_symlink()
    assert output.read_bytes().decode("utf-8").startswith("BEGIN:VCARD\r\n")


def test_a_failed_write_preserves_the_previous_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path)
    output = tmp_path / "people.vcf"
    output.write_text("previous\n", encoding="utf-8")
    # A destination whose parent is a file cannot receive the temporary file, so
    # publication fails before it can replace anything.
    blocked = output / "nested.vcf"

    assert main(["--db", str(db_path), "export-vcard", "--output", str(blocked)]) == 1

    assert output.read_text(encoding="utf-8") == "previous\n"
    assert "Cannot write the vCards" in capsys.readouterr().err


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_refuses_to_publish_over_the_active_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    suffix: str,
) -> None:
    db_path = tmp_path / "people.db"
    _seed(db_path)
    before = db_path.read_bytes()
    target = db_path.with_name(db_path.name + suffix)

    assert main(["--db", str(db_path), "export-vcard", "--output", str(target)]) == 2

    assert db_path.read_bytes() == before
    assert "Refusing to write the vCards" in capsys.readouterr().err


def test_an_empty_store_exports_nothing_without_failing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = tmp_path / "people.db"
    output = tmp_path / "people.vcf"

    assert main(["--db", str(db_path), "export-vcard", "--output", str(output)]) == 0

    assert output.read_bytes() == b""
    assert "Exported 0 contact(s)" in capsys.readouterr().out
