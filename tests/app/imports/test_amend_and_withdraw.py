"""Amending and withdrawing staged candidates (M29.1).

Review's released verbs were accept and ignore. What is checked here is the two that were added
and, mostly, what they *refuse*: an amendment is the reviewer finishing an extraction, so it has
to be held to every rule the extraction itself was held to, and every refusal has to leave the
stored candidate exactly as it was.

Two rules run through all of it. Nothing about an amendment or a withdrawal is a durable write
until commit, so neither audits the staging rows it touches; and no refusal may echo what it
refused, because a patch key and a candidate reference are both untrusted text.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.adapters.sqlite.repository import SqlitePeopleRepository
from people_context.app.imports import (
    MAX_MATCH_CANDIDATE_NAME_CHARS,
    MAX_MATCH_CANDIDATES,
    REDACTED_FIELD,
    AmendStagedCandidate,
    ImportBudget,
    ImportPipelineError,
)
from people_context.domain.person import Person

_NOW = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[ApplicationRuntime]:
    built = build_runtime(tmp_path / "people.db", clock=_Clock())
    yield built
    built.close()


def _person(ref: str, name: str, **overrides: Any) -> dict[str, Any]:
    return {"type": "person", "ref": ref, "name": name, "aliases": [], **overrides}


def _stage(runtime: ApplicationRuntime, candidates: list[dict[str, Any]], *, tracked: bool = True) -> str:
    batch = runtime.use_cases.stage_candidates.execute(
        "review",
        candidates,
        strict_identity=True,
        source_kind="notes" if tracked else None,
    )
    return batch.batch_id


def _rows(runtime: ApplicationRuntime, batch_id: str) -> list[Any]:
    return runtime.use_cases.review_import.execute(batch_id).candidates


def _ids(runtime: ApplicationRuntime, batch_id: str) -> list[str]:
    return [row.id for row in _rows(runtime, batch_id)]


def _candidate(runtime: ApplicationRuntime, batch_id: str, candidate_id: str) -> dict[str, Any]:
    return next(row.candidate for row in _rows(runtime, batch_id) if row.id == candidate_id)


def _simple_batch(runtime: ApplicationRuntime) -> tuple[str, list[str]]:
    batch_id = _stage(
        runtime,
        [
            _person("p1", "Nadia Okonkwo"),
            {"type": "affiliation", "person_ref": "p1", "org": "Acme", "role": "Engineer"},
        ],
    )
    return batch_id, _ids(runtime, batch_id)


def _save_person(runtime: ApplicationRuntime, person_id: str, name: str) -> str:
    """Store one person directly, so several may legitimately share a canonical name."""
    SqlitePeopleRepository(runtime.conn).save_person(
        Person(id=person_id, canonical_name=name, aliases=[], created_at=_NOW, updated_at=_NOW)
    )
    runtime.conn.commit()
    return person_id


# --- amendment: what it accepts ------------------------------------------


def test_an_amendment_replaces_the_named_fields_and_leaves_the_rest_alone(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)

    review = runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"role": "Staff Engineer"})

    amended = next(row.candidate for row in review.candidates if row.id == ids[1])
    assert amended["role"] == "Staff Engineer"
    assert amended["org"] == "Acme"
    assert amended["person_candidate_id"] == ids[0]


def test_an_amendment_returns_the_whole_batch_not_the_row_it_changed(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)

    review = runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"role": "Staff Engineer"})

    assert [row.id for row in review.candidates] == ids


def test_an_amended_row_keeps_its_id_batch_and_position(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)

    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"role": "Staff Engineer"})

    assert _ids(runtime, batch_id) == ids


def test_a_patched_list_replaces_the_stored_list_rather_than_merging_into_it(
    runtime: ApplicationRuntime,
) -> None:
    batch_id = _stage(
        runtime,
        [_person("p1", "Nadia Okonkwo", aliases=[{"value": "nadia@acme.test", "kind": "handle"}])],
    )
    candidate_id = _ids(runtime, batch_id)[0]

    runtime.use_cases.amend_staged_candidate.execute(
        batch_id, candidate_id, {"aliases": [{"value": "N. Okonkwo", "kind": "other"}]}
    )

    aliases = _candidate(runtime, batch_id, candidate_id)["aliases"]
    assert [alias["value"] for alias in aliases] == ["N. Okonkwo"]


def test_an_amendment_writes_no_audit_entry_for_the_staging_row(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)
    before = runtime.conn.execute("SELECT COUNT(*) AS total FROM audit_log").fetchone()["total"]

    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"role": "Staff Engineer"})

    assert runtime.conn.execute("SELECT COUNT(*) AS total FROM audit_log").fetchone()["total"] == before


# --- amendment: what it refuses ------------------------------------------


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"type": "fact"}, "invalid_candidates"),
        ({"unknown_field": "x"}, "invalid_candidates"),
        ({"match_disposition": "matched"}, "invalid_candidates"),
        ({"match_count": 1}, "invalid_candidates"),
        ({"role": ""}, "invalid_candidates"),
    ],
)
def test_an_amendment_breaking_the_candidate_shape_is_refused_and_stores_nothing(
    runtime: ApplicationRuntime, patch: dict[str, Any], code: str
) -> None:
    batch_id, ids = _simple_batch(runtime)
    before = _candidate(runtime, batch_id, ids[1])

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], patch)

    assert raised.value.code == code
    assert _candidate(runtime, batch_id, ids[1]) == before


def test_a_refusal_names_a_declared_field(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"role": ""})

    assert raised.value.details["details"][0]["loc"] == [ids[1], "role"]


def test_a_refusal_redacts_an_undeclared_key_because_it_is_untrusted_text(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, ids = _simple_batch(runtime)
    private = "Alice is being treated at the Mayo Clinic"

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {private: "x"})

    detail = raised.value.details["details"][0]
    assert detail["loc"] == [ids[1], REDACTED_FIELD]
    assert private not in str(raised.value)
    assert private not in str(raised.value.details)


def test_a_patch_restating_the_same_type_is_accepted(runtime: ApplicationRuntime) -> None:
    """`type` is immutable, not unmentionable.

    A round-tripped candidate carries its own discriminator, so refusing a patch that merely
    repeats it would make the obvious "edit this object and send it back" flow fail on every row.
    """
    batch_id, ids = _simple_batch(runtime)

    runtime.use_cases.amend_staged_candidate.execute(
        batch_id, ids[1], {"type": "affiliation", "role": "Staff Engineer"}
    )

    assert _candidate(runtime, batch_id, ids[1])["role"] == "Staff Engineer"


def test_choosing_a_matched_person_on_a_candidate_that_has_no_identity_is_refused(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, ids = _simple_batch(runtime)

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(
            batch_id, ids[1], {"matched_person_id": "01M29PERSONANY0000000000001"}
        )

    assert raised.value.details["details"][0]["loc"] == [ids[1], "matched_person_id"]


def test_amending_a_legacy_row_keeps_the_matcher_it_was_staged_under(
    runtime: ApplicationRuntime,
) -> None:
    """A row staged before the ambiguity-preserving matcher is re-derived the way it was made.

    Re-deriving it with today's matcher would let an amendment silently change an answer nobody
    amended — the same reason commit's own re-match branches on the stored disposition.
    """
    existing = _save_person(runtime, "01M29PERSONLEGACY00000000001", "Tomasz Wozniak")
    batch = runtime.use_cases.stage_candidates.execute(
        "review",
        [_person("p1", "Nadia Okonkwo"), {"type": "fact", "person_ref": "p1", "predicate": "c", "value": "v"}],
        strict_identity=False,
    )
    person_id = _ids(runtime, batch.batch_id)[0]
    assert "match_disposition" not in _candidate(runtime, batch.batch_id, person_id)

    runtime.use_cases.amend_staged_candidate.execute(batch.batch_id, person_id, {"name": "Tomasz Wozniak"})

    amended = _candidate(runtime, batch.batch_id, person_id)
    assert amended["matched_person_id"] == existing
    assert "match_disposition" not in amended


def test_amending_a_committed_row_is_refused_as_not_pending(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)
    runtime.use_cases.commit_import.execute(batch_id, ids)

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"role": "Staff Engineer"})

    assert raised.value.code == "candidate_not_pending"


def test_amending_a_withdrawn_row_is_refused_as_not_pending(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)
    runtime.use_cases.withdraw_staged_candidates.execute(batch_id, [ids[1]])

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"role": "Staff Engineer"})

    assert raised.value.code == "candidate_not_pending"


def test_amending_a_candidate_of_another_batch_is_refused(runtime: ApplicationRuntime) -> None:
    batch_id, _ids_here = _simple_batch(runtime)
    other_id = _ids(runtime, _stage(runtime, [_person("p1", "Tom Vance")]))[0]

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, other_id, {"name": "Tom V."})

    assert raised.value.code == "candidate_not_in_batch"


# --- amendment: the input rules the persisted shape does not enforce -----


def test_a_patched_relationship_type_with_no_word_character_is_refused(runtime: ApplicationRuntime) -> None:
    batch_id = _stage(
        runtime,
        [
            _person("p1", "Nadia Okonkwo"),
            _person("p2", "Tom Vance"),
            {"type": "relationship", "from_ref": "p1", "to_ref": "p2", "relationship_type": "colleague"},
        ],
    )
    relationship_id = _ids(runtime, batch_id)[2]

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, relationship_id, {"relationship_type": "---"})

    assert raised.value.code == "invalid_candidates"
    assert _candidate(runtime, batch_id, relationship_id)["relationship_type"] == "colleague"


def test_a_relationship_amended_onto_itself_is_refused(runtime: ApplicationRuntime) -> None:
    batch_id = _stage(
        runtime,
        [
            _person("p1", "Nadia Okonkwo"),
            _person("p2", "Tom Vance"),
            {"type": "relationship", "from_ref": "p1", "to_ref": "p2", "relationship_type": "colleague"},
        ],
    )
    ids = _ids(runtime, batch_id)

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[2], {"to_candidate_id": ids[0]})

    assert raised.value.details["details"][0]["loc"] == [ids[2], "to_candidate_id"]


def test_a_reversed_validity_period_is_refused_even_though_the_stored_shape_allows_it(
    runtime: ApplicationRuntime,
) -> None:
    batch_id = _stage(
        runtime,
        [_person("p1", "Nadia Okonkwo"), {"type": "fact", "person_ref": "p1", "predicate": "city", "value": "Lisbon"}],
    )
    fact_id = _ids(runtime, batch_id)[1]

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(
            batch_id, fact_id, {"valid_from": "2026-02-01", "valid_to": "2026-01-01"}
        )

    assert raised.value.code == "invalid_candidates"


def test_a_same_batch_evidence_citation_added_to_a_receiptless_batch_is_refused(
    runtime: ApplicationRuntime,
) -> None:
    batch_id = _stage(
        runtime,
        [
            _person("p1", "Nadia Okonkwo"),
            {"type": "observation", "person_ref": "p1", "text": "spoke at the all-hands"},
            {
                "type": "trait",
                "person_ref": "p1",
                "category": "communication_style",
                "value": "direct",
                "evidence_note": "from the all-hands",
                "confidence": 0.6,
            },
        ],
        tracked=False,
    )
    ids = _ids(runtime, batch_id)

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(
            batch_id, ids[2], {"evidence_candidate_ids": [ids[1]]}
        )

    assert raised.value.code == "evidence_requires_source_tracking"


def test_the_same_citation_is_accepted_in_a_source_tracked_batch(runtime: ApplicationRuntime) -> None:
    batch_id = _stage(
        runtime,
        [
            _person("p1", "Nadia Okonkwo"),
            {"type": "observation", "person_ref": "p1", "text": "spoke at the all-hands"},
            {
                "type": "trait",
                "person_ref": "p1",
                "category": "communication_style",
                "value": "direct",
                "evidence_note": "from the all-hands",
                "confidence": 0.6,
            },
        ],
    )
    ids = _ids(runtime, batch_id)

    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[2], {"evidence_candidate_ids": [ids[1]]})

    assert _candidate(runtime, batch_id, ids[2])["evidence_candidate_ids"] == [ids[1]]


def test_an_amendment_pointing_at_a_row_outside_the_batch_is_refused(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, ids = _simple_batch(runtime)
    foreign_id = _ids(runtime, _stage(runtime, [_person("p1", "Tom Vance")]))[0]

    for reference in ("01JUNKUNKNOWNCANDIDATEID00", foreign_id):
        with pytest.raises(ImportPipelineError) as raised:
            runtime.use_cases.amend_staged_candidate.execute(
                batch_id, ids[1], {"person_candidate_id": reference}
            )
        assert raised.value.code == "candidate_reference_invalid"


def test_an_amendment_pointing_at_a_row_of_the_wrong_type_is_refused(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"person_candidate_id": ids[1]})

    assert raised.value.code == "candidate_reference_invalid"


def test_a_trait_citing_a_row_that_is_not_evidence_is_refused(runtime: ApplicationRuntime) -> None:
    """The three reference kinds resolve through three different maps, so each is checked apart."""
    batch_id = _stage(
        runtime,
        [
            _person("p1", "Nadia Okonkwo"),
            {"type": "observation", "person_ref": "p1", "text": "spoke at the all-hands"},
            {
                "type": "trait",
                "person_ref": "p1",
                "category": "communication_style",
                "value": "direct",
                "evidence_note": "from the all-hands",
                "confidence": 0.6,
            },
        ],
    )
    ids = _ids(runtime, batch_id)

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(
            batch_id, ids[2], {"evidence_candidate_ids": [ids[0]]}
        )

    assert raised.value.code == "candidate_reference_invalid"


def test_a_membership_placed_in_a_row_that_is_not_a_group_is_refused(
    runtime: ApplicationRuntime,
) -> None:
    batch_id = _stage(
        runtime,
        [
            _person("p1", "Nadia Okonkwo"),
            {"type": "group", "ref": "class-1", "name": "Class 1, Grade 6", "kind": "class"},
            {"type": "group_membership", "person_ref": "p1", "group_ref": "class-1"},
        ],
    )
    ids = _ids(runtime, batch_id)

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(
            batch_id, ids[2], {"group_candidate_id": ids[0]}
        )

    assert raised.value.code == "candidate_reference_invalid"


def test_a_dependent_may_still_reference_a_withdrawn_row(runtime: ApplicationRuntime) -> None:
    """A withdrawn row stays a legal target: its dependents go unresolved, not unexplainable."""
    batch_id, ids = _simple_batch(runtime)
    runtime.use_cases.withdraw_staged_candidates.execute(batch_id, [ids[0]])

    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"role": "Staff Engineer"})

    assert _candidate(runtime, batch_id, ids[1])["person_candidate_id"] == ids[0]


def test_a_reference_refusal_reports_a_count_rather_than_the_reference_itself(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, ids = _simple_batch(runtime)
    invented = "Nadia's private clinic id"

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"person_candidate_id": invented})

    assert raised.value.details["invalid_reference_count"] == 1
    assert invented not in str(raised.value.details)


def test_an_oversized_patch_string_is_refused_before_anything_is_written(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, ids = _simple_batch(runtime)
    before = _candidate(runtime, batch_id, ids[1])

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"role": "x" * (9 * 1024)})

    assert raised.value.code == "candidate_string_too_long"
    assert _candidate(runtime, batch_id, ids[1]) == before


# --- amendment: choosing between people who share a name ------------------


def _ambiguous_batch(runtime: ApplicationRuntime, collisions: int) -> tuple[str, list[str], list[str]]:
    people = [
        _save_person(runtime, f"01M29PERSON{index:015d}", "Priya Sharma") for index in range(collisions)
    ]
    batch_id = _stage(
        runtime,
        [
            _person("p1", "Priya Sharma"),
            {"type": "fact", "person_ref": "p1", "predicate": "city", "value": "Berlin"},
        ],
    )
    return batch_id, _ids(runtime, batch_id), people


def test_an_ambiguous_person_row_lists_the_people_it_could_be(runtime: ApplicationRuntime) -> None:
    batch_id, ids, people = _ambiguous_batch(runtime, collisions=3)

    row = next(row for row in _rows(runtime, batch_id) if row.id == ids[0])

    assert row.candidate["match_disposition"] == "ambiguous"
    assert [match.id for match in row.match_candidates or []] == sorted(people)
    assert row.match_candidates_truncated is False


def test_a_row_that_is_not_ambiguous_lists_nothing_rather_than_an_empty_list(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, ids = _simple_batch(runtime)

    rows = {row.id: row for row in _rows(runtime, batch_id)}

    assert rows[ids[0]].match_candidates is None
    assert rows[ids[1]].match_candidates is None


def test_a_long_collision_list_is_capped_and_says_so_while_the_choice_is_not_capped(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, ids, people = _ambiguous_batch(runtime, collisions=25)

    row = next(row for row in _rows(runtime, batch_id) if row.id == ids[0])
    assert len(row.match_candidates or []) == MAX_MATCH_CANDIDATES
    assert row.match_candidates_truncated is True

    beyond_the_cap = sorted(people)[-1]
    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"matched_person_id": beyond_the_cap})

    assert _candidate(runtime, batch_id, ids[0])["matched_person_id"] == beyond_the_cap


def test_a_projected_name_is_cut_and_flagged_rather_than_rendered_whole(
    runtime: ApplicationRuntime,
) -> None:
    long_name = "Priya " + "x" * 400
    _save_person(runtime, "01M29PERSONLONGNAME000000001", long_name)
    _save_person(runtime, "01M29PERSONLONGNAME000000002", long_name)
    batch_id = _stage(runtime, [_person("p1", long_name)])

    row = _rows(runtime, batch_id)[0]

    assert row.match_candidates is not None
    for match in row.match_candidates:
        assert len(match.canonical_name) == MAX_MATCH_CANDIDATE_NAME_CHARS
        assert match.name_truncated is True


def test_choosing_a_person_the_candidate_does_not_resolve_to_is_refused(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, ids, _people = _ambiguous_batch(runtime, collisions=3)
    stranger = _save_person(runtime, "01M29PERSONSTRANGER000000001", "Tom Vance")

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"matched_person_id": stranger})

    assert raised.value.code == "person_not_a_match"
    assert _candidate(runtime, batch_id, ids[0])["match_disposition"] == "ambiguous"


def test_a_chosen_person_resolves_the_row_and_its_dependents_commit_to_them(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, ids, people = _ambiguous_batch(runtime, collisions=3)
    chosen = sorted(people)[1]

    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"matched_person_id": chosen})
    result = runtime.use_cases.commit_import.execute(batch_id, ids)

    assert result.unresolved_ids == []
    facts = runtime.conn.execute("SELECT person_id FROM facts").fetchall()
    assert [row["person_id"] for row in facts] == [chosen]


def test_an_ambiguous_row_amended_without_a_choice_stays_ambiguous(runtime: ApplicationRuntime) -> None:
    batch_id, ids, _people = _ambiguous_batch(runtime, collisions=3)

    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"summary": "met at the conference"})
    result = runtime.use_cases.commit_import.execute(batch_id, ids)

    assert _candidate(runtime, batch_id, ids[0])["match_disposition"] == "ambiguous"
    assert set(result.unresolved_ids) == set(ids)


def test_amending_the_name_re_runs_matching_on_the_amended_values(runtime: ApplicationRuntime) -> None:
    unique = _save_person(runtime, "01M29PERSONUNIQUE00000000001", "Tomasz Wozniak")
    batch_id, ids, _people = _ambiguous_batch(runtime, collisions=3)

    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"name": "Tomasz Wozniak"})

    amended = _candidate(runtime, batch_id, ids[0])
    assert amended["match_disposition"] == "matched"
    assert amended["matched_person_id"] == unique


# --- withdrawal -----------------------------------------------------------


def test_a_withdrawn_candidate_stays_listed_as_rejected(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)

    review = runtime.use_cases.withdraw_staged_candidates.execute(batch_id, [ids[1]])

    assert [(row.id, row.status) for row in review.candidates] == [(ids[0], "pending"), (ids[1], "rejected")]


def test_withdrawing_several_candidates_is_one_transaction(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)

    with pytest.raises(ImportPipelineError):
        runtime.use_cases.withdraw_staged_candidates.execute(batch_id, [ids[0], "01JUNKNOTINTHISBATCH000000"])

    assert [row.status for row in _rows(runtime, batch_id)] == ["pending", "pending"]


def test_withdrawing_a_committed_candidate_is_refused(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)
    runtime.use_cases.commit_import.execute(batch_id, ids)

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.withdraw_staged_candidates.execute(batch_id, [ids[0]])

    assert raised.value.code == "candidate_not_pending"


def test_a_withdrawn_candidate_is_skipped_by_commit_everything(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)
    runtime.use_cases.withdraw_staged_candidates.execute(batch_id, [ids[1]])

    result = runtime.use_cases.commit_import.execute(batch_id, [ids[0]])

    assert result.committed_ids == [ids[0]]


def test_naming_a_withdrawn_candidate_refuses_the_whole_commit(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)
    runtime.use_cases.withdraw_staged_candidates.execute(batch_id, [ids[1]])

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.commit_import.execute(batch_id, ids)

    assert raised.value.code == "candidate_withdrawn"
    assert runtime.conn.execute("SELECT COUNT(*) AS total FROM persons").fetchone()["total"] == 0


def test_a_withdrawn_person_resolves_nothing_even_when_it_matched_an_existing_person(
    runtime: ApplicationRuntime,
) -> None:
    existing = _save_person(runtime, "01M29PERSONEXISTING000000001", "Nadia Okonkwo")
    batch_id, ids = _simple_batch(runtime)
    assert _candidate(runtime, batch_id, ids[0])["matched_person_id"] == existing

    runtime.use_cases.withdraw_staged_candidates.execute(batch_id, [ids[0]])
    result = runtime.use_cases.commit_import.execute(batch_id, [ids[1]])

    assert result.committed_ids == []
    assert result.unresolved_ids == [ids[1]]


def test_rerunning_commit_everything_still_reports_the_rows_an_earlier_pass_committed(
    runtime: ApplicationRuntime,
) -> None:
    batch_id = _stage(
        runtime,
        [
            _person("p1", "Nadia Okonkwo"),
            {"type": "affiliation", "person_ref": "p1", "org": "Acme", "role": "Engineer"},
            {"type": "fact", "person_ref": "p1", "predicate": "city", "value": "Lisbon"},
        ],
    )
    ids = _ids(runtime, batch_id)
    runtime.use_cases.withdraw_staged_candidates.execute(batch_id, [ids[2]])
    runtime.use_cases.commit_import.execute(batch_id, [ids[0]])

    result = runtime.use_cases.commit_import.execute(batch_id, [ids[0], ids[1]])

    assert result.skipped_ids == [ids[0]]
    assert result.committed_ids == [ids[1]]


# --- the receipt a withdrawal can move ------------------------------------


def _receipt_status(runtime: ApplicationRuntime, batch_id: str) -> str:
    row = runtime.conn.execute(
        "SELECT status FROM import_source_sessions WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    return str(row["status"])


def test_withdrawing_every_candidate_makes_the_receipt_terminally_withdrawn(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, ids = _simple_batch(runtime)

    runtime.use_cases.withdraw_staged_candidates.execute(batch_id, ids)

    assert _receipt_status(runtime, batch_id) == "withdrawn"


def test_withdrawing_the_last_pending_row_of_a_partly_committed_batch_makes_it_committed(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, ids = _simple_batch(runtime)
    runtime.use_cases.commit_import.execute(batch_id, [ids[0]])

    runtime.use_cases.withdraw_staged_candidates.execute(batch_id, [ids[1]])

    assert _receipt_status(runtime, batch_id) == "committed"


def test_withdrawing_one_of_several_pending_rows_leaves_the_receipt_alone(
    runtime: ApplicationRuntime,
) -> None:
    batch_id = _stage(
        runtime,
        [
            _person("p1", "Nadia Okonkwo"),
            {"type": "affiliation", "person_ref": "p1", "org": "Acme", "role": "Engineer"},
        ],
    )
    before = runtime.conn.execute("SELECT COUNT(*) AS total FROM audit_log").fetchone()["total"]

    runtime.use_cases.withdraw_staged_candidates.execute(batch_id, [_ids(runtime, batch_id)[1]])

    assert _receipt_status(runtime, batch_id) == "staged"
    assert runtime.conn.execute("SELECT COUNT(*) AS total FROM audit_log").fetchone()["total"] == before


def test_a_receipt_transition_is_journalled_through_the_ordinary_mutation_seam(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, ids = _simple_batch(runtime)

    runtime.use_cases.withdraw_staged_candidates.execute(batch_id, ids)

    entries = runtime.conn.execute(
        "SELECT payload_json AS payload FROM audit_log WHERE entity_type = 'import_source_session' AND op = 'update'"
    ).fetchall()
    assert len(entries) == 1
    assert '"withdrawn"' in entries[0]["payload"]
    changelog = runtime.conn.execute(
        "SELECT payload_json AS payload FROM changelog WHERE entity_type = 'import_source_session'"
    ).fetchall()
    assert any('"withdrawn"' in row["payload"] for row in changelog)


def test_restaging_a_withdrawn_source_reports_nothing_left_to_review(
    runtime: ApplicationRuntime,
) -> None:
    candidates = [_person("p1", "Nadia Okonkwo")]
    first = runtime.use_cases.stage_candidates.execute(
        "review", candidates, strict_identity=True, source_kind="notes", content_digest="a" * 64
    )
    runtime.use_cases.withdraw_staged_candidates.execute(first.batch_id, _ids(runtime, first.batch_id))

    again = runtime.use_cases.stage_candidates.execute(
        "review", candidates, strict_identity=True, source_kind="notes", content_digest="a" * 64
    )

    assert again.duplicate is True
    assert again.reviewable is False


# --- the digest that closes the gap between showing and acting ------------


def test_the_digest_changes_when_the_batch_changes_and_not_otherwise(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, ids = _simple_batch(runtime)
    first = runtime.use_cases.review_import.execute(batch_id).batch_digest

    assert runtime.use_cases.review_import.execute(batch_id).batch_digest == first

    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"role": "Staff Engineer"})
    assert runtime.use_cases.review_import.execute(batch_id).batch_digest != first


def test_the_digest_ignores_the_read_time_match_projection(runtime: ApplicationRuntime) -> None:
    batch_id, ids, _people = _ambiguous_batch(runtime, collisions=2)
    before = runtime.use_cases.review_import.execute(batch_id).batch_digest

    _save_person(runtime, "01M29PERSONLATECOMER00000001", "Priya Sharma")

    review = runtime.use_cases.review_import.execute(batch_id)
    assert len(review.candidates[0].match_candidates or []) == 3
    assert review.batch_digest == before


@pytest.mark.parametrize("verb", ["amend", "withdraw", "commit"])
def test_a_stale_digest_refuses_every_verb_and_writes_nothing(
    runtime: ApplicationRuntime, verb: str
) -> None:
    batch_id, ids = _simple_batch(runtime)
    stale = runtime.use_cases.review_import.execute(batch_id).batch_digest
    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"role": "Staff Engineer"})
    use_cases = runtime.use_cases
    call = {
        "amend": lambda: use_cases.amend_staged_candidate.execute(
            batch_id, ids[1], {"role": "Principal"}, expected_batch_digest=stale
        ),
        "withdraw": lambda: use_cases.withdraw_staged_candidates.execute(
            batch_id, [ids[1]], expected_batch_digest=stale
        ),
        "commit": lambda: use_cases.commit_import.execute(batch_id, ids, expected_batch_digest=stale),
    }[verb]

    with pytest.raises(ImportPipelineError) as raised:
        call()

    assert raised.value.code == "batch_changed"
    assert [row.status for row in _rows(runtime, batch_id)] == ["pending", "pending"]
    assert _candidate(runtime, batch_id, ids[1])["role"] == "Staff Engineer"


def test_omitting_the_digest_keeps_the_released_behaviour(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)
    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"role": "Staff Engineer"})

    result = runtime.use_cases.commit_import.execute(batch_id, ids)

    assert result.committed_ids == ids


def test_a_current_digest_is_accepted(runtime: ApplicationRuntime) -> None:
    batch_id, ids = _simple_batch(runtime)
    digest = runtime.use_cases.review_import.execute(batch_id).batch_digest

    result = runtime.use_cases.commit_import.execute(batch_id, ids, expected_batch_digest=digest)

    assert result.committed_ids == ids


# --- regressions from review -------------------------------------------------


def test_an_unrelated_patch_does_not_discard_an_explicit_identity_choice(
    runtime: ApplicationRuntime,
) -> None:
    """Regression: correcting a summary re-ran the matcher and reverted the row to ambiguous.

    "Choose Priya, then fix her summary" is the supported sequence, and re-deriving the match on
    a name that still collides silently threw away the one decision the field exists to record.
    """
    batch_id, ids, people = _ambiguous_batch(runtime, collisions=3)
    chosen = sorted(people)[1]
    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"matched_person_id": chosen})

    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"summary": "met at the conference"})

    amended = _candidate(runtime, batch_id, ids[0])
    assert amended["match_disposition"] == "matched"
    assert amended["matched_person_id"] == chosen


def test_amending_the_name_still_re_runs_the_matcher(runtime: ApplicationRuntime) -> None:
    """The other half of the same rule: an identity-bearing field does reopen the question."""
    batch_id, ids, people = _ambiguous_batch(runtime, collisions=3)
    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"matched_person_id": sorted(people)[0]})
    _save_person(runtime, "01M29PERSONUNIQUENAME0000001", "Tomasz Wozniak")

    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"name": "Tomasz Wozniak"})

    assert _candidate(runtime, batch_id, ids[0])["matched_person_id"] == "01M29PERSONUNIQUENAME0000001"


def test_identity_cannot_move_once_a_dependent_has_committed_against_it(
    runtime: ApplicationRuntime,
) -> None:
    """Regression: a committed record was left on one person while the batch named another.

    Commit resolves a dependent through its person candidate's *stored* match whether or not the
    person row has itself committed, so one accepted fact pins the identity. Letting it move
    afterwards split a single batch's records across two people with nothing saying so.
    """
    batch_id = _stage(
        runtime,
        [
            _person("p1", "Priya Sharma"),
            {"type": "fact", "person_ref": "p1", "predicate": "city", "value": "Berlin"},
            {"type": "fact", "person_ref": "p1", "predicate": "role", "value": "Designer"},
        ],
    )
    people = [_save_person(runtime, f"01M29LOCKPERSON{index:012d}", "Priya Sharma") for index in range(2)]
    ids = _ids(runtime, batch_id)
    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"matched_person_id": people[0]})
    runtime.use_cases.commit_import.execute(batch_id, [ids[1]])

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"matched_person_id": people[1]})

    assert raised.value.code == "identity_already_committed"
    runtime.use_cases.commit_import.execute(batch_id, [ids[2]])
    stored = runtime.conn.execute("SELECT DISTINCT person_id FROM facts").fetchall()
    assert [row["person_id"] for row in stored] == [people[0]]


def test_a_locked_identity_still_accepts_an_amendment_that_does_not_move_it(
    runtime: ApplicationRuntime,
) -> None:
    """Only a retarget is refused; re-affirming the same person, or editing anything else, is not."""
    batch_id = _stage(
        runtime,
        [
            _person("p1", "Priya Sharma"),
            {"type": "fact", "person_ref": "p1", "predicate": "city", "value": "Berlin"},
        ],
    )
    people = [_save_person(runtime, f"01M29KEEPPERSON{index:012d}", "Priya Sharma") for index in range(2)]
    ids = _ids(runtime, batch_id)
    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"matched_person_id": people[0]})
    runtime.use_cases.commit_import.execute(batch_id, [ids[1]])

    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"matched_person_id": people[0]})
    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"summary": "unchanged identity"})

    assert _candidate(runtime, batch_id, ids[0])["matched_person_id"] == people[0]


@pytest.mark.parametrize("aliases", [None, ["not-an-alias"], [{"kind": "handle"}]])
def test_a_malformed_alias_patch_refuses_instead_of_raising(
    runtime: ApplicationRuntime, aliases: object
) -> None:
    """Regression: matching consumed `aliases` before the shape was checked, so these crashed.

    A `TypeError` or `AttributeError` escaping here reaches the CLI as a traceback and MCP as a
    tool failure, where both promise an atomic structured refusal.
    """
    batch_id, ids = _simple_batch(runtime)
    before = _candidate(runtime, batch_id, ids[0])

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"aliases": aliases})

    assert raised.value.code == "invalid_candidates"
    assert _candidate(runtime, batch_id, ids[0]) == before


def test_review_is_unbounded_by_default_and_bounded_only_when_a_caller_asks(
    runtime: ApplicationRuntime,
) -> None:
    """Regression: the shared use case defaulted to the CLI ceiling, narrowing the MCP contract.

    `review_import` shipped with an unbounded read contract that `docs/import.md` states
    explicitly, and one instance serves both boundaries.
    """
    batch_id, _ids_here = _simple_batch(runtime)
    tiny = ImportBudget(max_staged_payload_bytes=1)

    assert runtime.use_cases.review_import.execute(batch_id).candidates

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.review_import.execute(batch_id, budget=tiny)
    assert raised.value.code == "staged_payload_too_large"


def test_the_review_ceiling_counts_the_bytes_a_projected_person_id_really_costs(
    runtime: ApplicationRuntime,
) -> None:
    """Regression: the charge assumed a bounded person id, and nothing bounds one.

    A restored `Identifier` is any non-blank string, and the projection renders it whole because
    a truncated id would select nobody, so a worst-case allowance alone let the rendered review
    exceed the very ceiling it was supposed to enforce.
    """
    long_id = "X" * 200_000
    _save_person(runtime, long_id, "Priya Sharma")
    _save_person(runtime, "01M29SHORTIDPERSON0000000001", "Priya Sharma")
    batch_id = _stage(runtime, [_person("p1", "Priya Sharma")])
    # A ceiling the worst-case allowance clears comfortably, and the real projection does not.
    budget = ImportBudget(max_staged_payload_bytes=64 * 1024)

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.review_import.execute(batch_id, budget=budget)

    assert raised.value.code == "staged_payload_too_large"


def test_an_amendment_persists_the_normalized_value_not_the_raw_patch(
    runtime: ApplicationRuntime,
) -> None:
    """Regression: validation normalized a discarded copy while the raw patch was stored.

    Staging persists the model's own dump, so a padded name never reaches storage. Amendment
    validated and then wrote what arrived, so an amended, unmatched person committed under a
    visibly padded canonical name.
    """
    batch_id = _stage(runtime, [_person("p1", "Ada Lovelace")])
    candidate_id = _ids(runtime, batch_id)[0]

    runtime.use_cases.amend_staged_candidate.execute(
        batch_id,
        candidate_id,
        {"name": "  Ada Byron  ", "aliases": [{"value": "  AB  ", "kind": "other"}]},
    )

    amended = _candidate(runtime, batch_id, candidate_id)
    assert amended["name"] == "Ada Byron"
    assert amended["aliases"][0]["value"] == "AB"
    runtime.use_cases.commit_import.execute(batch_id, [candidate_id])
    stored = runtime.conn.execute("SELECT canonical_name FROM persons").fetchone()
    assert stored["canonical_name"] == "Ada Byron"


def test_re_dumping_an_amended_person_keeps_its_present_and_null_match_fields(
    runtime: ApplicationRuntime,
) -> None:
    """A person row holds `matched_person_id` as present-and-null, which `exclude_none` would drop.

    The field's absence and its null are different states to every reader of a staged row, so
    normalizing the candidate must not quietly change which one it is in.
    """
    batch_id = _stage(runtime, [_person("p1", "Ada Lovelace")])
    candidate_id = _ids(runtime, batch_id)[0]
    assert _candidate(runtime, batch_id, candidate_id)["matched_person_id"] is None

    runtime.use_cases.amend_staged_candidate.execute(batch_id, candidate_id, {"summary": "a note"})

    amended = _candidate(runtime, batch_id, candidate_id)
    assert "matched_person_id" in amended
    assert amended["matched_person_id"] is None
    assert amended["match_disposition"] == "unmatched"


def test_a_patch_that_is_not_an_object_is_refused(runtime: ApplicationRuntime) -> None:
    """An in-process caller is a trust boundary too; the CLI and MCP shapes stop short of here."""
    batch_id, ids = _simple_batch(runtime)

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], ["role", "Staff"])  # type: ignore[arg-type]

    assert raised.value.code == "invalid_candidates"


def test_a_patch_over_the_payload_ceiling_is_refused_before_anything_is_written(
    runtime: ApplicationRuntime,
) -> None:
    """The ceiling is on the whole patch, not only on any single string inside it."""
    batch_id, ids = _simple_batch(runtime)
    # Many small values rather than one large one, so the per-string bound is not what refuses it.
    patch = {f"field_{index}": "x" * 512 for index in range(4096)}

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], patch)

    assert raised.value.code == "candidate_payload_too_large"


def test_an_amendment_that_would_outgrow_the_review_ceiling_is_refused(
    runtime: ApplicationRuntime,
) -> None:
    """An amendment must not grow a batch past what review and commit can read.

    Nothing else could repair it afterwards: every bounded command would refuse the batch it
    produced.
    """
    batch_id, ids = _simple_batch(runtime)
    editor = AmendStagedCandidate(
        runtime.import_staging,
        runtime.use_cases.review_import,
        runtime.repo,
        budget=ImportBudget(max_staged_payload_bytes=256),
    )

    with pytest.raises(ImportPipelineError) as raised:
        editor.execute(batch_id, ids[1], {"role": "Staff Engineer of " + "x" * 512})

    assert raised.value.code == "staged_payload_too_large"
    assert _candidate(runtime, batch_id, ids[1])["role"] == "Engineer"


def test_amending_a_legacy_row_onto_a_name_nobody_has_clears_its_match(
    runtime: ApplicationRuntime,
) -> None:
    batch = runtime.use_cases.stage_candidates.execute(
        "review",
        [_person("p1", "Nadia Okonkwo"), {"type": "fact", "person_ref": "p1", "predicate": "c", "value": "v"}],
        strict_identity=False,
    )
    person_id = _ids(runtime, batch.batch_id)[0]

    runtime.use_cases.amend_staged_candidate.execute(batch.batch_id, person_id, {"name": "Nobody At All"})

    assert _candidate(runtime, batch.batch_id, person_id)["matched_person_id"] is None


def test_withdrawing_nothing_is_refused_rather_than_silently_doing_nothing(
    runtime: ApplicationRuntime,
) -> None:
    batch_id, _ids_here = _simple_batch(runtime)

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.withdraw_staged_candidates.execute(batch_id, [])

    assert raised.value.code == "invalid_candidates"


@pytest.mark.parametrize(
    "use_case", ["amend_staged_candidate", "withdraw_staged_candidates", "review_import"]
)
def test_every_edit_surface_reports_an_unknown_batch_the_same_way(
    runtime: ApplicationRuntime, use_case: str
) -> None:
    arguments: dict[str, list[object]] = {
        "amend_staged_candidate": ["01JUNKNOTABATCH0000000000", "c1", {}],
        "withdraw_staged_candidates": ["01JUNKNOTABATCH0000000000", ["c1"]],
        "review_import": ["01JUNKNOTABATCH0000000000"],
    }

    with pytest.raises(ImportPipelineError) as raised:
        getattr(runtime.use_cases, use_case).execute(*arguments[use_case])

    assert raised.value.code == "batch_not_found"


def test_a_refusal_inside_a_patched_alias_names_the_field_it_reached(
    runtime: ApplicationRuntime,
) -> None:
    """A validation location mixes field names with list indexes; only the names may print."""
    batch_id = _stage(runtime, [_person("p1", "Nadia Okonkwo")])
    candidate_id = _ids(runtime, batch_id)[0]

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(
            batch_id, candidate_id, {"aliases": [{"value": "ok"}, {"value": ""}]}
        )

    assert raised.value.details["details"][0]["loc"] == [candidate_id, "aliases"]


def test_a_nickname_only_alias_patch_keeps_an_explicit_identity_choice(
    runtime: ApplicationRuntime,
) -> None:
    """Regression: any `aliases` patch re-ran matching, though only handles are matched on.

    Adding a nickname to a person the reviewer had already resolved put the row back to
    `ambiguous` and left its dependents unresolved.
    """
    batch_id, ids, people = _ambiguous_batch(runtime, collisions=3)
    chosen = sorted(people)[2]
    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"matched_person_id": chosen})

    runtime.use_cases.amend_staged_candidate.execute(
        batch_id, ids[0], {"aliases": [{"value": "Pri", "kind": "nickname"}]}
    )

    amended = _candidate(runtime, batch_id, ids[0])
    assert amended["match_disposition"] == "matched"
    assert amended["matched_person_id"] == chosen


def test_a_handle_alias_patch_still_re_runs_matching(runtime: ApplicationRuntime) -> None:
    """The other half of the rule: a handle is an identity token, so adding one reopens the match."""
    batch_id, ids, people = _ambiguous_batch(runtime, collisions=3)
    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"matched_person_id": sorted(people)[0]})

    runtime.use_cases.amend_staged_candidate.execute(
        batch_id, ids[0], {"aliases": [{"value": "priya@acme.test", "kind": "handle"}]}
    )

    assert _candidate(runtime, batch_id, ids[0])["match_disposition"] == "ambiguous"


def test_a_spelling_change_that_normalizes_to_the_same_name_keeps_the_choice(
    runtime: ApplicationRuntime,
) -> None:
    """Tokens are compared normalized, as the matcher compares them: case is not a new identity."""
    batch_id, ids, people = _ambiguous_batch(runtime, collisions=3)
    chosen = sorted(people)[1]
    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"matched_person_id": chosen})

    runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[0], {"name": "PRIYA SHARMA"})

    assert _candidate(runtime, batch_id, ids[0])["matched_person_id"] == chosen


def test_an_interaction_cannot_be_amended_down_to_nobody(runtime: ApplicationRuntime) -> None:
    """Regression: the empty list persisted, then commit raised a validation traceback."""
    batch_id = _stage(
        runtime,
        [
            _person("p1", "Tom Vance"),
            {"type": "interaction", "summary": "call", "participant_refs": ["p1"], "date": "2026-09-01T10:00:00Z"},
        ],
    )
    ids = _ids(runtime, batch_id)
    before = _candidate(runtime, batch_id, ids[1])

    with pytest.raises(ImportPipelineError) as raised:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, ids[1], {"participant_candidate_ids": []})

    assert raised.value.details["details"][0]["loc"] == [ids[1], "participant_candidate_ids"]
    assert _candidate(runtime, batch_id, ids[1]) == before
