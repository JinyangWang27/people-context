"""Possible existing people on an unmatched person row (#222).

A shared given name is weak evidence, so these rows stay unmatched: suggestions widen what a reviewer is
shown and what they may choose, and never decide an identity on their own.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.adapters.sqlite.repository import SqlitePeopleRepository
from people_context.app.imports import ImportPipelineError
from people_context.app.imports.identity import possible_match_tokens
from people_context.domain.person import Person

_NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[ApplicationRuntime]:
    built = build_runtime(tmp_path / "people.db", clock=_Clock())
    yield built
    built.close()


def _save_person(runtime: ApplicationRuntime, person_id: str, name: str) -> str:
    SqlitePeopleRepository(runtime.conn).save_person(
        Person(id=person_id, canonical_name=name, aliases=[], created_at=_NOW, updated_at=_NOW)
    )
    runtime.conn.commit()
    return person_id


def _stage(runtime: ApplicationRuntime, *names: str, strict: bool = True) -> str:
    candidates: list[dict[str, Any]] = [
        {"type": "person", "ref": f"p{index}", "name": name, "aliases": []} for index, name in enumerate(names)
    ]
    return runtime.use_cases.stage_candidates.execute("notes", candidates, strict_identity=strict).batch_id


def _review(runtime: ApplicationRuntime, batch_id: str) -> list[Any]:
    return runtime.use_cases.review_import.execute(batch_id, suggest_possible_matches=True).candidates


def _suggested(row: Any) -> list[str]:
    return [entry.id for entry in row.possible_matches or []]


def test_tokens_cover_the_given_name_and_its_short_forms_but_not_the_name_itself() -> None:
    assert possible_match_tokens("Alex Morgan")[:2] == ["alec", "alec morgan"]
    assert "alex" in possible_match_tokens("Alex Morgan")
    assert "alex morgan" not in possible_match_tokens("Alex Morgan")
    assert {"kate", "kate bennett"} <= set(possible_match_tokens("Katherine Bennett"))
    assert possible_match_tokens("   ") == []


@pytest.mark.parametrize("strict", [True, False])
@pytest.mark.parametrize(
    ("stored", "incoming"),
    [("Alex", "Alex Morgan"), ("Kate", "Katherine Bennett"), ("Kate Bennett", "Katherine Bennett")],
)
def test_the_observed_pairs_are_suggested_and_stay_unmatched(
    runtime: ApplicationRuntime, stored: str, incoming: str, strict: bool
) -> None:
    existing = _save_person(runtime, "old", stored)

    (row,) = _review(runtime, _stage(runtime, incoming, strict=strict))

    assert _suggested(row) == [existing]
    assert row.candidate["matched_person_id"] is None
    assert row.match_candidates is None


def test_suggestions_are_opt_in_and_absent_when_there_is_nothing_to_suggest(runtime: ApplicationRuntime) -> None:
    _save_person(runtime, "old", "Alex")
    batch_id = _stage(runtime, "Alex Morgan", "Nadia Okonkwo")

    plain = runtime.use_cases.review_import.execute(batch_id).candidates
    alex, nadia = _review(runtime, batch_id)

    assert all(row.possible_matches is None for row in plain)
    assert nadia.possible_matches is None
    assert _suggested(alex) == ["old"]


def test_suggestions_do_not_change_the_digest(runtime: ApplicationRuntime) -> None:
    batch_id = _stage(runtime, "Alex Morgan")
    before = runtime.use_cases.review_import.execute(batch_id, suggest_possible_matches=True).batch_digest
    _save_person(runtime, "old", "Alex")

    after = runtime.use_cases.review_import.execute(batch_id, suggest_possible_matches=True)

    assert _suggested(after.candidates[0]) == ["old"]
    assert after.batch_digest == before


def test_suggestions_are_capped_and_flag_truncation(runtime: ApplicationRuntime) -> None:
    for index in range(5):
        _save_person(runtime, f"old{index}", "Alex")

    (row,) = _review(runtime, _stage(runtime, "Alex Morgan"))

    assert len(row.possible_matches) == 3
    assert row.possible_matches_truncated is True


def test_confirming_a_suggestion_commits_into_the_existing_person(runtime: ApplicationRuntime) -> None:
    existing = _save_person(runtime, "old", "Kate")
    batch_id = _stage(runtime, "Katherine Bennett")
    (row,) = _review(runtime, batch_id)
    stale = runtime.use_cases.review_import.execute(batch_id).batch_digest

    amended = runtime.use_cases.amend_staged_candidate.execute(
        batch_id, row.id, {"matched_person_id": existing}, expected_batch_digest=stale
    )
    (chosen,) = amended.candidates

    assert chosen.candidate["matched_person_id"] == existing
    assert chosen.candidate["match_disposition"] == "matched"
    # The reviewed target is part of the digest: a commit against the pre-choice view is refused.
    assert amended.batch_digest != stale
    with pytest.raises(ImportPipelineError) as refused:
        runtime.use_cases.commit_import.execute(batch_id, [row.id], expected_batch_digest=stale)
    assert refused.value.code == "batch_changed"

    runtime.use_cases.commit_import.execute(batch_id, [row.id], expected_batch_digest=amended.batch_digest)

    people = runtime.repo.list_people()
    assert [person.id for person in people] == [existing]
    assert "Katherine Bennett" in people[0].all_names()


def test_unrelated_people_sharing_a_given_name_are_never_matched_silently(runtime: ApplicationRuntime) -> None:
    _save_person(runtime, "a1", "Alex Chen")
    _save_person(runtime, "a2", "Alex Rivera")
    batch_id = _stage(runtime, "Alex Smith")
    (row,) = _review(runtime, batch_id)

    # Neither stored Alex is stored under "alex" itself, so nothing is even suggested.
    assert row.possible_matches is None
    runtime.use_cases.commit_import.execute(batch_id, [row.id])

    assert sorted(person.canonical_name for person in runtime.repo.list_people()) == [
        "Alex Chen",
        "Alex Rivera",
        "Alex Smith",
    ]


def test_a_suggestion_left_unconfirmed_commits_as_a_new_person(runtime: ApplicationRuntime) -> None:
    _save_person(runtime, "old", "Alex")
    batch_id = _stage(runtime, "Alex Morgan")
    (row,) = _review(runtime, batch_id)

    runtime.use_cases.commit_import.execute(batch_id, [row.id])

    assert sorted(person.canonical_name for person in runtime.repo.list_people()) == ["Alex", "Alex Morgan"]


def test_a_person_neither_matched_nor_suggested_is_still_refused(runtime: ApplicationRuntime) -> None:
    _save_person(runtime, "old", "Alex")
    stranger = _save_person(runtime, "other", "Nadia Okonkwo")
    batch_id = _stage(runtime, "Alex Morgan")
    (row,) = _review(runtime, batch_id)

    with pytest.raises(ImportPipelineError) as refused:
        runtime.use_cases.amend_staged_candidate.execute(batch_id, row.id, {"matched_person_id": stranger})

    assert refused.value.code == "person_not_a_match"
