"""Scoring must be deterministic and must not confuse one contact for another."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.scoring import evaluate_criterion, normalize, score_task
from evals.harness.suite import (
    ContainsAllCriterion,
    ContainsNoneCriterion,
    MatchesCriterion,
    Task,
    load_suite,
)

SUITE_PATH = Path(__file__).parents[2] / "evals" / "suite" / "suite.json"


def _task(task_id: str) -> Task:
    loaded = load_suite(SUITE_PATH)
    return next(task for task in loaded.suite.tasks if task.id == task_id)


def test_normalization_collapses_wrapping_and_case() -> None:
    assert normalize("Priya   Raman\n  is the\tData Lead") == "priya raman is the data lead"


def test_line_wrapping_does_not_change_a_score() -> None:
    task = _task("context-recall")
    single_line = "Tomas Brandt is the Operations Manager at Tidepool Collective; he prefers bullet points."
    wrapped = single_line.replace("; ", ";\n    ")

    assert score_task(task, single_line).earned == score_task(task, wrapped).earned


def test_contains_all_needs_every_phrase() -> None:
    criterion = ContainsAllCriterion(
        id="both",
        kind="answer_contains_all",
        description="both phrases",
        values=("Kestrel Analytics", "Data Lead"),
    )

    assert evaluate_criterion(criterion, "Data Lead at Kestrel Analytics")
    assert not evaluate_criterion(criterion, "Data Lead at Harbourline Trust")


def test_contains_none_fails_on_any_listed_phrase() -> None:
    criterion = ContainsNoneCriterion(
        id="neither",
        kind="answer_contains_none",
        description="neither phrase",
        values=("Harbourline Trust", "Grants Officer"),
    )

    assert evaluate_criterion(criterion, "Data Lead at Kestrel Analytics")
    assert not evaluate_criterion(criterion, "Grants Officer at Kestrel Analytics")


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Priya Raman is the Data Lead.", True),
        ("Priya Ramanathan is the Grants Officer.", False),
        ("priya raman, data lead", True),
    ],
)
def test_word_boundaries_separate_a_name_from_a_longer_name(answer: str, expected: bool) -> None:
    """Regression: substring matching would score 'Priya Ramanathan' as 'Priya Raman'."""
    criterion = MatchesCriterion(
        id="right-priya",
        kind="answer_matches",
        description="the shorter surname as a whole word",
        pattern=r"\bpriya raman\b",
    )

    assert evaluate_criterion(criterion, answer) is expected


def test_scores_are_weighted_and_partial() -> None:
    task = _task("context-recall")

    partial = score_task(task, "Tomas Brandt is the Operations Manager at Tidepool Collective.")

    assert partial.possible == 7
    assert partial.earned == 5
    assert partial.percent == 71.4
    failed = [criterion.id for criterion in partial.criteria if not criterion.passed]
    assert failed == ["states-update-preference"]


def test_criteria_are_reported_in_rubric_order() -> None:
    task = _task("identity-disambiguation")

    score = score_task(task, "Priya Raman, Data Lead at Kestrel Analytics.")

    assert [criterion.id for criterion in score.criteria] == [item.id for item in task.rubric]
    assert score.earned == score.possible


def test_an_empty_answer_earns_only_the_negative_criteria() -> None:
    task = _task("identity-disambiguation")

    score = score_task(task, "I do not know.")

    assert score.earned == 1
    assert score.percent == 20.0
