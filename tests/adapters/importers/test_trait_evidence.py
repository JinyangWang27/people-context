"""Staging and committing the durable evidence a trait rests on (M18.3).

Two halves. Staging must refuse a batch whose evidence references cannot be rewritten to one
unambiguous candidate, and must rewrite the ones it accepts deterministically. Commit must
resolve those rewritten ids — and any explicit durable ids — through the M18.1 mapping, apply
the type and subject rules, and *decline* rather than write a trait it cannot ground.
"""

from __future__ import annotations

import sqlite3
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
from people_context.adapters.sqlite.source_store import SqliteImportSourceStore
from people_context.adapters.sqlite.trait_evidence import SqliteTraitEvidenceStore
from people_context.app.imports import (
    CandidateStager,
    CommitImport,
    ImportPipelineError,
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
from people_context.domain.trait_evidence import MAX_EVIDENCE_REFERENCE_CHARS, MAX_TRAIT_EVIDENCE_LINKS

_NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _Harness:
    """One in-memory store wired for the whole stage/review/commit lifecycle.

    Batches are source-tracked, because a trait resolves same-batch evidence through the M18.1
    candidate commit mapping and that mapping only exists for a tracked batch.
    """

    def __init__(self) -> None:
        self.conn = open_db(":memory:")
        self._digests = 0
        people = SqlitePeopleRepository(self.conn)
        records = SqliteRecordStore(self.conn)
        audit = SqliteAuditLog(self.conn)
        staging = SqliteImportStagingStore(self.conn)
        sources = SqliteImportSourceStore(self.conn)
        self.evidence = SqliteTraitEvidenceStore(self.conn)
        self.people = people
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
            RecordTrait(people, records, audit, _Clock(), self.evidence),
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
            self.evidence,
        )

    def stage_batch(self, source: str, candidates: list[dict[str, Any]]) -> Any:
        """Stage one tracked batch, each with a distinct claim so nothing reads as a duplicate."""
        self._digests += 1
        return self._stage.execute(
            source,
            candidates,
            source_kind="meeting_transcript",
            content_digest=f"{self._digests:064x}",
        )

    def stage_untracked(self, source: str, candidates: list[dict[str, Any]]) -> Any:
        """Stage without any receipt metadata — the released default for `stage_candidates`."""
        return self._stage.execute(source, candidates)

    def links(self) -> list[tuple[str, str, str]]:
        return [
            (row["trait_id"], row["evidence_type"], row["evidence_id"])
            for row in self.conn.execute(
                "SELECT trait_id, evidence_type, evidence_id FROM trait_evidence"
                " ORDER BY trait_id, evidence_type, evidence_id"
            )
        ]

    def rows(self, batch_id: str) -> list[Any]:
        return self.review.execute(batch_id).candidates

    def committed(self, batch_id: str) -> dict[str, str]:
        """Return this batch's candidate-to-entity mappings."""
        return {
            row["candidate_id"]: row["entity_id"]
            for row in self.conn.execute(
                "SELECT candidate_id, entity_id FROM import_candidate_mappings WHERE batch_id = ?",
                (batch_id,),
            )
        }


def _person(ref: str, name: str, *handles: str) -> dict[str, Any]:
    return {
        "type": "person",
        "ref": ref,
        "name": name,
        "aliases": [{"value": handle, "kind": "handle"} for handle in handles],
    }


def _observation(person_ref: str = "alice", **overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "type": "observation",
        "person_ref": person_ref,
        "text": "Asked for concrete metrics before agreeing to the proposal",
        "observed_at": "2026-08-19T10:00:00+00:00",
    }
    candidate.update(overrides)
    return candidate


def _interaction(*participant_refs: str, **overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "type": "interaction",
        "summary": "Planning meeting",
        "participant_refs": list(participant_refs),
        "date": "2026-08-19T10:00:00+00:00",
    }
    candidate.update(overrides)
    return candidate


def _trait(person_ref: str = "alice", **overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "type": "trait",
        "person_ref": person_ref,
        "category": "communication_style",
        "value": "Responds better to proposals supported by quantitative evidence",
        "evidence_note": "Derived from the 19 Aug planning meeting.",
        "confidence": 0.65,
    }
    candidate.update(overrides)
    return candidate


