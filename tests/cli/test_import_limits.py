"""The resource ceilings the `pctx import` boundary applies, and who they do not apply to."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from people_context import cli
from people_context.adapters.importers.bounded_source import (
    SOURCE_TOO_LARGE,
)
from people_context.adapters.importers.bounded_source import (
    TOO_MANY_CANDIDATES as EXTRACTION_TOO_MANY_CANDIDATES,
)
from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.importers.router import ImportExtractorRouter
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteImportStagingStore,
    SqlitePeopleRepository,
    open_db,
)
from people_context.app.imports import (
    CLI_IMPORT_BUDGET,
    MAX_CLI_CANDIDATE_JSON_BYTES,
    MAX_CLI_RETAINED_PARSE_RECORDS,
    MAX_CLI_SOURCE_BYTES,
    MAX_CLI_STAGED_CANDIDATES,
    MAX_CLI_STAGED_PAYLOAD_BYTES,
    STAGED_PAYLOAD_TOO_LARGE,
    TOO_MANY_CANDIDATES,
    CandidateStager,
    ImportBudget,
    ImportContent,
    ImportPipelineError,
)
from people_context.app.people import RememberPerson, RememberPersonInput

_LINKEDIN_HEADERS = "First Name,Last Name,URL,Email Address,Company,Position,Connected On,Notes"
_NOW = datetime(2026, 7, 22, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _sparse(path: Path, size: int) -> Path:
    """Create a file of exactly ``size`` zero bytes without spending that much disk."""
    path.touch()
    os.truncate(path, size)
    return path


def _staging_rows(db_file: Path) -> list[sqlite3.Row]:
    conn = open_db(db_file)
    try:
        return conn.execute("SELECT * FROM import_staging").fetchall()
    finally:
        conn.close()


def test_the_cli_budget_is_the_documented_ceiling() -> None:
    assert MAX_CLI_SOURCE_BYTES == 64 * 1024 * 1024
    assert MAX_CLI_STAGED_CANDIDATES == 100_000
    assert MAX_CLI_STAGED_PAYLOAD_BYTES == 64 * 1024 * 1024
    assert MAX_CLI_CANDIDATE_JSON_BYTES == 1024 * 1024
    assert (
        ImportBudget(
            max_source_bytes=MAX_CLI_SOURCE_BYTES,
            max_candidates=MAX_CLI_STAGED_CANDIDATES,
            max_staged_payload_bytes=MAX_CLI_STAGED_PAYLOAD_BYTES,
            max_retained_parse_records=MAX_CLI_RETAINED_PARSE_RECORDS,
        )
        == CLI_IMPORT_BUDGET
    )


def test_the_parser_work_backstop_cannot_narrow_what_the_byte_ceiling_admits() -> None:
    """The retention backstop is derived from the byte ceiling, not chosen independently.

    Every parsed record costs at least one byte of source, so a ceiling equal to the source
    budget cannot be reached by anything that budget already admits. That is the whole point:
    M20 adds no fourth user-visible limit to `pctx import`.
    """
    assert MAX_CLI_RETAINED_PARSE_RECORDS == MAX_CLI_SOURCE_BYTES


def test_a_source_one_byte_over_the_limit_is_refused_before_any_staging(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    oversized = _sparse(tmp_path / "over.csv", MAX_CLI_SOURCE_BYTES + 1)

    code = cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(oversized)])

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(MAX_CLI_SOURCE_BYTES) in captured.err
    assert str(oversized) not in captured.err
    assert _staging_rows(db_file) == []


def test_a_source_exactly_at_the_limit_is_not_refused_for_its_size(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """At the limit the read proceeds; the file is then rejected on its own merits, not its size."""
    db_file = tmp_path / "people.db"
    exact = _sparse(tmp_path / "exact.csv", MAX_CLI_SOURCE_BYTES)

    code = cli.main(["--db", str(db_file), "import", "stage", "linkedin", str(exact)])

    assert code == 1
    error = capsys.readouterr().err
    assert str(MAX_CLI_SOURCE_BYTES) not in error
    assert "missing required canonical headers" in error
    assert _staging_rows(db_file) == []


def test_an_oversized_mbox_is_refused_before_a_single_message_is_read(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_file = tmp_path / "people.db"
    oversized = _sparse(tmp_path / "over.mbox", MAX_CLI_SOURCE_BYTES + 1)

    code = cli.main(["--db", str(db_file), "import", "stage", "mbox", str(oversized)])

    assert code == 1
    assert str(MAX_CLI_SOURCE_BYTES) in capsys.readouterr().err
    assert _staging_rows(db_file) == []


def test_the_mbox_budget_meters_the_scan_not_only_the_headers_it_parses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`mailbox` reads the whole file to build its table of contents before any factory call.

    A budget that metered only parsed headers would measure none of that, so the refusal has
    to come from the scan itself. The reported-size pre-check is disabled here precisely so
    that the metered read is the only thing left that can refuse.
    """
    source = tmp_path / "large.mbox"
    source.write_bytes(
        b"From sender@example.com Thu Jul 22 09:00:00 2026\n"
        b"From: Amina <amina@example.com>\n"
        b"Date: Wed, 22 Jul 2026 09:00:00 +0000\n"
        b"\n" + b"B" * 4096 + b"\n"
    )
    headers_only = 128
    assert source.stat().st_size > headers_only
    monkeypatch.setattr(
        "people_context.adapters.importers.email.refuse_oversized_file",
        lambda path, *, max_bytes: None,
    )

    with open_db(":memory:") as conn, pytest.raises(ImportExtractionError) as refusal:
        _import_content(conn).execute(
            "mbox",
            path=str(source),
            budget=ImportBudget(max_source_bytes=headers_only),
        )

    assert refusal.value.code == SOURCE_TOO_LARGE


