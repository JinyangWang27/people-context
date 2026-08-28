"""Observation, trait, and relationship candidate staging, matching, and commit (M17.1)."""

from __future__ import annotations

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
    MatchDisposition,
    ReviewImport,
    StageCandidates,
)
from people_context.app.people import RememberPerson
from people_context.app.records import (
    RecordFact,
    RecordInteraction,
    RecordObservation,
    RecordTrait,
    SetAffiliation,
)
from people_context.app.relationships import SetRelationship
from people_context.domain.person import Alias, AliasKind, Person

_NOW = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _CountingRememberPerson(RememberPerson):
    """A `RememberPerson` that records whether commit ever reached it.

    An ambiguous identity must not become a new person, and the only way to prove that is to
    watch the call that would have created one rather than to count rows afterwards.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[str] = []

    def execute(self, data: Any, *, transaction_id: str | None = None) -> Any:
        self.calls.append(data.name)
        return super().execute(data, transaction_id=transaction_id)


def _use_cases(conn: Any) -> tuple[Any, ...]:
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    audit = SqliteAuditLog(conn)
    staging = SqliteImportStagingStore(conn)
    remember = _CountingRememberPerson(people, people, audit, _Clock())
    commit = CommitImport(
        people,
        staging,
        remember,
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
    return (
        people,
        StageCandidates(CandidateStager(people, staging, _Clock())),
        ReviewImport(staging),
        commit,
        remember,
    )


def _person(ref: str, name: str, *handles: str) -> dict[str, Any]:
    return {
        "type": "person",
        "ref": ref,
        "name": name,
        "aliases": [{"value": handle, "kind": "handle"} for handle in handles],
    }


def _trait(person_ref: str = "alice", **overrides: Any) -> dict[str, Any]:
    candidate = {
        "type": "trait",
        "person_ref": person_ref,
        "category": "communication_style",
        "value": "Responds better to proposals supported by quantitative evidence",
        "evidence_note": "Derived from the 24 Aug planning meeting: repeatedly asked for measurable evidence.",
        "confidence": 0.65,
    }
    candidate.update(overrides)
    return candidate


def _rows_by_type(review: Any, batch_id: str) -> dict[str, Any]:
    return {row.candidate["type"]: row for row in review.execute(batch_id).candidates}


def test_observation_trait_and_relationship_commit_through_their_own_audited_use_cases() -> None:
    conn = open_db(":memory:")
    _, stage, review, commit, _ = _use_cases(conn)

    batch = stage.execute(
        "planning-meeting",
        [
            _person("alice", "Alice Rivera", "alice@example.com"),
            _person("bob", "Bob Chen", "bob@example.com"),
            {
                "type": "observation",
                "person_ref": "alice",
                "text": "Asked for concrete metrics before agreeing to the proposal",
                "observed_at": "2026-08-19T10:00:00+00:00",
            },
            _trait(),
            {
                "type": "relationship",
                "from_ref": "alice",
                "to_ref": "bob",
                "relationship_type": "manages",
                "confidence": 0.9,
            },
        ],
    )
    rows = review.execute(batch.batch_id).candidates
    result = commit.execute(batch.batch_id, [row.id for row in rows])

    assert result.unresolved_ids == []
    assert len(result.committed_ids) == 5

    observation = conn.execute("SELECT text, observed_at, sensitivity, provenance_source FROM observations").fetchone()
    assert tuple(observation) == (
        "Asked for concrete metrics before agreeing to the proposal",
        "2026-08-19T10:00:00+00:00",
        "personal",
        "import/agent:planning-meeting",
    )
    trait = conn.execute("SELECT category, value, evidence_note, confidence, provenance_source FROM traits").fetchone()
    assert trait["category"] == "communication_style"
    assert trait["confidence"] == pytest.approx(0.65)
    assert trait["evidence_note"].startswith("Derived from the 24 Aug planning meeting")
    assert trait["provenance_source"] == "import/agent:planning-meeting"

    # Every write is audited by the use case that owns it — import adds no privileged path.
    audited = {
        row["entity_type"]
        for row in conn.execute("SELECT DISTINCT entity_type FROM audit_log").fetchall()
    }
    assert {"observation", "trait", "relationship"} <= audited
    changed = {
        row["entity_type"]
        for row in conn.execute("SELECT DISTINCT entity_type FROM changelog").fetchall()
    }
    assert {"observation", "trait", "relationship"} <= changed


def test_relationship_commit_canonicalizes_a_seeded_non_canonical_type() -> None:
    conn = open_db(":memory:")
    people, stage, review, commit, _ = _use_cases(conn)

    batch = stage.execute(
        "notes",
        [
            _person("sarah", "Sarah Ito"),
            _person("bob", "Bob Chen"),
            {"type": "relationship", "from_ref": "sarah", "to_ref": "bob", "relationship_type": "Manages"},
        ],
    )
    rows = review.execute(batch.batch_id).candidates
    commit.execute(batch.batch_id, [row.id for row in rows])

    stored = conn.execute("SELECT subject_id, object_id, type FROM relationships").fetchone()
    sarah = people.find_by_normalized_name("sarah ito")[0]
    bob = people.find_by_normalized_name("bob chen")[0]
    # `manages` is a non-canonical seeded spelling, so the stored edge is its inverse with the
    # endpoints swapped — exactly what a direct `set_relationship` call would have written.
    assert tuple(stored) == (bob.id, sarah.id, "reports_to")


def test_relationship_commit_keeps_an_unknown_but_valid_type_as_an_uncategorized_edge() -> None:
    conn = open_db(":memory:")
    people, stage, review, commit, _ = _use_cases(conn)

    batch = stage.execute(
        "notes",
        [
            _person("sarah", "Sarah Ito"),
            _person("bob", "Bob Chen"),
            # `manager` is not a seeded synonym. M17 must not require registration, or every
            # currently legal uncategorized edge would become unimportable.
            {"type": "relationship", "from_ref": "sarah", "to_ref": "bob", "relationship_type": "Manager"},
        ],
    )
    rows = review.execute(batch.batch_id).candidates
    commit.execute(batch.batch_id, [row.id for row in rows])

    stored = conn.execute("SELECT subject_id, object_id, type FROM relationships").fetchone()
    sarah = people.find_by_normalized_name("sarah ito")[0]
    bob = people.find_by_normalized_name("bob chen")[0]
    assert tuple(stored) == (sarah.id, bob.id, "manager")


@pytest.mark.parametrize(
    "candidate",
    [
        pytest.param({"type": "observation", "person_ref": "alice"}, id="observation-without-text"),
        pytest.param(
            {"type": "observation", "person_ref": "alice", "text": "   "},
            id="observation-blank-text",
        ),
        pytest.param(
            {"type": "observation", "person_ref": "alice", "text": "seen", "quote": "raw transcript"},
            id="observation-extra-field",
        ),
        pytest.param(_trait(confidence=None), id="trait-without-confidence"),
        pytest.param(_trait(evidence_note="   "), id="trait-blank-evidence-note"),
        pytest.param(_trait(confidence=1.5), id="trait-confidence-out-of-range"),
        pytest.param(_trait(category="astrological_sign"), id="trait-unknown-category"),
        pytest.param(_trait(transcript="raw"), id="trait-extra-field"),
        pytest.param(
            {"type": "relationship", "from_ref": "alice", "relationship_type": "friend"},
            id="relationship-without-to-ref",
        ),
        pytest.param(
            {"type": "relationship", "from_ref": "alice", "to_ref": "bob", "relationship_type": "   "},
            id="relationship-blank-type",
        ),
        pytest.param(
            {"type": "relationship", "from_ref": "alice", "to_ref": "bob", "relationship_type": "-- ??"},
            id="relationship-non-word-type",
        ),
        pytest.param(
            {
                "type": "relationship",
                "from_ref": "alice",
                "to_ref": "bob",
                "relationship_type": "friend",
                "sensitivity": "restricted",
            },
            id="relationship-sensitivity-field",
        ),
        pytest.param(
            {"type": "relationship", "from_ref": "alice", "to_ref": "alice", "relationship_type": "friend"},
            id="relationship-self-edge",
        ),
        pytest.param(
            {"type": "relationship", "from_ref": "alice", "to_ref": "nobody", "relationship_type": "friend"},
            id="relationship-unknown-ref",
        ),
        pytest.param(
            {"type": "observation", "person_ref": "nobody", "text": "seen"},
            id="observation-unknown-ref",
        ),
    ],
)
def test_invalid_extraction_candidates_are_strict_and_stage_nothing(candidate: dict[str, Any]) -> None:
    conn = open_db(":memory:")
    _, stage, _, _, _ = _use_cases(conn)

    with pytest.raises(ImportPipelineError) as exc_info:
        stage.execute("notes", [_person("alice", "Alice"), _person("bob", "Bob"), candidate])

    assert exc_info.value.code == "invalid_candidates"
    assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


def test_relationship_sensitivity_field_is_rejected_rather_than_silently_discarded() -> None:
    """An elevated edge must fail loudly: the graph cannot enforce the protection it implies."""
    conn = open_db(":memory:")
    _, stage, _, _, _ = _use_cases(conn)

    with pytest.raises(ImportPipelineError) as exc_info:
        stage.execute(
            "notes",
            [
                _person("alice", "Alice"),
                _person("bob", "Bob"),
                {
                    "type": "relationship",
                    "from_ref": "alice",
                    "to_ref": "bob",
                    "relationship_type": "friend",
                    "sensitivity": "sensitive",
                },
            ],
        )

    assert [detail["type"] for detail in exc_info.value.details["details"]] == ["extra_forbidden"]
    assert conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] == 0


def test_new_dependent_types_have_their_person_ref_rewritten_to_the_staged_candidate_id() -> None:
    conn = open_db(":memory:")
    _, stage, review, _, _ = _use_cases(conn)

    batch = stage.execute(
        "notes",
        [
            _person("alice", "Alice Rivera"),
            _person("bob", "Bob Chen"),
            {"type": "observation", "person_ref": "alice", "text": "Asked for metrics"},
            _trait(),
            {"type": "relationship", "from_ref": "alice", "to_ref": "bob", "relationship_type": "colleague"},
        ],
    )
    rows = review.execute(batch.batch_id).candidates
    by_type = {row.candidate["type"]: row for row in rows}
    alice_id = next(row.id for row in rows if row.candidate.get("name") == "Alice Rivera")
    bob_id = next(row.id for row in rows if row.candidate.get("name") == "Bob Chen")

    for candidate_type in ("observation", "trait"):
        assert "person_ref" not in by_type[candidate_type].candidate
        assert by_type[candidate_type].candidate["person_candidate_id"] == alice_id
    relationship = by_type["relationship"].candidate
    assert "from_ref" not in relationship and "to_ref" not in relationship
    assert relationship["from_candidate_id"] == alice_id
    assert relationship["to_candidate_id"] == bob_id


def test_extraction_batch_reports_an_unmatched_person_as_a_new_identity() -> None:
    conn = open_db(":memory:")
    _, stage, review, _, _ = _use_cases(conn)

    batch = stage.execute(
        "notes",
        [
            _person("alice", "Alice Rivera", "alice@example.com"),
            {"type": "observation", "person_ref": "alice", "text": "Asked for metrics"},
        ],
    )
    person = _rows_by_type(review, batch.batch_id)["person"].candidate

    assert person["match_disposition"] == MatchDisposition.UNMATCHED.value
    assert person["matched_person_id"] is None
    assert person["match_count"] == 0


def test_extraction_batch_reports_a_single_existing_person_as_matched() -> None:
    conn = open_db(":memory:")
    people, stage, review, _, _ = _use_cases(conn)
    existing = Person(canonical_name="Alice Rivera", aliases=[Alias(value="alice@example.com", kind=AliasKind.HANDLE)])
    people.save_person(existing)

    batch = stage.execute(
        "notes",
        [
            _person("alice", "Alice Rivera", "alice@example.com"),
            {"type": "observation", "person_ref": "alice", "text": "Asked for metrics"},
        ],
    )
    person = _rows_by_type(review, batch.batch_id)["person"].candidate

    assert person["match_disposition"] == MatchDisposition.MATCHED.value
    assert person["matched_person_id"] == existing.id
    assert person["match_count"] == 1


def test_extraction_batch_reports_two_people_sharing_one_token_as_ambiguous() -> None:
    conn = open_db(":memory:")
    people, stage, review, _, _ = _use_cases(conn)
    for alias in ("first@example.com", "second@example.com"):
        people.save_person(Person(canonical_name="Alice Rivera", aliases=[Alias(value=alias, kind=AliasKind.HANDLE)]))

    batch = stage.execute("notes", [_person("alice", "Alice Rivera"), _trait("alice")])
    person = _rows_by_type(review, batch.batch_id)["person"].candidate

    assert person["match_disposition"] == MatchDisposition.AMBIGUOUS.value
    assert person["matched_person_id"] is None
    assert person["match_count"] == 2


def test_a_handle_that_normalizes_to_nothing_contributes_no_match_evidence() -> None:
    """A degenerate token is skipped, not asked about.

    `NonBlank` only rejects whitespace, so a handle of combining marks alone survives
    validation and then normalizes away entirely. Querying on the empty string would match on
    nothing meaningful, so the union must simply not count it — the candidate is still decided
    by the tokens that do normalize.
    """
    conn = open_db(":memory:")
    people, stage, review, _, _ = _use_cases(conn)
    existing = Person(canonical_name="Alice Rivera")
    people.save_person(existing)

    batch = stage.execute("notes", [_person("alice", "Alice Rivera", "́"), _trait("alice")])
    person = _rows_by_type(review, batch.batch_id)["person"].candidate

    assert person["match_disposition"] == MatchDisposition.MATCHED.value
    assert person["matched_person_id"] == existing.id
    assert person["match_count"] == 1


def test_a_unique_handle_does_not_short_circuit_a_conflicting_name() -> None:
    """The union is what decides. A first-token hit must not hide a conflict on a later one."""
    conn = open_db(":memory:")
    people, stage, review, _, _ = _use_cases(conn)
    people.save_person(
        Person(canonical_name="Alice Rivera", aliases=[Alias(value="alice@example.com", kind=AliasKind.HANDLE)])
    )
    people.save_person(Person(canonical_name="Alice Rivera"))

    batch = stage.execute("notes", [_person("alice", "Alice Rivera", "alice@example.com"), _trait("alice")])
    person = _rows_by_type(review, batch.batch_id)["person"].candidate

    assert person["match_disposition"] == MatchDisposition.AMBIGUOUS.value
    assert person["matched_person_id"] is None
    assert person["match_count"] == 2


def test_an_unmatched_name_with_a_handle_matching_two_people_is_ambiguous_not_unmatched() -> None:
    """The regression this whole disposition exists for: ambiguity read as a new identity."""
    conn = open_db(":memory:")
    people, stage, review, commit, remember = _use_cases(conn)
    for name in ("Alexandra Rivera", "Alex Rivera"):
        people.save_person(
            Person(canonical_name=name, aliases=[Alias(value="shared@example.com", kind=AliasKind.HANDLE)])
        )

    batch = stage.execute(
        "notes",
        [
            _person("alice", "A. Rivera", "shared@example.com"),
            {"type": "observation", "person_ref": "alice", "text": "Asked for metrics"},
        ],
    )
    rows = review.execute(batch.batch_id).candidates
    person = next(row for row in rows if row.candidate["type"] == "person")
    observation = next(row for row in rows if row.candidate["type"] == "observation")
    assert person.candidate["match_disposition"] == MatchDisposition.AMBIGUOUS.value

    result = commit.execute(batch.batch_id, [person.id, observation.id])

    assert result.committed_ids == []
    assert result.unresolved_ids == [person.id, observation.id]
    assert remember.calls == []
    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_an_ambiguous_candidate_resolves_once_the_conflict_is_externally_corrected() -> None:
    conn = open_db(":memory:")
    people, stage, review, commit, remember = _use_cases(conn)
    keep = Person(canonical_name="Alexandra Rivera", aliases=[Alias(value="shared@example.com", kind=AliasKind.HANDLE)])
    duplicate = Person(canonical_name="Alex Rivera", aliases=[Alias(value="shared@example.com", kind=AliasKind.HANDLE)])
    people.save_person(keep)
    people.save_person(duplicate)

    batch = stage.execute(
        "notes",
        [
            _person("alice", "shared@example.com", "shared@example.com"),
            {"type": "observation", "person_ref": "alice", "text": "Asked for metrics"},
        ],
    )
    rows = review.execute(batch.batch_id).candidates
    person = next(row for row in rows if row.candidate["type"] == "person")
    observation = next(row for row in rows if row.candidate["type"] == "observation")
    assert commit.execute(batch.batch_id, [person.id, observation.id]).unresolved_ids == [person.id, observation.id]

    # An explicit external correction leaves exactly one active person behind that handle.
    conn.execute("UPDATE persons SET deleted_at = ? WHERE id = ?", (_NOW.isoformat(), duplicate.id))

    result = commit.execute(batch.batch_id, [person.id, observation.id])

    assert result.unresolved_ids == []
    assert set(result.committed_ids) == {person.id, observation.id}
    # Resolution merges into the surviving person rather than minting a second identity.
    assert remember.calls == [keep.canonical_name]
    assert conn.execute("SELECT person_id FROM observations").fetchone()["person_id"] == keep.id


def test_accepted_dependants_of_an_unaccepted_ambiguous_person_stay_unresolved() -> None:
    conn = open_db(":memory:")
    people, stage, review, commit, remember = _use_cases(conn)
    for name in ("Sam Lee", "Samuel Lee"):
        people.save_person(Person(canonical_name=name, aliases=[Alias(value="sam@example.com", kind=AliasKind.HANDLE)]))

    batch = stage.execute(
        "notes",
        [
            _person("sam", "Sam", "sam@example.com"),
            _person("bob", "Bob Chen"),
            _trait("sam"),
            {"type": "relationship", "from_ref": "sam", "to_ref": "bob", "relationship_type": "colleague"},
        ],
    )
    rows = review.execute(batch.batch_id).candidates
    by_type = {row.candidate["type"]: row for row in rows}
    bob = next(row for row in rows if row.candidate.get("name") == "Bob Chen")
    dependants = [by_type["trait"].id, by_type["relationship"].id]

    # Bob is accepted and unambiguous; only Sam's identity is open. Both dependants still need
    # Sam, so neither may commit — a resolvable second endpoint does not rescue the edge.
    result = commit.execute(batch.batch_id, [bob.id, *dependants])

    assert set(result.unresolved_ids) == set(dependants)
    assert remember.calls == ["Bob Chen"]
    assert conn.execute("SELECT COUNT(*) FROM traits").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] == 0


def test_a_relationship_commits_only_once_both_endpoints_resolve() -> None:
    conn = open_db(":memory:")
    _, stage, review, commit, _ = _use_cases(conn)

    batch = stage.execute(
        "notes",
        [
            _person("alice", "Alice Rivera"),
            _person("bob", "Bob Chen"),
            {"type": "relationship", "from_ref": "alice", "to_ref": "bob", "relationship_type": "colleague"},
        ],
    )
    rows = review.execute(batch.batch_id).candidates
    by_name = {row.candidate.get("name"): row for row in rows if row.candidate["type"] == "person"}
    relationship = next(row for row in rows if row.candidate["type"] == "relationship")

    assert commit.execute(batch.batch_id, [by_name["Alice Rivera"].id, relationship.id]).unresolved_ids == [
        relationship.id
    ]
    commit.execute(batch.batch_id, [by_name["Bob Chen"].id])
    assert commit.execute(batch.batch_id, [relationship.id]).committed_ids == [relationship.id]
    assert conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] == 1


def test_two_refs_resolving_to_one_person_leave_the_relationship_unresolved() -> None:
    """A self-loop is corruption `merge_people` cleans up; import must not create one.

    Staging only sees ref strings, and agents are told to stage a candidate for every
    participant, so a name and a matching handle routinely describe the same existing person.
    The endpoints-differ rule therefore has to hold on the identities the refs resolve to.
    """
    conn = open_db(":memory:")
    people, stage, review, commit, _ = _use_cases(conn)
    existing = Person(canonical_name="Alice Rivera", aliases=[Alias(value="alice@example.com", kind=AliasKind.HANDLE)])
    people.save_person(existing)

    batch = stage.execute(
        "notes",
        [
            _person("by_name", "Alice Rivera"),
            _person("by_handle", "A. Rivera", "alice@example.com"),
            {
                "type": "relationship",
                "from_ref": "by_name",
                "to_ref": "by_handle",
                "relationship_type": "colleague",
            },
        ],
    )
    rows = review.execute(batch.batch_id).candidates
    relationship = next(row for row in rows if row.candidate["type"] == "relationship")

    result = commit.execute(batch.batch_id, [row.id for row in rows])

    assert result.unresolved_ids == [relationship.id]
    assert relationship.id not in result.committed_ids
    assert conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 1


def test_a_legacy_only_batch_keeps_its_released_matching_shape() -> None:
    """M17 is opt-in. A batch of the four released types is staged exactly as it was before."""
    conn = open_db(":memory:")
    people, stage, review, _, _ = _use_cases(conn)
    intended = Person(canonical_name="Sam Lee", aliases=[Alias(value="Sammy", kind=AliasKind.NICKNAME)])
    people.save_person(intended)
    people.save_person(Person(canonical_name="Sam Lee", aliases=[Alias(value="S. Lee", kind=AliasKind.FORMER_NAME)]))

    batch = stage.execute(
        "notes",
        [
            {"type": "person", "ref": "sam", "name": "Sammy", "aliases": []},
            {"type": "fact", "person_ref": "sam", "predicate": "location", "value": "Dubai"},
        ],
    )
    person = _rows_by_type(review, batch.batch_id)["person"].candidate

    assert "match_disposition" not in person
    assert "match_count" not in person
    assert person["matched_person_id"] == intended.id


def test_staged_extraction_candidates_carry_only_distilled_values() -> None:
    """Whatever the agent read stays outside the database; only what it wrote is staged."""
    conn = open_db(":memory:")
    _, stage, review, _, _ = _use_cases(conn)

    batch = stage.execute(
        "board-call",
        [
            _person("alice", "Alice Rivera", "alice@example.com"),
            {"type": "observation", "person_ref": "alice", "text": "Asked for concrete metrics"},
            _trait(),
        ],
    )
    staged = str([row.candidate for row in review.execute(batch.batch_id).candidates])

    assert "Asked for concrete metrics" in staged
    for sentinel in ("TRANSCRIPT", "verbatim", "[00:0", "Speaker 1:"):
        assert sentinel not in staged