def _staged(harness: _Harness, batch_id: str, kind: str) -> dict[str, Any]:
    return next(row.candidate for row in harness.rows(batch_id) if row.candidate["type"] == kind)


# -- staging: reference validation and rewriting -----------------------


def test_a_trait_cites_same_batch_evidence_by_canonical_candidate_id() -> None:
    harness = _Harness()

    batch = harness.stage_batch(
        "planning-meeting",
        [
            _person("alice", "Alice Rivera", "alice@example.com"),
            _observation(evidence_ref="metrics-question"),
            _interaction("alice", evidence_ref="meeting"),
            _trait(evidence_refs=["meeting", "metrics-question"]),
        ],
    )

    rows = {row.candidate["type"]: row for row in harness.rows(batch.batch_id)}
    trait = rows["trait"].candidate
    assert trait["evidence_candidate_ids"] == [rows["interaction"].id, rows["observation"].id]
    # The caller's own labels never reach storage: review shows canonical ids only.
    assert "evidence_refs" not in trait
    assert "evidence_ref" not in rows["observation"].candidate
    assert "evidence_ref" not in rows["interaction"].candidate


def test_a_candidate_without_the_optional_reference_is_staged_exactly_as_before() -> None:
    harness = _Harness()

    batch = harness.stage_batch(
        "planning-meeting",
        [_person("alice", "Alice Rivera"), _observation(), _interaction("alice"), _trait()],
    )

    for kind in ("observation", "interaction"):
        assert "evidence_ref" not in _staged(harness, batch.batch_id, kind)
    trait = _staged(harness, batch.batch_id, "trait")
    assert "evidence_candidate_ids" not in trait
    assert "evidence_ids" not in trait


def test_a_duplicate_evidence_reference_is_refused_before_staging() -> None:
    harness = _Harness()

    with pytest.raises(ImportPipelineError) as excinfo:
        harness.stage_batch(
            "planning-meeting",
            [
                _person("alice", "Alice Rivera"),
                _observation(evidence_ref="same"),
                _interaction("alice", evidence_ref="same"),
                _trait(evidence_refs=["same"]),
            ],
        )

    assert excinfo.value.code == "invalid_candidates"
    assert harness.conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


@pytest.mark.parametrize(
    "refs",
    [
        pytest.param(["absent"], id="undeclared"),
        # A person `ref` lives in a different namespace, so citing one is simply unknown here
        # rather than a legal reference to something a trait cannot rest on.
        pytest.param(["alice"], id="person-ref"),
    ],
)
def test_an_evidence_reference_naming_no_evidence_candidate_is_refused(refs: list[str]) -> None:
    harness = _Harness()

    with pytest.raises(ImportPipelineError) as excinfo:
        harness.stage_batch(
            "planning-meeting",
            [
                _person("alice", "Alice Rivera"),
                _observation(evidence_ref="metrics-question"),
                _trait(evidence_refs=refs),
            ],
        )

    assert excinfo.value.code == "invalid_candidates"
    assert harness.conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


@pytest.mark.parametrize(
    "candidate",
    [
        pytest.param(_observation(evidence_ref="   "), id="blank-ref"),
        pytest.param(_observation(evidence_ref="x" * (MAX_EVIDENCE_REFERENCE_CHARS + 1)), id="overlong-ref"),
    ],
)
def test_a_blank_or_overlong_evidence_reference_is_refused(candidate: dict[str, Any]) -> None:
    harness = _Harness()

    with pytest.raises(ImportPipelineError):
        harness.stage_batch("planning-meeting", [_person("alice", "Alice Rivera"), candidate])


@pytest.mark.parametrize(
    "evidence_ids",
    [
        pytest.param(["  "], id="blank-id"),
        pytest.param(["x" * (MAX_EVIDENCE_REFERENCE_CHARS + 1)], id="overlong-id"),
        pytest.param(["obs-1", "obs-1"], id="repeated-id"),
    ],
)
def test_a_blank_overlong_or_repeated_durable_evidence_id_is_refused(evidence_ids: list[str]) -> None:
    harness = _Harness()

    with pytest.raises(ImportPipelineError):
        harness.stage_batch(
            "planning-meeting",
            [_person("alice", "Alice Rivera"), _trait(evidence_ids=evidence_ids)],
        )