def test_an_mbox_exactly_at_the_budget_is_read_rather_than_refused(tmp_path: Path) -> None:
    """The scan reaches the final byte and no further, so the ceiling itself must pass."""
    source = tmp_path / "exact.mbox"
    source.write_bytes(
        b"From sender@example.com Thu Jul 22 09:00:00 2026\n"
        b"From: Amina <amina@example.com>\n"
        b"Date: Wed, 22 Jul 2026 09:00:00 +0000\n"
        b"\n"
        b"body\n"
    )
    exact = source.stat().st_size

    with open_db(":memory:") as conn:
        batch = _import_content(conn).execute(
            "mbox",
            path=str(source),
            budget=ImportBudget(max_source_bytes=exact),
        )

        assert batch.candidate_count == 2

        with pytest.raises(ImportExtractionError) as refusal:
            _import_content(conn).execute(
                "mbox",
                path=str(source),
                budget=ImportBudget(max_source_bytes=exact - 1),
            )

    assert refusal.value.code == SOURCE_TOO_LARGE


def test_extraction_stops_instead_of_accumulating_past_the_candidate_ceiling(tmp_path: Path) -> None:
    """A file inside the byte budget can still expand well past the candidate ceiling.

    Refusing only after `ExtractedImport` is complete would let a dense export allocate
    millions of candidates first, so the ceiling has to reach extraction itself.
    """
    source = tmp_path / "dense.csv"
    rows = "\n".join(f"Person{index},Surname{index},u,p{index}@example.com,,,," for index in range(200))
    source.write_text(f"{_LINKEDIN_HEADERS}\n{rows}\n", encoding="utf-8")

    with open_db(":memory:") as conn:
        with pytest.raises(ImportExtractionError) as refusal:
            _import_content(conn).execute(
                "linkedin",
                path=str(source),
                budget=ImportBudget(max_candidates=20),
            )

        assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0
    assert refusal.value.code == EXTRACTION_TOO_MANY_CANDIDATES
    assert "20" in str(refusal.value)
    assert "Person42" not in str(refusal.value)


