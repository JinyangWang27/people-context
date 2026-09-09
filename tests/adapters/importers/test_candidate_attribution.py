"""Assertion attribution on fact and affiliation candidates (M22.1).

A CV, a biography, or a page of notes supplies claims from a source, and the point of `stated_by`
is that storing one does not make it true. These tests hold the three things that keeps honest:
the attribution survives the whole stage → review → commit lifecycle into durable provenance, it
stays distinct from the processing metadata sitting next to it, and a candidate that carries none
behaves exactly as it did before the field existed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteImportStagingStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    open_db,
)
from people_context.app.imports import (
    CandidateStager,
    CommitImport,
    ImportPipelineError,
    ReviewImport,
    StageCandidates,
)
from people_context.app.imports.models import MAX_STATED_BY_CHARS
from people_context.app.people import RememberPerson
from people_context.app.records import (
    RecordFact,
    RecordInteraction,
    RecordObservation,
    RecordTrait,
    SetAffiliation,
)
from people_context.app.relationships import SetRelationship

_NOW = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _use_cases(conn: Any) -> tuple[Any, ...]:
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    audit = SqliteAuditLog(conn)
    staging = SqliteImportStagingStore(conn)
    commit = CommitImport(
        people,
        staging,
        RememberPerson(people, people, audit, _Clock()),
        RecordInteraction(people, records, audit, _Clock()),
        SetAffiliation(people, SqliteOrganizationStore(conn), records, audit, _Clock()),
        RecordFact(people, records, audit, _Clock()),
        RecordObservation(people, records, audit, _Clock()),
        RecordTrait(people, records, audit, _Clock()),
        SetRelationship(
            people,
            SqliteRelationshipStore(conn),
            audit,
            _Clock(),
            SqliteRelationshipVocabularyStore(conn),
        ),
    )
    return StageCandidates(CandidateStager(people, staging, _Clock())), ReviewImport(staging), commit


def _person(ref: str = "nadia", name: str = "Nadia Okonkwo") -> dict[str, Any]:
    return {"type": "person", "ref": ref, "name": name, "aliases": []}


def _fact(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "type": "fact",
        "person_ref": "nadia",
        "predicate": "qualification",
        "value": "MSc Structural Engineering, Northbridge College",
    }
    candidate.update(overrides)
    return candidate


def _affiliation(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "type": "affiliation",
        "person_ref": "nadia",
        "org": "Globex",
        "role": "Structural Engineer",
    }
    candidate.update(overrides)
    return candidate


def _commit_all(stage: Any, review: Any, commit: Any, candidates: list[dict[str, Any]]) -> Any:
    batch = stage.execute("nadia-cv", candidates)
    rows = review.execute(batch.batch_id).candidates
    return commit.execute(batch.batch_id, [row.id for row in rows])


def test_attribution_on_a_fact_and_an_affiliation_reaches_durable_provenance() -> None:
    """The whole point of the field: what the agent staged is what the record ends up carrying."""
    conn = open_db(":memory:")
    stage, review, commit = _use_cases(conn)

    result = _commit_all(
        stage,
        review,
        commit,
        [
            _person(),
            _fact(stated_by="Nadia Okonkwo's CV"),
            _affiliation(valid_from="2021-03-01", stated_by="Nadia Okonkwo's CV"),
        ],
    )

    assert result.unresolved_ids == []
    assert len(result.committed_ids) == 3

    fact = conn.execute("SELECT value, provenance_source, provenance_stated_by FROM facts").fetchone()
    assert fact["value"] == "MSc Structural Engineering, Northbridge College"
    assert fact["provenance_stated_by"] == "Nadia Okonkwo's CV"
    affiliation = conn.execute("SELECT role, provenance_source, provenance_stated_by FROM affiliations").fetchone()
    assert affiliation["role"] == "Structural Engineer"
    assert affiliation["provenance_stated_by"] == "Nadia Okonkwo's CV"

    # Attribution rides beside the processing metadata rather than displacing it: the batch still
    # records which process wrote each row.
    assert fact["provenance_source"] == affiliation["provenance_source"] == "import/agent:nadia-cv"


def test_attribution_reaches_the_changelog_actor_for_both_types() -> None:
    """A replayed write must carry the attribution too, or a synced device loses it."""
    conn = open_db(":memory:")
    stage, review, commit = _use_cases(conn)

    _commit_all(
        stage,
        review,
        commit,
        [_person(), _fact(stated_by="her CV"), _affiliation(stated_by="her CV")],
    )

    actors = {
        row["entity_type"]: json.loads(row["actor_json"])
        for row in conn.execute("SELECT entity_type, actor_json FROM changelog").fetchall()
    }
    assert actors["fact"]["stated_by"] == "her CV"
    assert actors["affiliation"]["stated_by"] == "her CV"
    assert actors["fact"]["source"] == "import/agent:nadia-cv"
    # The person write carried no attribution and must not have acquired one.
    assert "stated_by" not in actors["person"]


def test_a_candidate_without_attribution_commits_exactly_as_it_did_before() -> None:
    """Every batch staged before M22.1 is this batch. It must be untouched."""
    conn = open_db(":memory:")
    stage, review, commit = _use_cases(conn)

    result = _commit_all(stage, review, commit, [_person(), _fact(), _affiliation()])

    assert result.unresolved_ids == []
    fact = conn.execute("SELECT provenance_stated_by FROM facts").fetchone()
    affiliation = conn.execute("SELECT provenance_stated_by FROM affiliations").fetchone()
    assert fact["provenance_stated_by"] is None
    assert affiliation["provenance_stated_by"] is None

    staged = [
        json.loads(row["candidate_json"])
        for row in conn.execute("SELECT candidate_json FROM import_staging").fetchall()
    ]
    assert all("stated_by" not in candidate for candidate in staged), "an unset optional stays absent"


def test_review_shows_the_attribution_that_commit_will_write() -> None:
    """The review gate is where someone decides; it has to show what it is deciding about."""
    conn = open_db(":memory:")
    stage, review, _ = _use_cases(conn)

    batch = stage.execute("nadia-cv", [_person(), _fact(stated_by="her CV")])
    staged = {row.candidate["type"]: row.candidate for row in review.execute(batch.batch_id).candidates}

    assert staged["fact"]["stated_by"] == "her CV"


def test_attribution_is_stripped_and_bounded_on_the_model_itself() -> None:
    """Bounded for every request, not only one that opts into the M17 extraction budgets.

    Those budgets are conditional, so a legacy fact-only batch like this one reaches no byte
    ceiling at all. Attribution is exactly where a copied document passage would otherwise sit.
    """
    conn = open_db(":memory:")
    stage, review, _ = _use_cases(conn)

    batch = stage.execute("nadia-cv", [_person(), _fact(stated_by="  her CV  ")])
    staged = {row.candidate["type"]: row.candidate for row in review.execute(batch.batch_id).candidates}
    assert staged["fact"]["stated_by"] == "her CV"

    with pytest.raises(ImportPipelineError):
        stage.execute("nadia-cv", [_person(), _fact(stated_by="x" * (MAX_STATED_BY_CHARS + 1))])


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_attribution_is_refused_rather_than_stored_as_an_empty_speaker(value: str) -> None:
    """Unknown attribution is absent. A blank one asserts a speaker exists and names nobody."""
    conn = open_db(":memory:")
    stage, _, _ = _use_cases(conn)

    with pytest.raises(ImportPipelineError):
        stage.execute("nadia-cv", [_person(), _fact(stated_by=value)])


@pytest.mark.parametrize(
    "candidate",
    [
        {"type": "observation", "person_ref": "nadia", "text": "Asked for the drawings", "stated_by": "her CV"},
        {
            "type": "trait",
            "person_ref": "nadia",
            "category": "communication_style",
            "value": "Analytical",
            "evidence_note": "From the CV.",
            "confidence": 0.6,
            "stated_by": "her CV",
        },
        {
            "type": "relationship",
            "from_ref": "nadia",
            "to_ref": "nadia2",
            "relationship_type": "colleague of",
            "stated_by": "her CV",
        },
        {"type": "person", "ref": "x", "name": "X", "aliases": [], "stated_by": "her CV"},
    ],
)
def test_no_other_candidate_type_accepts_attribution(candidate: dict[str, Any]) -> None:
    """M22.1 broadens two candidate types and no others.

    A trait is the sharpest case. A CV calling someone analytical is that person's own claim about
    themselves, and it belongs in a fact whose value says so — not in a trait wearing an
    attribution, which would dress a self-description up as an inferred characteristic.
    """
    conn = open_db(":memory:")
    stage, _, _ = _use_cases(conn)

    with pytest.raises(ImportPipelineError):
        stage.execute("nadia-cv", [_person(), _person("nadia2", "Nadia Two"), candidate])
