"""One fictional CV captured end to end (M22.2).

M22.2 introduced no mechanism. What it added is the judgement that decides which of the
already-shipped pieces a claim belongs in, and these exercise that judgement against the real
stores: an exactly dated current role becomes an affiliation, a year-only degree stays a fact
because an affiliation would have to invent two days or open a period that reads as a current
job, sensitive background goes where disclosure can be enforced, and an ambiguous subject
commits nothing at all.

The last check is the one the milestone spec names directly: the document itself never enters
People Context, so a distinctive line of the fictional CV must appear in no staged row, no
committed record, no receipt, and no inspection output.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteContextReader,
    SqliteImportStagingStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    open_db,
)
from people_context.adapters.sqlite.source_store import SqliteImportSourceStore
from people_context.adapters.sqlite.trait_evidence import SqliteTraitEvidenceStore
from people_context.app.context import GetPersonContext
from people_context.app.imports import (
    CandidateStager,
    CommitImport,
    ListImportSources,
    ReviewImport,
    ShowImportSource,
    StageCandidates,
)
from people_context.app.people import RememberPerson
from people_context.app.people.remember import AmbiguousPersonError
from people_context.app.records import (
    RecordFact,
    RecordInteraction,
    RecordObservation,
    RecordTrait,
    SetAffiliation,
)
from people_context.app.relationships import SetRelationship
from people_context.domain.person import Alias, AliasKind, Person

_NOW = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)

#: A line of the fictional CV, deliberately distinctive. People Context is never given the
#: document, so this string must not survive anywhere in the store or in anything it renders.
_RAW_CV_LINE = "RAW-CV-PARAGRAPH-Nadia-spent-her-twenties-sailing-and-would-rather-not-discuss-it"

_ATTRIBUTION = "Nadia Okonkwo (CV)"


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _Harness:
    """The whole stage → review → commit lifecycle over one in-memory database.

    Batches are source-tracked, because a CV capture is exactly the case an M18 receipt exists
    for: the agent read an artifact People Context cannot see, and the receipt records that the
    processing happened without claiming anything about what the artifact said.
    """

    def __init__(self) -> None:
        self.conn = open_db(":memory:")
        self._digests = 0
        people = SqlitePeopleRepository(self.conn)
        records = SqliteRecordStore(self.conn)
        audit = SqliteAuditLog(self.conn)
        staging = SqliteImportStagingStore(self.conn)
        sources = SqliteImportSourceStore(self.conn)
        evidence = SqliteTraitEvidenceStore(self.conn)
        self.people = people
        self.context = GetPersonContext(people, SqliteContextReader(self.conn), _Clock())
        self.list_sources = ListImportSources(sources)
        self.show_source = ShowImportSource(sources)
        self._stage = StageCandidates(CandidateStager(people, staging, _Clock(), sources, audit))
        self.review = ReviewImport(staging)
        self.commit = CommitImport(
            people,
            staging,
            RememberPerson(people, people, audit, _Clock()),
            RecordInteraction(people, records, audit, _Clock()),
            SetAffiliation(people, SqliteOrganizationStore(self.conn), records, audit, _Clock()),
            RecordFact(people, records, audit, _Clock()),
            RecordObservation(people, records, audit, _Clock()),
            RecordTrait(people, records, audit, _Clock(), evidence),
            SetRelationship(
                people,
                SqliteRelationshipStore(self.conn),
                audit,
                _Clock(),
                SqliteRelationshipVocabularyStore(self.conn),
            ),
            sources,
            audit,
            _Clock(),
            evidence,
        )

    def stage(self, candidates: list[dict[str, Any]], *, strict: bool = True) -> Any:
        """Stage one tracked batch the way `pctx import stage-candidates` does.

        The CLI boundary always demands the ambiguity-preserving matcher; the digest stands in
        for one the agent computed over the bytes it actually read.
        """
        self._digests += 1
        return self._stage.execute(
            "nadia-cv",
            candidates,
            strict_identity=strict,
            source_kind="cv",
            content_digest=f"{self._digests:064x}",
        )

    def staged(self, batch_id: str) -> dict[str, Any]:
        return {row.candidate["type"]: row for row in self.review.execute(batch_id).candidates}

    def accept_all(self, batch_id: str) -> Any:
        return self.commit.execute(batch_id, [row.id for row in self.review.execute(batch_id).candidates])

    def dump(self) -> str:
        """Every byte of durable state, for asserting what is absent from all of it."""
        return "\n".join(self.conn.iterdump())


def _person(name: str = "Nadia Okonkwo", *handles: str) -> dict[str, Any]:
    return {
        "type": "person",
        "ref": "nadia",
        "name": name,
        "aliases": [{"value": handle, "kind": "handle"} for handle in handles],
    }


def _cv_batch() -> list[dict[str, Any]]:
    """The distillation of one fictional CV: what the agent stages, never what it read."""
    return [
        _person("Nadia Okonkwo", "nadia.okonkwo@example.com"),
        # Stated as current with an exact start date, so it is representable as an affiliation.
        {
            "type": "affiliation",
            "person_ref": "nadia",
            "org": "Northbridge Analytics",
            "role": "Senior Data Engineer",
            "valid_from": "2023-04-03",
            "stated_by": _ATTRIBUTION,
            "confidence": 0.7,
        },
        # A concurrent role. Two at once is ordinary, not a contradiction to resolve.
        {
            "type": "affiliation",
            "person_ref": "nadia",
            "org": "Harbour Data Trust",
            "role": "Board Member",
            "valid_from": "2025-01-15",
            "stated_by": _ATTRIBUTION,
            "confidence": 0.7,
        },
        # Year-only study: the uncertainty lives in the value, not in guessed date fields.
        {
            "type": "fact",
            "person_ref": "nadia",
            "predicate": "education",
            "value": "CV reports study at Northbridge College, 2017-2019; months and days unknown",
            "stated_by": _ATTRIBUTION,
            "confidence": 0.6,
        },
        # A self-description stays an attributed claim about what the CV says.
        {
            "type": "fact",
            "person_ref": "nadia",
            "predicate": "self_description",
            "value": "Describes herself as analytical in her CV",
            "stated_by": _ATTRIBUTION,
            "confidence": 0.5,
        },
        # Background that needs protecting goes where a read can withhold it.
        {
            "type": "fact",
            "person_ref": "nadia",
            "predicate": "background",
            "value": "CV mentions a two-year medical career break",
            "sensitivity": "sensitive",
            "stated_by": _ATTRIBUTION,
            "confidence": 0.5,
        },
    ]


def test_a_readable_cv_commits_attributed_claims_with_only_the_dates_it_supports() -> None:
    harness = _Harness()

    batch = harness.stage(_cv_batch())
    result = harness.accept_all(batch.batch_id)

    assert result.unresolved_ids == []
    assert len(result.committed_ids) == 6

    affiliations = [
        dict(row)
        for row in harness.conn.execute(
            "SELECT org_id, role, valid_from, valid_to, provenance_stated_by FROM affiliations ORDER BY role"
        )
    ]
    assert [row["role"] for row in affiliations] == ["Board Member", "Senior Data Engineer"]
    # Concurrent roles both stand, each attributed to the document that asserted it.
    assert {row["provenance_stated_by"] for row in affiliations} == {_ATTRIBUTION}
    assert [row["valid_from"] for row in affiliations] == ["2025-01-15", "2023-04-03"]

    facts = {
        row["predicate"]: dict(row)
        for row in harness.conn.execute(
            "SELECT predicate, value, valid_from, valid_to, sensitivity, provenance_stated_by FROM facts"
        )
    }
    assert set(facts) == {"education", "self_description", "background"}
    assert all(row["provenance_stated_by"] == _ATTRIBUTION for row in facts.values())


def test_a_year_only_role_never_becomes_an_apparently_current_affiliation() -> None:
    """The failure this milestone exists to prevent: two invented days, or an open period."""
    harness = _Harness()

    harness.accept_all(harness.stage(_cv_batch()).batch_id)

    orgs = [
        row["name"]
        for row in harness.conn.execute(
            "SELECT o.name AS name FROM affiliations a JOIN organizations o ON o.id = a.org_id"
        )
    ]
    assert "Northbridge College" not in orgs, "a year-only period must not open an affiliation"

    education = harness.conn.execute(
        "SELECT value, valid_from, valid_to FROM facts WHERE predicate = 'education'"
    ).fetchone()
    # The uncertainty survives in the text, and nothing invented a 1 January to hold it.
    assert "2017-2019" in education["value"]
    assert "months and days unknown" in education["value"]
    assert education["valid_from"] is None
    assert education["valid_to"] is None


def test_a_self_description_is_a_fact_about_the_claim_and_never_a_trait() -> None:
    harness = _Harness()

    harness.accept_all(harness.stage(_cv_batch()).batch_id)

    assert harness.conn.execute("SELECT COUNT(*) FROM traits").fetchone()[0] == 0
    described = harness.conn.execute(
        "SELECT value, provenance_stated_by FROM facts WHERE predicate = 'self_description'"
    ).fetchone()
    assert described["value"] == "Describes herself as analytical in her CV"
    assert described["provenance_stated_by"] == _ATTRIBUTION


def test_sensitive_background_stays_out_of_the_ordinary_read() -> None:
    harness = _Harness()

    harness.accept_all(harness.stage(_cv_batch()).batch_id)
    person_id = harness.conn.execute("SELECT id FROM persons").fetchone()["id"]

    ordinary = harness.context.execute(person_id).model_dump_json()
    elevated = harness.context.execute(person_id, include_sensitive=True).model_dump_json()

    assert "medical career break" not in ordinary
    assert "medical career break" in elevated
    # The ordinary bundle still shows the unprotected claims, so this is disclosure rather than
    # the whole capture having failed.
    assert "Senior Data Engineer" in ordinary


def test_an_ambiguous_subject_commits_nothing_and_creates_no_person() -> None:
    harness = _Harness()
    for alias in ("jamie@example.com", "j.okonkwo@example.com"):
        harness.people.save_person(
            Person(canonical_name="Jamie Okonkwo", aliases=[Alias(value=alias, kind=AliasKind.HANDLE)])
        )

    batch = harness.stage(
        [
            _person("Jamie Okonkwo"),
            {
                "type": "fact",
                "person_ref": "nadia",
                "predicate": "skill",
                "value": "CV lists Rust and distributed systems as primary skills",
                "stated_by": _ATTRIBUTION,
            },
        ]
    )
    staged = harness.staged(batch.batch_id)

    assert staged["person"].candidate["match_disposition"] == "ambiguous"
    assert staged["person"].candidate["match_count"] == 2
    assert staged["person"].candidate["matched_person_id"] is None

    result = harness.accept_all(batch.batch_id)

    # Accepting an ambiguous identity resolves nothing, and its dependants wait with it.
    assert sorted(result.unresolved_ids) == sorted(row.id for row in staged.values())
    assert result.committed_ids == []
    assert harness.conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 2
    assert harness.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0


def test_a_unique_handle_binds_a_legacy_batch_that_reports_no_ambiguity() -> None:
    """Why the guidance resolves identity itself instead of trusting the released matcher.

    A CV batch of `person`, `affiliation`, and `fact` candidates is the pre-M17 shape, so through
    `stage_candidates` it keeps the released matcher. That matcher returns on the first token with
    exactly one hit, so a unique handle wins outright while the name on the document matches two
    other people — and nothing in the review says so. This is not a defect to fix here: narrowing
    it would retroactively change a released MCP contract.
    """
    harness = _Harness()
    handle_owner = Person(
        canonical_name="N. Okonkwo",
        aliases=[Alias(value="nadia.okonkwo@example.com", kind=AliasKind.HANDLE)],
    )
    harness.people.save_person(handle_owner)
    for alias in ("nadia.o@example.com", "n.okonkwo@example.com"):
        harness.people.save_person(
            Person(canonical_name="Nadia Okonkwo", aliases=[Alias(value=alias, kind=AliasKind.HANDLE)])
        )

    batch = harness.stage([_person("Nadia Okonkwo", "nadia.okonkwo@example.com")], strict=False)
    staged = harness.staged(batch.batch_id)

    assert "match_disposition" not in staged["person"].candidate
    assert staged["person"].candidate["matched_person_id"] == handle_owner.id

    # The ambiguity-preserving matcher takes the union of both tokens and refuses to choose.
    strict = harness.staged(harness.stage([_person("Nadia Okonkwo", "nadia.okonkwo@example.com")]).batch_id)
    assert strict["person"].candidate["match_disposition"] == "ambiguous"
    assert strict["person"].candidate["match_count"] == 3


def test_a_legacy_batch_with_nothing_to_separate_two_names_fails_the_whole_commit() -> None:
    """The other half: no report at review, and then the entire accepted batch refuses."""
    harness = _Harness()
    for alias in ("jamie@example.com", "j.okonkwo@example.com"):
        harness.people.save_person(
            Person(canonical_name="Jamie Okonkwo", aliases=[Alias(value=alias, kind=AliasKind.HANDLE)])
        )

    batch = harness.stage([_person("Jamie Okonkwo")], strict=False)
    assert harness.staged(batch.batch_id)["person"].candidate["matched_person_id"] is None

    with pytest.raises(AmbiguousPersonError):
        harness.accept_all(batch.batch_id)

    assert harness.conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 2


def test_the_document_itself_reaches_no_staged_row_receipt_or_inspection() -> None:
    harness = _Harness()

    batch = harness.stage(_cv_batch())
    review = harness.review.execute(batch.batch_id)
    result = harness.accept_all(batch.batch_id)

    sources = harness.list_sources.execute()
    assert len(sources.sources) == 1
    shown = harness.show_source.execute(sources.sources[0].id)

    rendered = [
        review.model_dump_json(),
        result.model_dump_json(),
        json.dumps(batch.model_dump(mode="json")),
        sources.model_dump_json(),
        shown.model_dump_json(),
        harness.dump(),
    ]
    for document in rendered:
        assert _RAW_CV_LINE not in document
        assert "sailing" not in document

    # The receipt is evidence of processing and says so with a digest, never with content.
    assert shown.source.source_kind == "cv"
    assert shown.source.content_digest is not None


def test_a_reprocessed_cv_returns_the_existing_batch_rather_than_staging_it_twice() -> None:
    """Repetition is the same claim seen again, not new evidence and not a second batch."""
    harness = _Harness()
    candidates = _cv_batch()

    first = harness._stage.execute(
        "nadia-cv", candidates, strict_identity=True, source_kind="cv", content_digest="a" * 64
    )
    second = harness._stage.execute(
        "nadia-cv", candidates, strict_identity=True, source_kind="cv", content_digest="a" * 64
    )

    assert second.batch_id == first.batch_id
    assert harness.conn.execute("SELECT COUNT(DISTINCT batch_id) FROM import_staging").fetchone()[0] == 1