def test_the_combined_evidence_budget_bounds_references_and_durable_ids_together() -> None:
    harness = _Harness()
    refs = [f"obs-{index}" for index in range(MAX_TRAIT_EVIDENCE_LINKS)]

    with pytest.raises(ImportPipelineError):
        harness.stage_batch(
            "planning-meeting",
            [
                _person("alice", "Alice Rivera"),
                *(_observation(evidence_ref=ref) for ref in refs),
                _trait(evidence_refs=refs, evidence_ids=["one-too-many"]),
            ],
        )


def test_a_rejected_evidence_reference_is_never_echoed() -> None:
    """The label is as untrusted as any other extracted string.

    An `evidence_ref` is free-form text the agent chose, so it can carry source wording just as a
    candidate body can. The refusal reports the field location and the failure category; neither
    the label nor any candidate content travels back out through the diagnostic.
    """
    harness = _Harness()
    secret = "Told me in confidence about the redundancy plan"

    with pytest.raises(ImportPipelineError) as excinfo:
        harness.stage_batch(
            "planning-meeting",
            [
                _person("alice", "Alice Rivera"),
                _observation(text=secret, evidence_ref=secret),
                _interaction("alice", evidence_ref=secret),
            ],
        )

    reported = f"{excinfo.value}{excinfo.value.details}"
    assert secret not in reported
    assert "evidence_ref" in reported


def test_an_unknown_evidence_reference_is_never_echoed() -> None:
    harness = _Harness()
    secret = "Mentioned the merger over dinner"

    with pytest.raises(ImportPipelineError) as excinfo:
        harness.stage_batch(
            "planning-meeting",
            [
                _person("alice", "Alice Rivera"),
                _observation(evidence_ref="metrics-question"),
                _trait(evidence_refs=[secret]),
            ],
        )

    reported = f"{excinfo.value}{excinfo.value.details}"
    assert secret not in reported
    assert "evidence_refs" in reported


def test_an_untracked_batch_may_not_cite_evidence_staged_beside_it() -> None:
    """Without the M18.1 mapping seam a batch-local citation has no answer after a partial
    commit, so it is refused while the batch can still be declined whole."""
    harness = _Harness()

    with pytest.raises(ImportPipelineError) as excinfo:
        harness.stage_untracked(
            "planning-meeting",
            [
                _person("alice", "Alice Rivera"),
                _observation(evidence_ref="metrics-question"),
                _trait(evidence_refs=["metrics-question"]),
            ],
        )

    assert excinfo.value.code == "evidence_requires_source_tracking"
    assert harness.conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


def test_an_untracked_batch_may_still_cite_durable_records() -> None:
    """A durable id already names the record, so it needs no receipt to be resolvable."""
    harness = _Harness()

    batch = harness.stage_untracked(
        "planning-meeting",
        [_person("alice", "Alice Rivera"), _trait(evidence_ids=["obs-1"])],
    )

    assert _staged(harness, batch.batch_id, "trait")["evidence_ids"] == ["obs-1"]


def test_a_tracked_batch_resolves_a_citation_across_separate_commits() -> None:
    """The regression the refusal above exists to prevent, proven fixed on a tracked batch."""
    harness = _Harness()
    batch = harness.stage_batch(
        "planning-meeting",
        [
            _person("alice", "Alice Rivera"),
            _observation(evidence_ref="metrics-question"),
            _trait(evidence_refs=["metrics-question"]),
        ],
    )
    rows = {row.candidate["type"]: row for row in harness.rows(batch.batch_id)}
    harness.commit.execute(batch.batch_id, [rows["person"].id, rows["observation"].id])

    result = harness.commit.execute(batch.batch_id, [rows["trait"].id])

    assert result.unresolved_ids == []
    assert len(harness.links()) == 1