@pytest.mark.parametrize(
    ("source_type", "filename", "body"),
    [
        (
            "linkedin",
            "connections.csv",
            f"{_LINKEDIN_HEADERS}\n" + "".join(f"A{i},B{i},u,a{i}@e.com,Acme,Eng,22 Jul 2026,n\n" for i in range(40)),
        ),
        (
            "outlook",
            "contacts.csv",
            "First Name,Middle Name,Last Name,E-mail Address,Company,Job Title,Birthday\n"
            + "".join(f"A{i},,B{i},a{i}@e.com,Acme,Eng,\n" for i in range(40)),
        ),
        (
            "vcard",
            "contacts.vcf",
            "".join(
                f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Person {i}\r\nEMAIL:p{i}@e.com\r\nEND:VCARD\r\n" for i in range(40)
            ),
        ),
        (
            "ics",
            "calendar.ics",
            "BEGIN:VCALENDAR\r\n"
            + "".join(
                f"BEGIN:VEVENT\r\nUID:e{i}\r\nDTSTART:2026072{i % 10}T090000Z\r\nSUMMARY:S\r\n"
                f"ATTENDEE;CN=Person {i}:mailto:p{i}@e.com\r\nEND:VEVENT\r\n"
                for i in range(40)
            )
            + "END:VCALENDAR\r\n",
        ),
        (
            "whatsapp",
            "chat.txt",
            "".join(f"[2{i % 10}/07/2026, 09:00:00] Person {i}: hi\n" for i in range(40)),
        ),
        (
            "mbox",
            "archive.mbox",
            "".join(
                "From s@e.com Thu Jul 22 09:00:00 2026\n"
                f"From: Person {i} <p{i}@e.com>\n"
                "Date: Wed, 22 Jul 2026 09:00:00 +0000\n"
                "\nbody\n\n"
                for i in range(40)
            ),
        ),
    ],
)
def test_every_extractor_honours_the_candidate_ceiling(
    source_type: str,
    filename: str,
    body: str,
    tmp_path: Path,
) -> None:
    source = tmp_path / filename
    source.write_text(body, encoding="utf-8")

    with open_db(":memory:") as conn:
        with pytest.raises(ImportExtractionError) as refusal:
            _import_content(conn).execute(
                source_type,
                path=str(source),
                budget=ImportBudget(max_candidates=5),
            )

        assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0
    assert refusal.value.code == EXTRACTION_TOO_MANY_CANDIDATES


def test_one_calendar_event_cannot_fan_out_past_the_candidate_ceiling(tmp_path: Path) -> None:
    """A single VEVENT carries as many ATTENDEE lines as the source budget allows.

    Accounting only once the event is complete would let that one event expand unbounded,
    so the ceiling has to apply inside the attendee fan-out.
    """
    attendees = "".join(f"ATTENDEE;CN=Person {index}:mailto:p{index}@example.com\r\n" for index in range(200))
    source = tmp_path / "one-big-event.ics"
    source.write_text(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:e1\r\nDTSTART:20260722T090000Z\r\n"
        f"SUMMARY:All hands\r\n{attendees}END:VEVENT\r\nEND:VCALENDAR\r\n",
        encoding="utf-8",
    )

    with open_db(":memory:") as conn:
        with pytest.raises(ImportExtractionError) as refusal:
            _import_content(conn).execute(
                "ics",
                path=str(source),
                budget=ImportBudget(max_candidates=20),
            )

        assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0
    assert refusal.value.code == EXTRACTION_TOO_MANY_CANDIDATES


def test_one_email_message_cannot_fan_out_past_the_candidate_ceiling(tmp_path: Path) -> None:
    """One message's recipient headers are the same unbounded fan-out as a calendar event."""
    recipients = ", ".join(f"Person {index} <p{index}@example.com>" for index in range(200))
    source = tmp_path / "wide.eml"
    source.write_text(
        "From: Amina <amina@example.com>\n"
        f"To: {recipients}\n"
        "Date: Wed, 22 Jul 2026 09:00:00 +0000\n"
        "Message-ID: <wide@example.com>\n"
        "\nbody\n",
        encoding="utf-8",
    )

    with open_db(":memory:") as conn:
        with pytest.raises(ImportExtractionError) as refusal:
            _import_content(conn).execute(
                "email",
                path=str(source),
                budget=ImportBudget(max_candidates=20),
            )

        assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0
    assert refusal.value.code == EXTRACTION_TOO_MANY_CANDIDATES


def test_an_unbudgeted_extraction_still_produces_every_candidate(tmp_path: Path) -> None:
    source = tmp_path / "connections.csv"
    rows = "".join(f"A{i},B{i},u,a{i}@e.com,Acme,Eng,22 Jul 2026,n\n" for i in range(40))
    source.write_text(f"{_LINKEDIN_HEADERS}\n{rows}", encoding="utf-8")

    with open_db(":memory:") as conn:
        batch = _import_content(conn).execute("linkedin", path=str(source))

    assert batch.candidate_count == 120


