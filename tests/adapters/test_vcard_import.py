"""Per-card vCard extraction, staging, dependency, and commit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from people_context.adapters.email_import import ImportExtractionError
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteImportStagingStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.adapters.vcard_import import ImportExtractorRouter, VCardImportExtractor
from people_context.app import (
    CommitImport,
    ImportContent,
    ImportPipelineError,
    RecordFact,
    RecordInteraction,
    RememberPerson,
    ReviewImport,
    SetAffiliation,
)
from people_context.domain.person import Alias, AliasKind, Person

_NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
_NOTE_SENTINEL = "VCARD-NOTE-MUST-NEVER-BE-STORED-6e91"


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


def _feature_cards() -> str:
    return "\n".join(
        [
            "BEGIN:VCARD",
            "VERSION:3.0",
            "FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:Alice=20",
            " Example",
            "N:Doe;Alice;;Dr.;PhD",
            r"NICKNAME:Ally,A\,Team",
            "item1.EMAIL;TYPE=work:alice@example.com",
            "EMAIL;TYPE=home:ally@example.com",
            r"ORG:Acme\; Holdings;Research",
            "TITLE:Senior SQL Engineer",
            "BDAY:1990-01-02",
            f"NOTE:{_NOTE_SENTINEL}",
            f" {_NOTE_SENTINEL}",
            "PHOTO;ENCODING=b:ignored-photo",
            "END:VCARD",
            "BEGIN:VCARD",
            "VERSION:4.0",
            "FN:王小明",
            "EMAIL;TYPE=internet:xiaoming@example.cn",
            "END:VCARD",
        ]
    )


def test_vcard_feature_fixture_stages_safe_fields_and_commits_dependencies() -> None:
    conn = open_db(":memory:")
    _, import_content, review, commit = _use_cases(conn)

    batch = import_content.execute("vcard", content=_feature_cards())
    rows = review.execute(batch.batch_id).candidates

    assert batch.candidate_count == 4
    assert batch.skipped_cards == []
    people = [row for row in rows if row.candidate["type"] == "person"]
    alice = next(row for row in people if row.candidate["name"] == "Alice Example")
    assert alice.candidate["aliases"] == [
        {"value": "Dr. Alice Doe PhD", "kind": "other"},
        {"value": "Ally", "kind": "nickname"},
        {"value": "A,Team", "kind": "nickname"},
        {"value": "alice@example.com", "kind": "handle"},
        {"value": "ally@example.com", "kind": "handle"},
    ]
    affiliation = next(row for row in rows if row.candidate["type"] == "affiliation")
    fact = next(row for row in rows if row.candidate["type"] == "fact")
    assert affiliation.candidate["org"] == "Acme; Holdings"
    assert affiliation.candidate["role"] == "Senior SQL Engineer"
    assert fact.candidate["predicate"] == "birthday"
    assert fact.candidate["value"] == "1990-01-02"
    assert _NOTE_SENTINEL not in _ordinary_text(conn)

    unresolved = commit.execute(batch.batch_id, [affiliation.id, fact.id])
    assert set(unresolved.unresolved_ids) == {affiliation.id, fact.id}
    accepted = commit.execute(batch.batch_id, [alice.id, affiliation.id, fact.id])
    assert set(accepted.committed_ids) == {alice.id, affiliation.id, fact.id}
    assert conn.execute("SELECT COUNT(*) FROM affiliations").fetchone()[0] == 1
    assert conn.execute("SELECT value FROM facts WHERE predicate = 'birthday'").fetchone()[0] == "1990-01-02"
    repeated = commit.execute(batch.batch_id, [alice.id, affiliation.id, fact.id])
    assert set(repeated.skipped_ids) == {alice.id, affiliation.id, fact.id}
    assert _NOTE_SENTINEL not in _ordinary_text(conn)


def test_vcard_matches_email_before_name_and_dependents_commit_without_person_acceptance() -> None:
    conn = open_db(":memory:")
    people, import_content, review, commit = _use_cases(conn)
    existing = Person(
        canonical_name="Alice Existing",
        aliases=[Alias(value="alice@example.com", kind=AliasKind.HANDLE)],
    )
    people.save_person(existing)

    # Use only the first feature card while keeping its BEGIN marker.
    first_card = _feature_cards().split("END:VCARD", maxsplit=1)[0] + "END:VCARD\n"
    batch = import_content.execute("vcard", content=first_card)
    rows = review.execute(batch.batch_id).candidates
    person = next(row for row in rows if row.candidate["type"] == "person")
    dependents = [row for row in rows if row.candidate["type"] in {"affiliation", "fact"}]

    assert person.candidate["matched_person_id"] == existing.id
    result = commit.execute(batch.batch_id, [row.id for row in dependents])
    assert set(result.committed_ids) == {row.id for row in dependents}
    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1


def test_mixed_invalid_cards_report_stable_one_based_reasons_and_keep_valid_cards() -> None:
    content = "\n".join(
        [
            "BEGIN:VCARD",
            "VERSION:2.1",
            "FN:Old Card",
            "END:VCARD",
            "BEGIN:VCARD",
            "VERSION:4.0",
            "EMAIL:missing@example.com",
            "END:VCARD",
            "BEGIN:VCARD",
            "VERSION:4.0",
            "BROKEN PROPERTY",
            "END:VCARD",
            "BEGIN:VCARD",
            "VERSION:4.0",
            "FN:Valid Card",
            "END:VCARD",
        ]
    )
    extracted = VCardImportExtractor().extract("vcard", content=content, path=None, self_addresses=set())

    assert extracted.skipped_cards == [
        {"index": 1, "reason": "unsupported_version"},
        {"index": 2, "reason": "missing_fn"},
        {"index": 3, "reason": "malformed_card"},
    ]
    assert [candidate["name"] for candidate in extracted.candidates if candidate["type"] == "person"] == [
        "Valid Card"
    ]


def test_all_invalid_cards_return_no_candidates_without_creating_batch() -> None:
    conn = open_db(":memory:")
    _, import_content, _, _ = _use_cases(conn)
    content = "BEGIN:VCARD\nVERSION:2.1\nFN:Old\nEND:VCARD\n"

    with pytest.raises(ImportPipelineError) as exc_info:
        import_content.execute("vcard", content=content)

    assert exc_info.value.code == "no_candidates"
    assert exc_info.value.details["skipped_cards"] == [{"index": 1, "reason": "unsupported_version"}]
    assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


def test_large_vcard_batch_skips_one_bad_card_without_blocking_others() -> None:
    cards = [
        (
            "BEGIN:VCARD\nVERSION:4.0\nBROKEN\nEND:VCARD"
            if index == 150
            else f"BEGIN:VCARD\nVERSION:4.0\nFN:Person {index}\nEND:VCARD"
        )
        for index in range(1, 301)
    ]
    conn = open_db(":memory:")
    _, import_content, _, _ = _use_cases(conn)

    result = import_content.execute("vcard", content="\n".join(cards))

    assert result.candidate_count == 299
    assert result.skipped_cards == [{"index": 150, "reason": "malformed_card"}]


def test_vcard_requires_exactly_one_content_source() -> None:
    extractor = VCardImportExtractor()

    with pytest.raises(ImportExtractionError):
        extractor.extract("vcard", content=None, path=None, self_addresses=set())
    with pytest.raises(ImportExtractionError):
        extractor.extract("vcard", content="card", path="card.vcf", self_addresses=set())


def _ordinary_text(conn) -> str:
    values: list[str] = []
    for table in (
        "persons",
        "aliases",
        "organizations",
        "affiliations",
        "facts",
        "import_staging",
        "audit_log",
    ):
        for row in conn.execute(f"SELECT * FROM {table}").fetchall():
            values.extend(str(value) for value in row if isinstance(value, str))
    return "\n".join(values)
