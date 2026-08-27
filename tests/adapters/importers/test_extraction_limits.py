"""Resource bounds on a staging request that opts into an M17 candidate type (M17.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from people_context.adapters.sqlite import (
    SqliteImportStagingStore,
    SqlitePeopleRepository,
    open_db,
)
from people_context.app.imports import (
    CANDIDATE_PAYLOAD_TOO_LARGE,
    CANDIDATE_STRING_TOO_LONG,
    MAX_CANDIDATE_REF_CHARS,
    MAX_EXTRACTION_CANDIDATES,
    MAX_EXTRACTION_PAYLOAD_BYTES,
    MAX_EXTRACTION_SOURCE_CHARS,
    MAX_EXTRACTION_STRING_BYTES,
    MAX_OBSERVATION_TEXT_BYTES,
    MAX_RELATIONSHIP_TYPE_CHARS,
    MAX_TRAIT_EVIDENCE_NOTE_BYTES,
    MAX_TRAIT_VALUE_BYTES,
    SOURCE_LABEL_TOO_LONG,
    TOO_MANY_CANDIDATES,
    CandidateStager,
    ImportPipelineError,
    StageCandidates,
)

_NOW = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _RefusingStagingStore(SqliteImportStagingStore):
    """A staging store that fails loudly if a refused request ever reaches persistence."""

    def stage_batch(self, rows: list[Any]) -> None:  # pragma: no cover - guard
        raise AssertionError("a rejected extraction request must never reach durable staging")


def _stage(conn: Any, *, guard: bool = True) -> StageCandidates:
    staging = _RefusingStagingStore(conn) if guard else SqliteImportStagingStore(conn)
    return StageCandidates(CandidateStager(SqlitePeopleRepository(conn), staging, _Clock()))


def _person(ref: str = "alice", name: str = "Alice") -> dict[str, Any]:
    return {"type": "person", "ref": ref, "name": name, "aliases": []}


def _observation(person_ref: str = "alice", text: str = "Asked for metrics") -> dict[str, Any]:
    return {"type": "observation", "person_ref": person_ref, "text": text}


def _trait(**overrides: Any) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "type": "trait",
        "person_ref": "alice",
        "category": "communication_style",
        "value": "Prefers written proposals",
        "evidence_note": "Said so twice in the 20 Aug review.",
        "confidence": 0.6,
    }
    candidate.update(overrides)
    return candidate


def _refusal(stage: StageCandidates, source: str, candidates: list[dict[str, Any]]) -> ImportPipelineError:
    with pytest.raises(ImportPipelineError) as exc_info:
        stage.execute(source, candidates)
    return exc_info.value


def test_an_extraction_request_may_carry_the_maximum_candidate_count() -> None:
    conn = open_db(":memory:")
    stage = _stage(conn, guard=False)
    people = [_person(f"p{index}", f"Person {index}") for index in range(MAX_EXTRACTION_CANDIDATES - 1)]

    result = stage.execute("notes", [*people, _observation("p0")])

    assert result.candidate_count == MAX_EXTRACTION_CANDIDATES


def test_an_extraction_request_one_candidate_over_the_count_is_refused() -> None:
    conn = open_db(":memory:")
    stage = _stage(conn)
    people = [_person(f"p{index}", f"Person {index}") for index in range(MAX_EXTRACTION_CANDIDATES)]

    error = _refusal(stage, "notes", [*people, _observation("p0")])

    assert error.code == TOO_MANY_CANDIDATES
    assert error.details["limit"] == MAX_EXTRACTION_CANDIDATES
    assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


def test_the_extraction_source_label_is_bounded_at_its_exact_limit() -> None:
    conn = open_db(":memory:")
    candidates = [_person(), _observation()]

    assert _stage(conn, guard=False).execute("s" * MAX_EXTRACTION_SOURCE_CHARS, candidates).candidate_count == 2

    error = _refusal(_stage(conn), "s" * (MAX_EXTRACTION_SOURCE_CHARS + 1), candidates)

    assert error.code == SOURCE_LABEL_TOO_LONG
    assert error.details["limit"] == MAX_EXTRACTION_SOURCE_CHARS


def test_the_extraction_source_label_is_measured_after_normalization() -> None:
    """The label is bounded as it will be stored, not as it arrived."""
    conn = open_db(":memory:")
    padded = "  " + "s" * MAX_EXTRACTION_SOURCE_CHARS + "  "

    assert _stage(conn, guard=False).execute(padded, [_person(), _observation()]).candidate_count == 2


def test_an_oversized_string_anywhere_in_an_extraction_request_is_refused() -> None:
    conn = open_db(":memory:")
    oversized = "x" * (MAX_EXTRACTION_STRING_BYTES + 1)

    error = _refusal(_stage(conn), "notes", [_person(), _observation(), _trait(value=oversized)])

    assert error.code == CANDIDATE_STRING_TOO_LONG
    assert error.details["limit"] == MAX_EXTRACTION_STRING_BYTES


def test_an_oversized_legacy_field_is_refused_when_an_m17_candidate_is_present() -> None:
    """A mixed batch must not be able to smuggle a transcript through a released field."""
    conn = open_db(":memory:")
    oversized = "x" * (MAX_EXTRACTION_STRING_BYTES + 1)
    fact = {"type": "fact", "person_ref": "alice", "predicate": "note", "value": oversized}

    error = _refusal(_stage(conn), "notes", [_person(), fact, _observation()])

    assert error.code == CANDIDATE_STRING_TOO_LONG
    assert oversized not in str(error)
    assert oversized not in str(error.details)


def test_an_oversized_nested_string_is_refused() -> None:
    conn = open_db(":memory:")
    oversized = "x" * (MAX_EXTRACTION_STRING_BYTES + 1)
    person = {"type": "person", "ref": "alice", "name": "Alice", "aliases": [{"value": oversized}]}

    error = _refusal(_stage(conn), "notes", [person, _observation()])

    assert error.code == CANDIDATE_STRING_TOO_LONG


def test_the_all_string_limit_counts_utf8_bytes_not_characters() -> None:
    conn = open_db(":memory:")
    # Four bytes per character, so half the byte budget in characters is exactly at the limit.
    at_limit = "🙂" * (MAX_EXTRACTION_STRING_BYTES // 4)
    over_limit = at_limit + "🙂"
    fact = {"type": "fact", "person_ref": "alice", "predicate": "note", "value": at_limit}

    assert _stage(conn, guard=False).execute("notes", [_person(), fact, _observation()]).candidate_count == 3

    error = _refusal(_stage(conn), "notes", [_person(), {**fact, "value": over_limit}, _observation()])

    assert error.code == CANDIDATE_STRING_TOO_LONG


def test_an_extraction_request_over_the_payload_limit_is_refused_before_staging() -> None:
    conn = open_db(":memory:")
    # Each fact stays inside the 8 KiB string limit, so only the whole-array measurement can
    # refuse this: the payload cap is not a restatement of the per-string one.
    filler = "x" * (MAX_EXTRACTION_STRING_BYTES - 1)
    facts = [
        {"type": "fact", "person_ref": "alice", "predicate": f"note{index}", "value": filler}
        for index in range(MAX_EXTRACTION_PAYLOAD_BYTES // MAX_EXTRACTION_STRING_BYTES + 2)
    ]

    error = _refusal(_stage(conn), "notes", [_person(), *facts, _observation()])

    assert error.code == CANDIDATE_PAYLOAD_TOO_LARGE
    assert error.details["limit"] == MAX_EXTRACTION_PAYLOAD_BYTES
    assert filler not in str(error.details)
    assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("candidate", "limit"),
    [
        pytest.param(
            _observation(text="x" * (MAX_OBSERVATION_TEXT_BYTES + 1)),
            MAX_OBSERVATION_TEXT_BYTES,
            id="observation-text",
        ),
        pytest.param(_trait(value="x" * (MAX_TRAIT_VALUE_BYTES + 1)), MAX_TRAIT_VALUE_BYTES, id="trait-value"),
        pytest.param(
            _trait(evidence_note="x" * (MAX_TRAIT_EVIDENCE_NOTE_BYTES + 1)),
            MAX_TRAIT_EVIDENCE_NOTE_BYTES,
            id="trait-evidence-note",
        ),
    ],
)
def test_a_new_field_over_its_own_tighter_limit_is_refused(candidate: dict[str, Any], limit: int) -> None:
    conn = open_db(":memory:")

    error = _refusal(_stage(conn), "notes", [_person(), candidate])

    assert error.code == "invalid_candidates"
    assert any(str(limit) in detail["msg"] for detail in error.details["details"])
    # The refusal names the limit; the rejected text is untrusted and never travels back out.
    assert "x" * 64 not in str(error.details)


@pytest.mark.parametrize(
    ("candidate", "limit"),
    [
        pytest.param(
            _observation(text="x" * MAX_OBSERVATION_TEXT_BYTES),
            MAX_OBSERVATION_TEXT_BYTES,
            id="observation-text",
        ),
        pytest.param(_trait(value="x" * MAX_TRAIT_VALUE_BYTES), MAX_TRAIT_VALUE_BYTES, id="trait-value"),
        pytest.param(
            _trait(evidence_note="x" * MAX_TRAIT_EVIDENCE_NOTE_BYTES),
            MAX_TRAIT_EVIDENCE_NOTE_BYTES,
            id="trait-evidence-note",
        ),
    ],
)
def test_a_new_field_exactly_at_its_limit_is_accepted(candidate: dict[str, Any], limit: int) -> None:
    conn = open_db(":memory:")

    assert _stage(conn, guard=False).execute("notes", [_person(), candidate]).candidate_count == 2


def test_new_reference_and_relationship_type_strings_are_bounded_at_256_characters() -> None:
    conn = open_db(":memory:")
    long_ref = "r" * (MAX_CANDIDATE_REF_CHARS + 1)
    relationship = {
        "type": "relationship",
        "from_ref": long_ref,
        "to_ref": "bob",
        "relationship_type": "colleague",
    }

    error = _refusal(_stage(conn), "notes", [_person(long_ref, "Alice"), _person("bob", "Bob"), relationship])
    assert error.code == "invalid_candidates"

    over_typed = {
        "type": "relationship",
        "from_ref": "alice",
        "to_ref": "bob",
        "relationship_type": "t" * (MAX_RELATIONSHIP_TYPE_CHARS + 1),
    }
    assert _refusal(_stage(conn), "notes", [_person(), _person("bob", "Bob"), over_typed]).code == "invalid_candidates"


def test_a_legacy_only_request_keeps_its_released_unbounded_shape() -> None:
    """M17's caps are conditional. A request of released types only is not narrowed by them."""
    conn = open_db(":memory:")
    stage = _stage(conn, guard=False)
    people = [_person(f"p{index}", f"Person {index}") for index in range(MAX_EXTRACTION_CANDIDATES + 1)]
    oversized_fact = {
        "type": "fact",
        "person_ref": "p0",
        "predicate": "note",
        "value": "x" * (MAX_EXTRACTION_STRING_BYTES + 1),
    }

    result = stage.execute("s" * (MAX_EXTRACTION_SOURCE_CHARS + 1), [*people, oversized_fact])

    assert result.candidate_count == MAX_EXTRACTION_CANDIDATES + 2