def test_more_candidates_than_the_limit_are_refused_before_validation_or_staging() -> None:
    candidates = _person_candidates(MAX_CLI_STAGED_CANDIDATES + 1)

    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        stager = CandidateStager(SqlitePeopleRepository(conn), staging, _Clock())

        with pytest.raises(ImportPipelineError) as refusal:
            stager.execute("import/linkedin", candidates, budget=CLI_IMPORT_BUDGET)

        assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0
    assert refusal.value.code == TOO_MANY_CANDIDATES
    assert refusal.value.details == {"limit": MAX_CLI_STAGED_CANDIDATES}


def test_a_batch_exactly_at_the_candidate_limit_is_staged() -> None:
    candidates = _person_candidates(1) + _interaction_candidates(MAX_CLI_STAGED_CANDIDATES - 1)

    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        stager = CandidateStager(SqlitePeopleRepository(conn), staging, _Clock())

        result = stager.execute("import/linkedin", candidates, budget=CLI_IMPORT_BUDGET)

        assert result.candidate_count == MAX_CLI_STAGED_CANDIDATES
        assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == MAX_CLI_STAGED_CANDIDATES


def test_the_payload_budget_refuses_one_byte_past_the_ceiling_and_stages_nothing() -> None:
    """The boundary is `>`: the exact ceiling stages, one byte more does not."""
    candidates = _person_candidates(1) + _interaction_candidates(3)

    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        stager = CandidateStager(SqlitePeopleRepository(conn), staging, _Clock())
        measured = stager.execute("import/linkedin", candidates).batch_id
        exact = staging.measure_batch(measured, row_scan_limit=100).payload_bytes
        conn.execute("DELETE FROM import_staging")

        at_limit = stager.execute(
            "import/linkedin",
            candidates,
            budget=ImportBudget(max_staged_payload_bytes=exact),
        )
        assert at_limit.candidate_count == 4
        conn.execute("DELETE FROM import_staging")

        with pytest.raises(ImportPipelineError) as refusal:
            stager.execute(
                "import/linkedin",
                candidates,
                budget=ImportBudget(max_staged_payload_bytes=exact - 1),
            )

        assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0
    assert refusal.value.code == STAGED_PAYLOAD_TOO_LARGE


def test_a_staged_payload_over_sixty_four_mebibytes_is_refused_without_persisting_rows() -> None:
    one_mebibyte = "s" * (1024 * 1024)
    candidates = _person_candidates(1) + _interaction_candidates(65, summary=one_mebibyte)

    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        stager = CandidateStager(SqlitePeopleRepository(conn), staging, _Clock())

        with pytest.raises(ImportPipelineError) as refusal:
            stager.execute("import/linkedin", candidates, budget=CLI_IMPORT_BUDGET)

        assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0
    assert refusal.value.code == STAGED_PAYLOAD_TOO_LARGE
    assert refusal.value.details == {"limit": MAX_CLI_STAGED_PAYLOAD_BYTES}


def test_a_staged_payload_below_the_ceiling_is_accepted_at_that_scale() -> None:
    one_mebibyte = "s" * (1024 * 1024)
    candidates = _person_candidates(1) + _interaction_candidates(32, summary=one_mebibyte)

    with open_db(":memory:") as conn:
        staging = SqliteImportStagingStore(conn)
        stager = CandidateStager(SqlitePeopleRepository(conn), staging, _Clock())

        result = stager.execute("import/linkedin", candidates, budget=CLI_IMPORT_BUDGET)

        assert result.candidate_count == 33
        measured = staging.measure_batch(result.batch_id, row_scan_limit=100).payload_bytes
    assert 32 * 1024 * 1024 < measured <= MAX_CLI_STAGED_PAYLOAD_BYTES


def test_the_payload_budget_stops_building_rows_instead_of_measuring_afterwards() -> None:
    """Refusal must interrupt the work, not audit it once every row already exists."""
    one_mebibyte = "s" * (1024 * 1024)
    candidates = _person_candidates(1) + _interaction_candidates(64, summary=one_mebibyte)

    with open_db(":memory:") as conn:
        staging = _CountingStagingStore(SqliteImportStagingStore(conn))
        stager = _CountingStager(SqlitePeopleRepository(conn), staging, _Clock())

        with pytest.raises(ImportPipelineError):
            stager.execute(
                "import/linkedin",
                candidates,
                budget=ImportBudget(max_staged_payload_bytes=4 * 1024 * 1024),
            )

    assert stager.rows_built < 10
    assert staging.staged_batches == 0


