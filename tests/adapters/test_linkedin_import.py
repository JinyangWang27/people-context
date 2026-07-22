"""LinkedIn Connections CSV extraction, staging, and commit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from people_context.adapters.email_import import ImportExtractionError
from people_context.adapters.import_router import ImportExtractorRouter
from people_context.adapters.linkedin_import import LinkedInImportExtractor
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteImportStagingStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.app import (
    CommitImport,
    ImportContent,
    RecordFact,
    RecordInteraction,
    RememberPerson,
    ReviewImport,
    SetAffiliation,
)

_HEADERS = "First Name,Last Name,URL,Email Address,Company,Position,Connected On,Notes"
_URL_SENTINEL = "LINKEDIN-URL-MUST-NOT-LEAK-41d7"
_NOTE_SENTINEL = "LINKEDIN-NOTE-MUST-NOT-LEAK-92ac"
_NOW = datetime(2026, 7, 22, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _csv(*rows: str) -> str:
    return "\n".join([_HEADERS, *rows])


def _use_cases(conn):
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    audit = SqliteAuditLog(conn)
    staging = SqliteImportStagingStore(conn)
    return (
        ImportContent(people, ImportExtractorRouter(), staging, _Clock()),
        ReviewImport(staging),
        CommitImport(
            people,
            staging,
            RememberPerson(people, people, audit, _Clock()),
            RecordInteraction(people, records, audit, _Clock()),
            SetAffiliation(people, SqliteOrganizationStore(conn), records, audit, _Clock()),
            RecordFact(people, records, audit, _Clock()),
        ),
    )


def test_linkedin_accepts_bom_header_superset_dates_and_omits_raw_fields() -> None:
    content = "\ufeff" + _csv(
        f"Alice,Example,https://example.test/{_URL_SENTINEL},ALICE@EXAMPLE.COM,Acme,Engineer,"
        f"04 Mar 2026,{_NOTE_SENTINEL}",
        "Bob,Builder,https://example.test/bob,,Build Co,Foreman,2026-04-05,ordinary note",
    )

    extracted = LinkedInImportExtractor().extract(
        "linkedin", content=content, path=None, self_addresses=set()
    )

    people = [candidate for candidate in extracted.candidates if candidate["type"] == "person"]
    assert [person["ref"] for person in people] == ["linkedin-person-1", "linkedin-person-2"]
    assert people[0]["aliases"] == [{"value": "alice@example.com", "kind": "handle"}]
    assert people[1]["aliases"] == []
    facts = [candidate for candidate in extracted.candidates if candidate["type"] == "fact"]
    assert [fact["value"] for fact in facts] == ["2026-03-04", "2026-04-05"]
    assert _URL_SENTINEL not in repr(extracted)
    assert _NOTE_SENTINEL not in repr(extracted)


def test_linkedin_discards_real_export_notes_preamble_before_canonical_header() -> None:
    preamble_sentinel = "LINKEDIN-PREAMBLE-MUST-NOT-LEAK-5e21"
    content = "\n".join(
        [
            "\ufeffNotes:",
            f'"{preamble_sentinel}, including comma-separated notice text"',
            "",
            _HEADERS,
            "Alice,Example,url,alice@example.com,Acme,Engineer,04 Mar 2026,note",
            "Bad,Date,url,bad@example.com,Acme,Engineer,not-a-date,note",
        ]
    )

    extracted = LinkedInImportExtractor().extract(
        "linkedin", content=content, path=None, self_addresses=set()
    )

    assert [candidate["name"] for candidate in extracted.candidates if candidate["type"] == "person"] == [
        "Alice Example"
    ]
    assert extracted.skipped_cards == [{"index": 2, "reason": "invalid_connected_on"}]
    assert preamble_sentinel not in repr(extracted)


def test_linkedin_coalesces_email_names_but_keeps_no_email_rows_distinct_and_dedupes_dependents() -> None:
    content = _csv(
        "Alice,Example,url,alice@example.com,Acme,Engineer,04 Mar 2026,note",
        "Alicia,Example,url,ALICE@example.com,Acme,Engineer,2026-03-04,note",
        "Pat,Lee,url,,Studio,Designer,,note",
        "Pat,Lee,url,,Studio,Designer,,note",
    )

    extracted = LinkedInImportExtractor().extract(
        "linkedin", content=content, path=None, self_addresses=set()
    )

    people = [candidate for candidate in extracted.candidates if candidate["type"] == "person"]
    assert [person["ref"] for person in people] == [
        "linkedin-person-1",
        "linkedin-person-2",
        "linkedin-person-3",
    ]
    assert people[0]["aliases"] == [
        {"value": "alice@example.com", "kind": "handle"},
        {"value": "Alicia Example", "kind": "other"},
    ]
    affiliations = [candidate for candidate in extracted.candidates if candidate["type"] == "affiliation"]
    facts = [candidate for candidate in extracted.candidates if candidate["type"] == "fact"]
    assert [candidate["person_ref"] for candidate in affiliations] == [
        "linkedin-person-1",
        "linkedin-person-2",
        "linkedin-person-3",
    ]
    assert facts == [
        {
            "type": "fact",
            "person_ref": "linkedin-person-1",
            "predicate": "linkedin_connected_on",
            "value": "2026-03-04",
        }
    ]


def test_linkedin_skips_invalid_rows_independently_with_stable_safe_reasons() -> None:
    content = _csv(
        f',,https://example.test/{_URL_SENTINEL},,Secret,Text,04 Mar 2026,{_NOTE_SENTINEL}',
        "Bad,Email,url,not-an-email,Secret,Text,04 Mar 2026,note",
        "Bad,Date,url,bad-date@example.com,Secret,Text,March 4th 2026,note",
        "Good,Neighbor,url,good@example.com,Acme,Engineer,,note",
    )

    extracted = LinkedInImportExtractor().extract(
        "linkedin", content=content, path=None, self_addresses=set()
    )

    assert extracted.skipped_cards == [
        {"index": 1, "reason": "missing_name"},
        {"index": 2, "reason": "invalid_email"},
        {"index": 3, "reason": "invalid_connected_on"},
    ]
    assert [candidate["name"] for candidate in extracted.candidates if candidate["type"] == "person"] == [
        "Good Neighbor"
    ]
    assert _URL_SENTINEL not in repr(extracted)
    assert _NOTE_SENTINEL not in repr(extracted)


def test_linkedin_requires_canonical_headers_and_exactly_one_source(tmp_path) -> None:
    extractor = LinkedInImportExtractor()
    with pytest.raises(ImportExtractionError) as error:
        extractor.extract("linkedin", content="First,Last\nAlice,Example", path=None, self_addresses=set())
    assert error.value.code == "invalid_headers"

    valid = _csv("Alice,Example,url,,,,,note")
    path = tmp_path / "connections.csv"
    path.write_text(valid, encoding="utf-8")
    assert extractor.extract("linkedin", content=None, path=str(path), self_addresses=set()).candidates
    with pytest.raises(ImportExtractionError):
        extractor.extract("linkedin", content=None, path=None, self_addresses=set())
    with pytest.raises(ImportExtractionError):
        extractor.extract("linkedin", content=valid, path=str(path), self_addresses=set())


def test_linkedin_stage_review_commit_preserves_source_and_dependents() -> None:
    conn = open_db(":memory:")
    importer, reviewer, committer = _use_cases(conn)
    batch = importer.execute(
        "linkedin",
        content=_csv("Alice,Example,url,alice@example.com,Acme,Engineer,04 Mar 2026,note"),
    )
    rows = reviewer.execute(batch.batch_id).candidates

    assert batch.candidate_count == 3
    assert {row.source for row in rows} == {"import/linkedin"}
    assert {row.candidate["type"] for row in rows} == {"person", "affiliation", "fact"}
    result = committer.execute(batch.batch_id, [row.id for row in rows])
    assert set(result.committed_ids) == {row.id for row in rows}
    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM affiliations").fetchone()[0] == 1
    assert tuple(conn.execute("SELECT predicate, value FROM facts").fetchone()) == (
        "linkedin_connected_on",
        "2026-03-04",
    )