@pytest.mark.parametrize("field", ["evidence_ref", "evidence_ids"])
def test_an_evidence_token_is_never_rewritten_on_its_way_to_storage(field: str) -> None:
    """Tokens are identities matched exactly, not text: trimming one would make a legitimately
    restored id unciteable, or resolve it to a different record whose id is the trimmed form."""
    harness = _Harness()
    padded = " obs-1 "
    candidates = (
        [_person("alice", "Alice Rivera"), _observation(evidence_ref=padded), _trait(evidence_refs=[padded])]
        if field == "evidence_ref"
        else [_person("alice", "Alice Rivera"), _trait(evidence_ids=[padded])]
    )

    batch = harness.stage_batch("planning-meeting", candidates)

    assert _staged(harness, batch.batch_id, "trait").get("evidence_ids", [padded]) == [padded]


# -- commit: resolution, subject rules, and declining ------------------


def test_evidence_committed_in_the_same_invocation_grounds_the_trait() -> None:
    harness = _Harness()
    batch = harness.stage_batch(
        "planning-meeting",
        [
            _person("alice", "Alice Rivera"),
            # The trait is staged *before* the records it cites, which is exactly what an agent
            # that states its conclusion first produces.
            _trait(evidence_refs=["meeting", "metrics-question"]),
            _observation(evidence_ref="metrics-question"),
            _interaction("alice", evidence_ref="meeting"),
        ],
    )
    rows = harness.rows(batch.batch_id)

    result = harness.commit.execute(batch.batch_id, [row.id for row in rows])

    assert result.unresolved_ids == []
    mappings = harness.committed(batch.batch_id)
    trait_id = mappings[next(row.id for row in rows if row.candidate["type"] == "trait")]
    observation_id = mappings[next(row.id for row in rows if row.candidate["type"] == "observation")]
    interaction_id = mappings[next(row.id for row in rows if row.candidate["type"] == "interaction")]
    assert harness.links() == sorted(
        [(trait_id, "interaction", interaction_id), (trait_id, "observation", observation_id)]
    )


def test_evidence_committed_in_an_earlier_partial_commit_still_grounds_the_trait() -> None:
    harness = _Harness()
    batch = harness.stage_batch(
        "planning-meeting",
        [
            _person("alice", "Alice Rivera"),
            _observation(evidence_ref="metrics-question"),
            _trait(evidence_refs=["metrics-question"]),
        ],
    )
    rows = {row.candidate["type"]: row for row in harness.rows(batch.batch_id)}
    harness.commit.execute(batch.batch_id, [rows["person"].id, rows["observation"].id])

    result = harness.commit.execute(batch.batch_id, [rows["trait"].id])

    assert result.unresolved_ids == []
    mappings = harness.committed(batch.batch_id)
    assert harness.links() == [(mappings[rows["trait"].id], "observation", mappings[rows["observation"].id])]


def test_a_trait_whose_same_batch_evidence_was_not_accepted_stays_unresolved() -> None:
    harness = _Harness()
    batch = harness.stage_batch(
        "planning-meeting",
        [
            _person("alice", "Alice Rivera"),
            _observation(evidence_ref="metrics-question"),
            _trait(evidence_refs=["metrics-question"]),
        ],
    )
    rows = {row.candidate["type"]: row for row in harness.rows(batch.batch_id)}

    result = harness.commit.execute(batch.batch_id, [rows["person"].id, rows["trait"].id])

    assert result.unresolved_ids == [rows["trait"].id]
    assert harness.conn.execute("SELECT COUNT(*) FROM traits").fetchone()[0] == 0
    # And it commits once the evidence does, without re-staging.
    again = harness.commit.execute(batch.batch_id, [rows["observation"].id, rows["trait"].id])
    assert again.unresolved_ids == []
    assert len(harness.links()) == 1


def test_a_stored_mapping_naming_a_record_no_trait_may_cite_leaves_it_unresolved() -> None:
    """Defence in depth at the resolution seam.

    Staging refuses an `evidence_refs` entry naming anything but an observation or interaction,
    and bundle validation refuses one too, so a mapping to another record type should never reach
    here. If a corrupted or hand-edited row does, the trait declines rather than linking to
    something a trait may not rest on.
    """
    harness = _Harness()
    batch = harness.stage_batch(
        "planning-meeting",
        [
            _person("alice", "Alice Rivera"),
            _observation(evidence_ref="metrics-question"),
            _trait(evidence_refs=["metrics-question"]),
        ],
    )
    rows = {row.candidate["type"]: row for row in harness.rows(batch.batch_id)}
    harness.commit.execute(batch.batch_id, [rows["person"].id, rows["observation"].id])
    harness.conn.execute(
        "UPDATE import_candidate_mappings SET entity_type = 'fact' WHERE candidate_id = ?",
        (rows["observation"].id,),
    )
    harness.conn.commit()

    result = harness.commit.execute(batch.batch_id, [rows["trait"].id])

    assert result.unresolved_ids == [rows["trait"].id]
    assert harness.links() == []


