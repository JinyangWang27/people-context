"""Per-event iCalendar attendee extraction, time semantics, staging, and commit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from people_context.adapters.importers.email import ImportExtractionError
from people_context.adapters.importers.ics import IcsImportExtractor
from people_context.adapters.importers.router import ImportExtractorRouter
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteImportStagingStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.app.imports import (
    CommitImport,
    ImportContent,
    ImportPipelineError,
    ReviewImport,
)
from people_context.app.people import RememberPerson
from people_context.app.records import (
    RecordFact,
    RecordInteraction,
    SetAffiliation,
)
from people_context.domain.person import Alias, AliasKind, Person

_NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
_SUMMARY_SENTINEL = "ICS-SUMMARY-MUST-NEVER-BE-STORED-4f7a"
_DESCRIPTION_SENTINEL = "ICS-DESCRIPTION-MUST-NEVER-BE-STORED-9c02"
_LOCATION_SENTINEL = "ICS-LOCATION-MUST-NEVER-BE-STORED-1de8"


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _use_cases(conn):
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    audit = SqliteAuditLog(conn)
    staging = SqliteImportStagingStore(conn)
    remember = RememberPerson(people, people, audit, _Clock())
    interactions = RecordInteraction(people, records, audit, _Clock())
    affiliations = SetAffiliation(people, SqliteOrganizationStore(conn), records, audit, _Clock())
    facts = RecordFact(people, records, audit, _Clock())
    return (
        people,
        ImportContent(people, ImportExtractorRouter(), staging, _Clock()),
        ReviewImport(staging),
        CommitImport(people, staging, remember, interactions, affiliations, facts),
    )


def _feature_calendar() -> str:
    return "\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Example//Test//EN",
            "BEGIN:VEVENT",
            "UID:event-1@example.com",
            f"SUMMARY:{_SUMMARY_SENTINEL}",
            f"DESCRIPTION:{_DESCRIPTION_SENTINEL}",
            f"LOCATION:{_LOCATION_SENTINEL}",
            "DTSTART:20260304T090600Z",
            "ORGANIZER;CN=Owner Self:mailto:owner@example.com",
            "ATTENDEE;CN=Alice Example:mailto:alice@example.com",
            "ATTENDEE;CN=Bob Builder:mailto:bob@example.com",
            "ATTENDEE;CN=Owner Self:mailto:OWNER@example.com",
            "BEGIN:VALARM",
            "ACTION:EMAIL",
            "ATTENDEE;CN=Alarm Target:mailto:alarm@example.com",
            "END:VALARM",
            "END:VEVENT",
            "BEGIN:VEVENT",
            "UID:event-2@example.com",
            "DTSTART;TZID=America/New_York:20260701T120000",
            "ATTENDEE;CN=Alice A.:mailto:alice@example.com",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )


def test_ics_dedups_attendees_maps_times_and_omits_raw_free_text() -> None:
    extracted = IcsImportExtractor().extract(
        "ics",
        content=_feature_calendar(),
        path=None,
        self_addresses={"owner@example.com"},
    )

    people = [candidate for candidate in extracted.candidates if candidate["type"] == "person"]
    interactions = [candidate for candidate in extracted.candidates if candidate["type"] == "interaction"]

    assert [person["ref"] for person in people] == ["alice@example.com", "bob@example.com"]
    alice = next(person for person in people if person["ref"] == "alice@example.com")
    assert alice["name"] == "Alice Example"
    assert alice["aliases"] == [
        {"value": "alice@example.com", "kind": "handle"},
        {"value": "Alice A.", "kind": "other"},
    ]

    assert [interaction["summary"] for interaction in interactions] == ["Calendar event", "Calendar event"]
    assert interactions[0]["participant_refs"] == ["alice@example.com", "bob@example.com"]
    assert interactions[0]["date"] == datetime(2026, 3, 4, 9, 6, tzinfo=UTC)
    assert interactions[0]["message_id"] == "event-1@example.com"
    # 12:00 in America/New_York on 2026-07-01 is EDT (UTC-4) -> 16:00Z.
    assert interactions[1]["participant_refs"] == ["alice@example.com"]
    assert interactions[1]["date"] == datetime(2026, 7, 1, 16, 0, tzinfo=UTC)

    # The alarm attendee lives inside VALARM and must not become a person, and no free text leaks.
    assert "alarm@example.com" not in [person["ref"] for person in people]
    assert "owner@example.com" not in [person["ref"] for person in people]
    serialized = repr(extracted)
    for sentinel in (_SUMMARY_SENTINEL, _DESCRIPTION_SENTINEL, _LOCATION_SENTINEL):
        assert sentinel not in serialized


def test_ics_all_day_value_date_maps_to_midnight_utc() -> None:
    content = "\n".join(
        [
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT",
            "DTSTART;VALUE=DATE:20260304",
            "ATTENDEE:mailto:alice@example.com",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )

    extracted = IcsImportExtractor().extract("ics", content=content, path=None, self_addresses=set())

    interactions = [candidate for candidate in extracted.candidates if candidate["type"] == "interaction"]
    assert interactions[0]["date"] == datetime(2026, 3, 4, 0, 0, tzinfo=UTC)
    # A missing CN falls back to the email local part, never to raw free text.
    person = next(candidate for candidate in extracted.candidates if candidate["type"] == "person")
    assert person["name"] == "alice"


@pytest.mark.parametrize(
    ("dtstart", "expected_reason"),
    [
        ("DTSTART:20260304T090600", "floating_dtstart_unsupported"),
        ("DTSTART;TZID=Mars/Phobos:20260304T090600", "unknown_tzid"),
        ("DTSTART;TZID=America/New_York:20260308T023000", "nonexistent_dtstart"),
        ("DTSTART;TZID=America/New_York:20261101T013000", "ambiguous_dtstart"),
        ("DTSTART:20261332T090600Z", "invalid_dtstart"),
        ("DTSTART:not-a-timestamp", "malformed_dtstart"),
    ],
)
def test_ics_skips_unsupported_or_invalid_starts_with_stable_reasons(dtstart: str, expected_reason: str) -> None:
    content = "\n".join(
        [
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT",
            dtstart,
            "ATTENDEE:mailto:alice@example.com",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )

    extracted = IcsImportExtractor().extract("ics", content=content, path=None, self_addresses=set())

    assert extracted.candidates == []
    assert extracted.skipped_cards == [{"index": 1, "reason": expected_reason}]


def test_ics_skips_missing_dtstart_and_self_only_events_independently() -> None:
    content = "\n".join(
        [
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT",
            "ATTENDEE:mailto:alice@example.com",
            "END:VEVENT",
            "BEGIN:VEVENT",
            "DTSTART:20260304T090600Z",
            "ATTENDEE:mailto:owner@example.com",
            "END:VEVENT",
            "BEGIN:VEVENT",
            "DTSTART:20260305T090600Z",
            "ATTENDEE:mailto:bob@example.com",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )

    extracted = IcsImportExtractor().extract(
        "ics",
        content=content,
        path=None,
        self_addresses={"owner@example.com"},
    )

    assert extracted.skipped_cards == [
        {"index": 1, "reason": "missing_dtstart"},
        {"index": 2, "reason": "no_external_attendee"},
    ]
    people = [candidate for candidate in extracted.candidates if candidate["type"] == "person"]
    assert [person["ref"] for person in people] == ["bob@example.com"]


def test_ics_strips_mailto_query_fields_and_still_matches_self() -> None:
    query_sentinel = "ICS-MAILTO-QUERY-MUST-NOT-LEAK-3a19"
    content = "\n".join(
        [
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT",
            "DTSTART:20260304T090600Z",
            f"ATTENDEE;CN=Alice Example:mailto:alice@example.com?subject={query_sentinel}",
            f"ATTENDEE:mailto:owner@example.com?cc={query_sentinel}",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )

    extracted = IcsImportExtractor().extract(
        "ics",
        content=content,
        path=None,
        self_addresses={"owner@example.com"},
    )

    people = [candidate for candidate in extracted.candidates if candidate["type"] == "person"]
    assert [person["ref"] for person in people] == ["alice@example.com"]
    assert people[0]["aliases"][0] == {"value": "alice@example.com", "kind": "handle"}
    interactions = [candidate for candidate in extracted.candidates if candidate["type"] == "interaction"]
    assert interactions[0]["participant_refs"] == ["alice@example.com"]
    assert query_sentinel not in repr(extracted)


def test_ics_mismatched_component_end_marks_event_malformed() -> None:
    content = "\n".join(
        [
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT",
            "DTSTART:20260304T090600Z",
            "ATTENDEE:mailto:alice@example.com",
            "BEGIN:VALARM",
            "END:VEVENT",  # closes VEVENT while VALARM is still open
            "BEGIN:VEVENT",
            "DTSTART:20260305T090600Z",
            "ATTENDEE:mailto:bob@example.com",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )

    extracted = IcsImportExtractor().extract("ics", content=content, path=None, self_addresses=set())

    assert extracted.skipped_cards == [{"index": 1, "reason": "malformed_event"}]
    people = [candidate for candidate in extracted.candidates if candidate["type"] == "person"]
    assert [person["ref"] for person in people] == ["bob@example.com"]


def test_ics_one_malformed_event_does_not_block_neighbors() -> None:
    content = "\n".join(
        [
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT",
            "DTSTART:20260304T090600Z",
            "BROKEN PROPERTY LINE",
            "ATTENDEE:mailto:alice@example.com",
            "END:VEVENT",
            "BEGIN:VEVENT",
            "DTSTART:20260305T090600Z",
            "ATTENDEE:mailto:bob@example.com",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )

    extracted = IcsImportExtractor().extract("ics", content=content, path=None, self_addresses=set())

    assert extracted.skipped_cards == [{"index": 1, "reason": "malformed_event"}]
    people = [candidate for candidate in extracted.candidates if candidate["type"] == "person"]
    assert [person["ref"] for person in people] == ["bob@example.com"]


def test_ics_requires_exactly_one_content_source() -> None:
    extractor = IcsImportExtractor()

    with pytest.raises(ImportExtractionError):
        extractor.extract("ics", content=None, path=None, self_addresses=set())
    with pytest.raises(ImportExtractionError):
        extractor.extract("ics", content="cal", path="cal.ics", self_addresses=set())


def test_ics_rejects_foreign_source_type() -> None:
    with pytest.raises(ImportExtractionError) as error:
        IcsImportExtractor().extract("vcard", content="x", path=None, self_addresses=set())

    assert error.value.code == "invalid_source_type"


def test_ics_all_skipped_events_report_no_candidates_without_batch() -> None:
    conn = open_db(":memory:")
    _, import_content, _, _ = _use_cases(conn)
    content = "\n".join(
        [
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT",
            "DTSTART:20260304T090600",
            "ATTENDEE:mailto:a@example.com",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )

    with pytest.raises(ImportPipelineError) as exc_info:
        import_content.execute("ics", content=content)

    assert exc_info.value.code == "no_candidates"
    assert exc_info.value.details["skipped_cards"] == [{"index": 1, "reason": "floating_dtstart_unsupported"}]
    assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


def test_ics_stage_review_commit_persists_interaction_and_people() -> None:
    conn = open_db(":memory:")
    people, import_content, review, commit = _use_cases(conn)
    people.save_person(
        Person(
            canonical_name="Owner Self",
            is_self=True,
            aliases=[Alias(value="owner@example.com", kind=AliasKind.HANDLE)],
        )
    )

    batch = import_content.execute("ics", content=_feature_calendar())
    rows = review.execute(batch.batch_id).candidates

    # Two people plus two interactions.
    assert batch.candidate_count == 4
    assert batch.skipped_cards == []
    person_rows = [row for row in rows if row.candidate["type"] == "person"]
    interaction_rows = [row for row in rows if row.candidate["type"] == "interaction"]
    assert {row.candidate["name"] for row in person_rows} == {"Alice Example", "Bob Builder"}
    assert all(row.candidate["summary"] == "Calendar event" for row in interaction_rows)

    accepted = commit.execute(batch.batch_id, [row.id for row in rows])
    assert set(accepted.committed_ids) == {row.id for row in rows}
    assert accepted.unresolved_ids == []
    # Seeded self plus the two imported attendees; the self attendee never created a duplicate.
    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM persons WHERE is_self = 1").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0] == 2
    summaries = {row[0] for row in conn.execute("SELECT summary FROM interactions").fetchall()}
    assert summaries == {"Calendar event"}

    ordinary = _ordinary_text(conn)
    for sentinel in (_SUMMARY_SENTINEL, _DESCRIPTION_SENTINEL, _LOCATION_SENTINEL):
        assert sentinel not in ordinary


def _ordinary_text(conn) -> str:
    values: list[str] = []
    for table in (
        "persons",
        "aliases",
        "interactions",
        "import_staging",
        "audit_log",
    ):
        for row in conn.execute(f"SELECT * FROM {table}").fetchall():
            values.extend(str(value) for value in row if isinstance(value, str))
    return "\n".join(values)
