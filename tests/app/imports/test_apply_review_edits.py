"""Applying an edited review document to its batch (M29.3).

The document is diffed by a pure function and applied by one write-locked use case. What is
checked here is the all-or-nothing promise — a refusal anywhere writes nothing — the fields a
document may not change, and the rendered-expansion constant the read bound is derived from.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.app.imports import (
    MAX_MATCH_CANDIDATE_NAME_CHARS,
    MAX_MATCH_CANDIDATES,
    RENDERED_EXPANSION,
    ImportBudget,
    ImportPipelineError,
    MatchCandidate,
    edited_document_read_bound,
    import_review_document,
    render_import_json,
    review_document_edits,
)
from people_context.app.imports.documents import ImportReviewCandidateEntry, ImportReviewDocument
from people_context.app.imports.review_edits import (
    RENDERED_INDENT,
    RENDERED_MATCH_ENTRY_OVERHEAD,
    RENDERED_MATCH_LIST_OVERHEAD,
    RENDERED_MAX_DEPTH,
)
from people_context.app.imports.workflow import ApplyReviewEdits

_NOW = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[ApplicationRuntime]:
    built = build_runtime(tmp_path / "people.db", clock=_Clock())
    yield built
    built.close()


def _stage(runtime: ApplicationRuntime, *, tracked: bool = True) -> str:
    candidates: list[dict[str, Any]] = [{"type": "person", "ref": "p1", "name": "Nadia Okonkwo", "aliases": []}]
    candidates += [
        {"type": "fact", "person_ref": "p1", "predicate": f"likes{n}", "value": f"thing {n}"} for n in range(5)
    ]
    return runtime.use_cases.stage_candidates.execute(
        "review", candidates, strict_identity=True, source_kind="notes" if tracked else None
    ).batch_id


def _document(runtime: ApplicationRuntime, batch_id: str) -> dict[str, Any]:
    return import_review_document(runtime.use_cases.review_import.execute(batch_id)).model_dump(mode="json")


def _apply(runtime: ApplicationRuntime, rendered: dict[str, Any], edited: Any) -> Any:
    edits = review_document_edits(rendered, edited)
    return runtime.use_cases.apply_review_edits.execute(
        rendered["batch_id"], edits.amendments, edits.withdrawals, expected_batch_digest=rendered["batch_digest"]
    )


def _receipt_status(runtime: ApplicationRuntime, batch_id: str) -> str:
    row = runtime.conn.execute("SELECT status FROM import_source_sessions WHERE batch_id = ?", (batch_id,)).fetchone()
    return str(row["status"])


# --- the diff ---------------------------------------------------------------


def test_an_unchanged_document_expresses_no_edits(runtime: ApplicationRuntime) -> None:
    rendered = _document(runtime, _stage(runtime))

    edits = review_document_edits(rendered, copy.deepcopy(rendered))

    assert edits.amendments == {} and edits.withdrawals == []


def test_a_removed_row_is_a_withdrawal_and_a_changed_candidate_is_a_patch(runtime: ApplicationRuntime) -> None:
    rendered = _document(runtime, _stage(runtime))
    edited = copy.deepcopy(rendered)
    removed = edited["candidates"].pop(2)
    edited["candidates"][1]["candidate"]["value"] = "tea"
    del edited["candidates"][1]["candidate"]["sensitivity"]

    edits = review_document_edits(rendered, edited)

    assert edits.withdrawals == [removed["id"]]
    assert edits.amendments == {edited["candidates"][1]["id"]: {"value": "tea", "sensitivity": None}}


@pytest.mark.parametrize(
    ("field", "value"),
    [("status", "rejected"), ("source", "elsewhere"), ("ordinal", 99), ("match_candidates", []), ("id", "other")],
)
def test_a_changed_row_field_other_than_candidate_refuses_naming_index_and_field(
    runtime: ApplicationRuntime, field: str, value: Any
) -> None:
    rendered = _document(runtime, _stage(runtime))
    edited = copy.deepcopy(rendered)
    edited["candidates"][3][field] = value

    with pytest.raises(ImportPipelineError) as raised:
        review_document_edits(rendered, edited)

    assert raised.value.code == "review_field_changed"
    assert raised.value.details == {"index": 3, "field": field}
    assert f"candidates[3].{field}" in str(raised.value)


def test_an_invented_row_key_is_redacted(runtime: ApplicationRuntime) -> None:
    rendered = _document(runtime, _stage(runtime))
    edited = copy.deepcopy(rendered)
    edited["candidates"][0]["Nadia's secret"] = True

    with pytest.raises(ImportPipelineError) as raised:
        review_document_edits(rendered, edited)

    assert raised.value.code == "review_field_changed"
    assert "secret" not in str(raised.value)
    assert raised.value.details["field"] == "(redacted)"


def test_a_document_naming_another_batch_refuses_whole(runtime: ApplicationRuntime) -> None:
    rendered = _document(runtime, _stage(runtime))
    foreign = _document(runtime, _stage(runtime, tracked=False))

    with pytest.raises(ImportPipelineError) as raised:
        review_document_edits(rendered, foreign)

    assert raised.value.code == "review_field_changed"
    assert "document.batch_id" in str(raised.value)


def test_an_added_row_refuses(runtime: ApplicationRuntime) -> None:
    rendered = _document(runtime, _stage(runtime))
    edited = copy.deepcopy(rendered)
    edited["candidates"].append(copy.deepcopy(edited["candidates"][1]))

    with pytest.raises(ImportPipelineError) as raised:
        review_document_edits(rendered, edited)

    assert raised.value.code == "candidate_not_in_batch"


def test_a_changed_digest_refuses_as_batch_changed(runtime: ApplicationRuntime) -> None:
    rendered = _document(runtime, _stage(runtime))
    edited = copy.deepcopy(rendered)
    edited["batch_digest"] = "0" * 64

    with pytest.raises(ImportPipelineError) as raised:
        review_document_edits(rendered, edited)

    assert raised.value.code == "batch_changed"


@pytest.mark.parametrize("edited", [[], "text", {"format": 1}])
def test_a_document_of_the_wrong_shape_refuses(runtime: ApplicationRuntime, edited: Any) -> None:
    rendered = _document(runtime, _stage(runtime))

    with pytest.raises(ImportPipelineError) as raised:
        review_document_edits(rendered, edited)

    assert raised.value.code in {"invalid_review_document", "review_field_changed"}


# --- the use case -----------------------------------------------------------


def test_a_withdrawal_and_an_amendment_apply_together(runtime: ApplicationRuntime) -> None:
    batch_id = _stage(runtime)
    rendered = _document(runtime, batch_id)
    edited = copy.deepcopy(rendered)
    withdrawn = edited["candidates"].pop(5)["id"]
    edited["candidates"][1]["candidate"]["value"] = "tea"

    review = _apply(runtime, rendered, edited)

    by_id = {row.id: row for row in review.candidates}
    assert by_id[withdrawn].status == "rejected"
    assert by_id[rendered["candidates"][1]["id"]].candidate["value"] == "tea"


def test_an_invalid_row_refuses_the_whole_apply_and_leaves_valid_rows_unchanged(
    runtime: ApplicationRuntime,
) -> None:
    batch_id = _stage(runtime)
    rendered = _document(runtime, batch_id)
    edited = copy.deepcopy(rendered)
    edited["candidates"][1]["candidate"]["value"] = "tea"
    del edited["candidates"][5]["candidate"]["predicate"]
    edited["candidates"].pop(4)

    with pytest.raises(ImportPipelineError) as raised:
        _apply(runtime, rendered, edited)

    assert raised.value.code == "invalid_candidates"
    assert raised.value.details["candidate_id"] == rendered["candidates"][5]["id"]
    assert _document(runtime, batch_id) == rendered


def test_a_batch_amended_elsewhere_refuses_and_keeps_the_newer_amendment(runtime: ApplicationRuntime) -> None:
    batch_id = _stage(runtime)
    rendered = _document(runtime, batch_id)
    target = rendered["candidates"][2]["id"]
    runtime.use_cases.amend_staged_candidate.execute(batch_id, target, {"value": "newer"})
    edited = copy.deepcopy(rendered)
    edited["candidates"][2]["candidate"]["value"] = "older"

    with pytest.raises(ImportPipelineError) as raised:
        _apply(runtime, rendered, edited)

    assert raised.value.code == "batch_changed"
    assert next(row for row in _document(runtime, batch_id)["candidates"] if row["id"] == target)[
        "candidate"
    ]["value"] == "newer"


def test_withdrawing_every_row_settles_the_receipt_with_an_audit_entry(runtime: ApplicationRuntime) -> None:
    batch_id = _stage(runtime)
    rendered = _document(runtime, batch_id)
    before = runtime.conn.execute("SELECT COUNT(*) AS total FROM audit_log").fetchone()["total"]

    _apply(runtime, rendered, {**rendered, "candidates": []})

    assert _receipt_status(runtime, batch_id) == "withdrawn"
    assert runtime.conn.execute("SELECT COUNT(*) AS total FROM audit_log").fetchone()["total"] == before + 1


def test_withdrawing_the_rest_after_a_commit_settles_the_receipt_committed(runtime: ApplicationRuntime) -> None:
    batch_id = _stage(runtime)
    first = _document(runtime, batch_id)["candidates"][0]["id"]
    runtime.use_cases.commit_import.execute(batch_id, [first])
    rendered = _document(runtime, batch_id)

    _apply(runtime, rendered, {**rendered, "candidates": rendered["candidates"][:1]})

    assert _receipt_status(runtime, batch_id) == "committed"


def test_growth_past_the_ceiling_is_refused_by_remeasurement(runtime: ApplicationRuntime) -> None:
    batch_id = _stage(runtime)
    rendered = _document(runtime, batch_id)
    payload = runtime.use_cases.preflight_import_batch.execute(batch_id).payload_bytes
    alias = {"value": "a", "kind": "other"}
    grown_by_one = len(json.dumps([alias], ensure_ascii=False)) - len("[]")
    staging, review, people = runtime.use_cases.amend_staged_candidate._staging, runtime.use_cases.review_import, (
        runtime.use_cases.amend_staged_candidate._people
    )
    apply = ApplyReviewEdits(
        staging, review, people, budget=ImportBudget(max_staged_payload_bytes=payload + grown_by_one)
    )
    person = rendered["candidates"][0]["id"]

    fits = apply.execute(batch_id, {person: {"aliases": [alias]}}, [], expected_batch_digest=rendered["batch_digest"])
    assert fits.candidates[0].candidate["aliases"] == [alias]

    with pytest.raises(ImportPipelineError) as raised:
        apply.execute(batch_id, {person: {"aliases": [alias, alias]}}, [], expected_batch_digest=fits.batch_digest)
    assert raised.value.code == "staged_payload_too_large"


# --- the read bound ---------------------------------------------------------


def _rendered_bytes(candidate: dict[str, Any]) -> int:
    entry = ImportReviewCandidateEntry(id="c", source="s", status="pending", candidate=candidate, ordinal=1)
    return len(render_import_json(ImportReviewDocument(batch_id="b", candidates=[entry])).encode("utf-8"))


def _stored_bytes(candidate: dict[str, Any]) -> int:
    return len(json.dumps(candidate, ensure_ascii=False).encode("utf-8"))


_PERSON = {"type": "person", "name": "N", "aliases": [], "matched_person_id": None}
_ALIASED = {**_PERSON, "aliases": [{"value": "a", "kind": "other"}]}
_INTERACTION = {"type": "interaction", "summary": "s", "participant_candidate_ids": [], "date": "2026-01-01T00:00:00Z"}
_TRAIT = {
    "type": "trait", "person_candidate_id": "p", "category": "style", "value": "v", "evidence_note": "n",
    "confidence": 0.5, "evidence_ids": [],
}


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (_PERSON, _ALIASED),
        (_ALIASED, {**_ALIASED, "aliases": [*_ALIASED["aliases"], {"value": "a", "kind": "other"}]}),
        (_ALIASED, {**_ALIASED, "aliases": [{"value": "a", "kind": "other", "lang": "a"}]}),
        (_INTERACTION, {**_INTERACTION, "participant_candidate_ids": ["a"]}),
        (
            {**_INTERACTION, "participant_candidate_ids": ["a"]},
            {**_INTERACTION, "participant_candidate_ids": ["a", "a"]},
        ),
        (_TRAIT, {**_TRAIT, "evidence_ids": ["a"]}),
        (_PERSON, {**_PERSON, "summary": "a"}),
    ],
)
def test_rendered_growth_never_exceeds_the_pinned_expansion(before: dict[str, Any], after: dict[str, Any]) -> None:
    stored = _stored_bytes(after) - _stored_bytes(before)
    rendered = _rendered_bytes(after) - _rendered_bytes(before)

    assert 0 < rendered <= RENDERED_EXPANSION * stored


def test_the_pinned_depth_is_the_deepest_indent_the_renderer_emits() -> None:
    lines = render_import_json(
        ImportReviewDocument(
            batch_id="b",
            candidates=[ImportReviewCandidateEntry(id="c", source="s", status="pending", candidate=_ALIASED)],
        )
    ).splitlines()

    assert max(len(line) - len(line.lstrip(" ")) for line in lines) == RENDERED_INDENT * RENDERED_MAX_DEPTH


def test_an_unchanged_document_always_fits_its_read_bound() -> None:
    assert edited_document_read_bound(1000, payload_bytes=500, payload_limit=400) == 1000
    assert edited_document_read_bound(1000, payload_bytes=390, payload_limit=400) == 1000 + 10 * RENDERED_EXPANSION


# --- review fixes: JSON types and stale projections ------------------------


def test_a_number_turned_into_a_boolean_is_an_edit_not_a_no_op(runtime: ApplicationRuntime) -> None:
    """`1 == True` in Python; an edit to a computed count must still reach validation."""
    batch_id = _stage(runtime)
    rendered = _document(runtime, batch_id)
    edited = copy.deepcopy(rendered)
    edited["candidates"][0]["candidate"]["match_count"] = False  # stored as 0, and 0 == False in Python
    edited["candidates"].pop(5)

    edits = review_document_edits(rendered, edited)

    assert edits.amendments == {rendered["candidates"][0]["id"]: {"match_count": False}}
    with pytest.raises(ImportPipelineError):
        _apply(runtime, rendered, edited)
    assert _document(runtime, batch_id) == rendered


def test_a_version_turned_into_a_boolean_refuses() -> None:
    rendered = {"format": "f", "version": 1, "batch_id": "b", "batch_digest": "d", "candidates": []}

    with pytest.raises(ImportPipelineError) as raised:
        review_document_edits(rendered, {**rendered, "version": True})

    assert raised.value.code == "review_field_changed"


@pytest.mark.parametrize("id_chars", [MAX_MATCH_CANDIDATE_NAME_CHARS, 20 * 1024])
def test_a_saved_document_with_larger_stale_projections_fits_its_read_bound(id_chars: int) -> None:
    """Whatever projection the earlier review admitted fits, including an unbounded restored id."""
    long_name = "\U0001f600" * MAX_MATCH_CANDIDATE_NAME_CHARS
    person = {**_PERSON, "match_disposition": "ambiguous", "match_count": 12}
    entries = [
        {"id": f"{n}" + "i" * id_chars, "canonical_name": long_name, "name_truncated": True}
        for n in range(MAX_MATCH_CANDIDATES)
    ]

    def document(projection: list[dict[str, Any]]) -> bytes:
        entry = ImportReviewCandidateEntry(
            id="c", source="s", status="pending", candidate=person, ordinal=1,
            match_candidates=[MatchCandidate.model_validate(item) for item in projection],
            match_candidates_truncated=bool(projection),
        )
        return render_import_json(ImportReviewDocument(batch_id="b", candidates=[entry])).encode("utf-8")

    saved, fresh = document(entries), document([])
    # Exactly the headroom `ReviewImport._charge_projected` needed to admit the saved projection.
    compact = len(json.dumps(entries, ensure_ascii=False).encode("utf-8"))
    limit = 1000 + compact

    bound = edited_document_read_bound(len(fresh), payload_bytes=1000, payload_limit=limit, stale_projection_rows=1)

    assert len(saved) <= bound
    # The same document may also grow its candidates up to the ceiling: that growth has its own
    # headroom at the renderer's worst case, so the projections must not be drawn from it.
    headroom = limit - 1000
    assert bound - len(fresh) >= RENDERED_EXPANSION * headroom + (len(saved) - len(fresh))
    assert len(saved) - len(fresh) - compact <= (
        MAX_MATCH_CANDIDATES * RENDERED_MATCH_ENTRY_OVERHEAD + RENDERED_MATCH_LIST_OVERHEAD
    )
    assert edited_document_read_bound(len(fresh), payload_bytes=limit, payload_limit=limit) < len(saved)