def test_an_explicit_durable_evidence_id_is_used_exactly_as_supplied() -> None:
    """A restored non-ULID id is as addressable as a generated one: lookup is exact, not shaped."""
    harness = _Harness()
    first = harness.stage_batch("planning-meeting", [_person("alice", "Alice Rivera"), _observation()])
    rows = harness.rows(first.batch_id)
    harness.commit.execute(first.batch_id, [row.id for row in rows])
    observation_id = harness.conn.execute("SELECT id FROM observations").fetchone()["id"]
    harness.conn.execute("UPDATE observations SET id = 'obs-1' WHERE id = ?", (observation_id,))
    harness.conn.commit()

    second = harness.stage_batch("follow-up", [_person("alice", "Alice Rivera"), _trait(evidence_ids=["obs-1"])])
    second_rows = harness.rows(second.batch_id)
    result = harness.commit.execute(second.batch_id, [row.id for row in second_rows])

    assert result.unresolved_ids == []
    assert [link[1:] for link in harness.links()] == [("observation", "obs-1")]


def test_a_durable_evidence_id_that_does_not_exist_leaves_the_trait_unresolved() -> None:
    harness = _Harness()
    batch = harness.stage_batch(
        "planning-meeting",
        [_person("alice", "Alice Rivera"), _trait(evidence_ids=["01J0000000000000000ABSENT"])],
    )
    rows = {row.candidate["type"]: row for row in harness.rows(batch.batch_id)}

    result = harness.commit.execute(batch.batch_id, [rows["person"].id, rows["trait"].id])

    assert result.unresolved_ids == [rows["trait"].id]
    assert harness.conn.execute("SELECT COUNT(*) FROM traits").fetchone()[0] == 0


def test_an_observation_about_another_person_cannot_ground_this_trait() -> None:
    harness = _Harness()
    batch = harness.stage_batch(
        "planning-meeting",
        [
            _person("alice", "Alice Rivera"),
            _person("bob", "Bob Chen"),
            _observation(person_ref="bob", evidence_ref="bobs-remark"),
            _trait(person_ref="alice", evidence_refs=["bobs-remark"]),
        ],
    )
    rows = harness.rows(batch.batch_id)

    result = harness.commit.execute(batch.batch_id, [row.id for row in rows])

    trait_row = next(row for row in rows if row.candidate["type"] == "trait")
    assert result.unresolved_ids == [trait_row.id]
    assert harness.links() == []


def test_an_interaction_the_subject_did_not_join_cannot_ground_this_trait() -> None:
    harness = _Harness()
    batch = harness.stage_batch(
        "planning-meeting",
        [
            _person("alice", "Alice Rivera"),
            _person("bob", "Bob Chen"),
            _person("cara", "Cara Diaz"),
            _interaction("bob", "cara", evidence_ref="their-meeting"),
            _trait(person_ref="alice", evidence_refs=["their-meeting"]),
        ],
    )
    rows = harness.rows(batch.batch_id)

    result = harness.commit.execute(batch.batch_id, [row.id for row in rows])

    trait_row = next(row for row in rows if row.candidate["type"] == "trait")
    assert result.unresolved_ids == [trait_row.id]
    assert harness.links() == []


