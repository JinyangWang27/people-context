"""Outlook contacts CSV extraction, staging, and commit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from people_context.adapters.importers.email import ImportExtractionError
from people_context.adapters.importers.outlook import OutlookImportExtractor
from people_context.adapters.importers.router import ImportExtractorRouter
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteImportStagingStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.app.imports import CommitImport, ImportContent, ImportPipelineError, ReviewImport
from people_context.app.people import RememberPerson
from people_context.app.records import RecordFact, RecordInteraction, SetAffiliation

_HEADERS = (
    "First Name,Middle Name,Last Name,Nickname,E-mail Address,Company,Department,Job Title,"
    "Web Page,Birthday,Notes"
)
_NOTE_SENTINEL = "OUTLOOK-NOTE-MUST-NOT-LEAK-3f19"
_URL_SENTINEL = "OUTLOOK-URL-MUST-NOT-LEAK-8a02"
_NOW = datetime(2026, 8, 10, tzinfo=UTC)


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


def test_outlook_accepts_bom_header_superset_and_omits_raw_columns() -> None:
    content = "\ufeff" + _csv(
        f"Alice,Q,Example,Ali,ALICE@EXAMPLE.COM,Acme,Research,Engineer,"
        f"https://example.test/{_URL_SENTINEL},1985-01-23,{_NOTE_SENTINEL}",
        "Bob,,Builder,,,Build Co,,Foreman,,,ordinary note",
    )

    extracted = OutlookImportExtractor().extract("outlook", content=content, path=None, self_addresses=set())

    people = [candidate for candidate in extracted.candidates if candidate["type"] == "person"]
    assert [person["ref"] for person in people] == ["outlook-person-1", "outlook-person-2"]
    assert people[0]["name"] == "Alice Q Example"
    assert people[0]["aliases"] == [{"value": "alice@example.com", "kind": "handle"}]
    assert people[1]["aliases"] == []
    affiliations = [candidate for candidate in extracted.candidates if candidate["type"] == "affiliation"]
    assert [(item["org"], item["role"]) for item in affiliations] == [("Acme", "Engineer"), ("Build Co", "Foreman")]
    facts = [candidate for candidate in extracted.candidates if candidate["type"] == "fact"]
    assert facts == [
        {"type": "fact", "person_ref": "outlook-person-1", "predicate": "birthday", "value": "1985-01-23"}
    ]
    assert extracted.skipped_cards == []
    assert _NOTE_SENTINEL not in repr(extracted)
    assert _URL_SENTINEL not in repr(extracted)


def test_outlook_rows_fail_independently_without_blocking_neighbors() -> None:
    content = _csv(
        ",,,,,Acme,,Engineer,,,note",
        "Bad,,Email,,not-an-address,Acme,,Engineer,,,note",
        "Carol,,Example,,carol@example.com,,,,,,note",
    )

    extracted = OutlookImportExtractor().extract("outlook", content=content, path=None, self_addresses=set())

    people = [candidate for candidate in extracted.candidates if candidate["type"] == "person"]
    assert [person["name"] for person in people] == ["Carol Example"]
    assert extracted.skipped_cards == [
        {"index": 1, "reason": "missing_name"},
        {"index": 2, "reason": "invalid_email"},
    ]


def test_outlook_accepts_only_year_first_birthdays_and_keeps_the_contact() -> None:
    content = _csv(
        "Alice,,Example,,alice@example.com,,,,,1985/01/23,note",
        "Bob,,Builder,,bob@example.com,,,,,1/23/1985,note",
        "Carol,,Example,,carol@example.com,,,,,1985-02-30,note",
    )

    extracted = OutlookImportExtractor().extract("outlook", content=content, path=None, self_addresses=set())

    people = [candidate for candidate in extracted.candidates if candidate["type"] == "person"]
    assert [person["name"] for person in people] == ["Alice Example", "Bob Builder", "Carol Example"]
    facts = [candidate for candidate in extracted.candidates if candidate["type"] == "fact"]
    assert [(fact["person_ref"], fact["value"]) for fact in facts] == [("outlook-person-1", "1985-01-23")]
    assert extracted.skipped_cards == [
        {"index": 2, "reason": "invalid_birthday"},
        {"index": 3, "reason": "invalid_birthday"},
    ]


def test_outlook_coalesces_duplicate_emails_and_keeps_nameless_email_rows_distinct() -> None:
    content = _csv(
        "Alice,,Example,,alice@example.com,Acme,,Engineer,,1985-01-23,note",
        "Alice,,Example-Smith,,ALICE@example.com,Acme,,Engineer,,1985-01-23,note",
        "Sam,,Smith,,,,,,,,note",
        "Sam,,Smith,,,,,,,,note",
    )

    extracted = OutlookImportExtractor().extract("outlook", content=content, path=None, self_addresses=set())

    people = [candidate for candidate in extracted.candidates if candidate["type"] == "person"]
    assert [person["ref"] for person in people] == [
        "outlook-person-1",
        "outlook-person-2",
        "outlook-person-3",
    ]
    assert people[0]["aliases"] == [
        {"value": "alice@example.com", "kind": "handle"},
        {"value": "Alice Example-Smith", "kind": "other"},
    ]
    affiliations = [candidate for candidate in extracted.candidates if candidate["type"] == "affiliation"]
    facts = [candidate for candidate in extracted.candidates if candidate["type"] == "fact"]
    assert len(affiliations) == 1
    assert len(facts) == 1


def test_outlook_omits_rows_matching_a_stored_self_handle() -> None:
    content = _csv(
        "Me,,Myself,,me@example.com,,,,,,note",
        "Alice,,Example,,alice@example.com,,,,,,note",
    )

    extracted = OutlookImportExtractor().extract(
        "outlook",
        content=content,
        path=None,
        self_addresses={"ME@example.com"},
    )

    people = [candidate for candidate in extracted.candidates if candidate["type"] == "person"]
    assert [person["name"] for person in people] == ["Alice Example"]
    assert extracted.skipped_cards == []


def test_outlook_rejects_missing_canonical_headers_and_wrong_source() -> None:
    with pytest.raises(ImportExtractionError) as missing_headers:
        OutlookImportExtractor().extract(
            "outlook",
            content="First Name,Last Name\nAlice,Example",
            path=None,
            self_addresses=set(),
        )
    assert missing_headers.value.code == "invalid_headers"

    with pytest.raises(ImportExtractionError) as wrong_source:
        OutlookImportExtractor().extract("linkedin", content=_csv(), path=None, self_addresses=set())
    assert wrong_source.value.code == "invalid_source_type"

    with pytest.raises(ImportExtractionError) as ambiguous_source:
        OutlookImportExtractor().extract("outlook", content=_csv(), path="file.csv", self_addresses=set())
    assert ambiguous_source.value.code == "invalid_source"


def test_outlook_reads_a_utf8_sig_file_path(tmp_path) -> None:
    csv_path = tmp_path / "contacts.csv"
    csv_path.write_text(
        _csv("Alice,,Example,,alice@example.com,,,,,,note"),
        encoding="utf-8-sig",
    )

    extracted = ImportExtractorRouter().extract(
        "outlook",
        content=None,
        path=str(csv_path),
        self_addresses=set(),
    )

    assert [candidate["name"] for candidate in extracted.candidates if candidate["type"] == "person"] == [
        "Alice Example"
    ]


def test_outlook_import_stages_reviews_and_commits_with_source_provenance() -> None:
    with open_db(":memory:") as conn:
        import_content, review_import, commit_import = _use_cases(conn)
        content = _csv(f"Alice,,Example,,alice@example.com,Acme,,Engineer,,1985-01-23,{_NOTE_SENTINEL}")

        batch = import_content.execute("outlook", content=content)
        reviewed = review_import.execute(batch.batch_id)
        committed = commit_import.execute(batch.batch_id, [row.id for row in reviewed.candidates])

        assert batch.candidate_count == 3
        assert {row.source for row in reviewed.candidates} == {"import/outlook"}
        assert len(committed.committed_ids) == 3
        assert committed.unresolved_ids == []
        assert _NOTE_SENTINEL not in repr(reviewed)

        people = SqlitePeopleRepository(conn).find_by_normalized_name("alice example")
        assert [person.canonical_name for person in people] == ["Alice Example"]


def test_outlook_batch_without_usable_rows_reports_no_candidates() -> None:
    with open_db(":memory:") as conn:
        import_content, _, _ = _use_cases(conn)

        with pytest.raises(ImportPipelineError) as error:
            import_content.execute("outlook", content=_csv(",,,,,,,,,,note"))

        assert error.value.code == "no_candidates"
        assert error.value.details["skipped_cards"] == [{"index": 1, "reason": "missing_name"}]