def test_the_limits_do_not_narrow_a_caller_that_supplied_no_budget(tmp_path: Path) -> None:
    """The released MCP and application contract is unbounded, and stays unbounded."""
    source = tmp_path / "connections.csv"
    source.write_text(
        "\n".join([_LINKEDIN_HEADERS, "Amina,Haddad,u,amina@example.com,Acme,Engineer,22 Jul 2026,note"]),
        encoding="utf-8",
    )
    candidates = _person_candidates(MAX_CLI_STAGED_CANDIDATES + 1)

    with open_db(":memory:") as conn:
        stager = CandidateStager(SqlitePeopleRepository(conn), SqliteImportStagingStore(conn), _Clock())
        # The count that the CLI refuses is only validated when a budget asked for it, so an
        # over-CLI-limit batch fails here for its content, never for its size.
        with pytest.raises(ImportPipelineError) as refusal:
            stager.execute("import/agent:notes", [*candidates, {"type": "person"}])
        assert refusal.value.code == "invalid_candidates"

        batch = _import_content(conn).execute("linkedin", path=str(source))
        assert batch.candidate_count == 3


def test_init_keeps_its_unbounded_vcard_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Onboarding shares the CLI's rendering and selection, not the `pctx import` budgets."""
    db_file = tmp_path / "people.db"
    card = tmp_path / "contacts.vcf"
    card.write_text(
        "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Amina Haddad\r\nEMAIL:amina@example.com\r\nEND:VCARD\r\n",
        encoding="utf-8",
    )
    answers = iter(["Sam Self", "sam@example.com", str(card), "", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))

    assert cli.main(["--db", str(db_file), "init"]) == 0

    conn = open_db(db_file)
    try:
        assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 1
    finally:
        conn.close()


class _CountingStager(CandidateStager):
    """A stager that counts the rows it managed to build before it was stopped."""

    rows_built = 0

    def _row(self, *args: Any, **kwargs: Any) -> Any:
        self.rows_built += 1
        return super()._row(*args, **kwargs)


class _CountingStagingStore:
    """A staging store that records whether a refused batch reached durable storage."""

    def __init__(self, inner: SqliteImportStagingStore) -> None:
        self._inner = inner
        self.staged_batches = 0

    @property
    def unit_of_work(self) -> Any:
        return self._inner.unit_of_work

    def stage_batch(self, rows: list[Any]) -> None:
        self.staged_batches += 1
        self._inner.stage_batch(rows)

    def list_batch(self, batch_id: str) -> list[Any]:
        return self._inner.list_batch(batch_id)

    def mark_committed(self, candidate_ids: list[str]) -> None:
        self._inner.mark_committed(candidate_ids)


def _import_content(conn: sqlite3.Connection) -> ImportContent:
    people = SqlitePeopleRepository(conn)
    RememberPerson(people, people, SqliteAuditLog(conn), _Clock()).execute(
        RememberPersonInput(name="Sam Self", is_self=True)
    )
    return ImportContent(people, ImportExtractorRouter(), SqliteImportStagingStore(conn), _Clock())


def _person_candidates(count: int) -> list[dict[str, Any]]:
    return [
        {"type": "person", "ref": f"person-{index}", "name": f"Person {index}", "aliases": []} for index in range(count)
    ]


def _interaction_candidates(count: int, summary: str = "Correspondence") -> list[dict[str, Any]]:
    return [
        {
            "type": "interaction",
            "summary": summary,
            "participant_refs": ["person-0"],
            "date": "2026-07-22T09:00:00Z",
        }
        for _ in range(count)
    ]


def test_a_refused_batch_leaves_no_trace_in_the_staging_table(tmp_path: Path) -> None:
    db_file = tmp_path / "people.db"
    conn = open_db(db_file)
    try:
        stager = CandidateStager(SqlitePeopleRepository(conn), SqliteImportStagingStore(conn), _Clock())
        with pytest.raises(ImportPipelineError):
            stager.execute(
                "import/linkedin",
                _person_candidates(1) + _interaction_candidates(4),
                budget=ImportBudget(max_staged_payload_bytes=1),
            )
    finally:
        conn.close()

    assert _staging_rows(db_file) == []
    assert json.dumps([dict(row) for row in _staging_rows(db_file)]) == "[]"