def test_a_mapped_citation_resolves_to_the_record_its_candidate_produced() -> None:
    """Ids are unique only within their own table, so a citation carries the type its candidate
    produced. Resolving by id alone would answer with whichever table is consulted first."""
    harness = _Harness()
    batch = harness.stage_batch(
        "planning-meeting",
        [
            _person("alice", "Alice Rivera"),
            _person("bob", "Bob Chen"),
            _interaction("alice", evidence_ref="meeting"),
            _trait(evidence_refs=["meeting"]),
        ],
    )
    rows = {row.candidate["type"]: row for row in harness.rows(batch.batch_id)}
    people = [row for row in harness.rows(batch.batch_id) if row.candidate["type"] == "person"]
    harness.commit.execute(batch.batch_id, [*(row.id for row in people), rows["interaction"].id])
    interaction_id = harness.conn.execute("SELECT id FROM interactions").fetchone()["id"]
    bob = harness.conn.execute("SELECT id FROM persons WHERE canonical_name = 'Bob Chen'").fetchone()["id"]
    # A restored store may legitimately hold an observation sharing that opaque id — and this one
    # is about somebody else, so resolving to it would either mis-ground the trait or strand it.
    harness.conn.execute(
        """INSERT INTO observations (id, person_id, text, observed_at, sensitivity, provenance_source)
           VALUES (?, ?, 'Unrelated', '2026-08-19T10:00:00+00:00', 'personal', 'user')""",
        (interaction_id, bob),
    )
    harness.conn.commit()

    result = harness.commit.execute(batch.batch_id, [rows["trait"].id])

    assert result.unresolved_ids == []
    assert [link[1:] for link in harness.links()] == [("interaction", interaction_id)]


def test_links_are_stored_in_a_stable_order_regardless_of_how_the_agent_listed_them() -> None:
    ordered = _links_for(["meeting", "metrics-question"])
    reversed_request = _links_for(["metrics-question", "meeting"])

    assert [link[1] for link in ordered] == ["interaction", "observation"]
    assert [link[1] for link in ordered] == [link[1] for link in reversed_request]


def _links_for(refs: list[str]) -> list[tuple[str, str, str]]:
    harness = _Harness()
    batch = harness.stage_batch(
        "planning-meeting",
        [
            _person("alice", "Alice Rivera"),
            _observation(evidence_ref="metrics-question"),
            _interaction("alice", evidence_ref="meeting"),
            _trait(evidence_refs=refs),
        ],
    )
    rows = harness.rows(batch.batch_id)
    harness.commit.execute(batch.batch_id, [row.id for row in rows])
    return harness.links()


def test_every_link_shares_the_commit_transaction_of_the_trait_it_grounds() -> None:
    harness = _Harness()
    batch = harness.stage_batch(
        "planning-meeting",
        [
            _person("alice", "Alice Rivera"),
            _observation(evidence_ref="metrics-question"),
            _trait(evidence_refs=["metrics-question"]),
        ],
    )
    rows = harness.rows(batch.batch_id)
    harness.commit.execute(batch.batch_id, [row.id for row in rows])

    # Staging journalled the receipt under its own transaction, so this asks only about the
    # effects one `CommitImport.execute` produced.
    transactions = {
        row["transaction_id"]
        for row in harness.conn.execute(
            """SELECT transaction_id FROM changelog
               WHERE entity_type IN ('person', 'observation', 'trait', 'trait_evidence',
                                     'import_candidate_mapping')"""
        )
    }
    assert len(transactions) == 1
    assert (
        harness.conn.execute("SELECT COUNT(*) FROM changelog WHERE entity_type = 'trait_evidence'").fetchone()[0] == 1
    )


def test_an_unwired_commit_declines_a_trait_that_asks_for_grounding() -> None:
    """A commit with no evidence store must not silently write an ungrounded trait."""
    harness = _Harness()
    unwired = _unwired_commit(harness)
    batch = harness.stage_batch(
        "planning-meeting",
        [
            _person("alice", "Alice Rivera"),
            _observation(evidence_ref="metrics-question"),
            _trait(evidence_refs=["metrics-question"]),
        ],
    )
    rows = {row.candidate["type"]: row for row in harness.rows(batch.batch_id)}

    result = unwired.execute(batch.batch_id, [row.id for row in rows.values()])

    assert result.unresolved_ids == [rows["trait"].id]
    assert harness.conn.execute("SELECT COUNT(*) FROM traits").fetchone()[0] == 0


def _unwired_commit(harness: _Harness) -> CommitImport:
    conn: sqlite3.Connection = harness.conn
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    audit = SqliteAuditLog(conn)
    return CommitImport(
        people,
        SqliteImportStagingStore(conn),
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
