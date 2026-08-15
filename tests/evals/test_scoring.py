"""Scoring must be deterministic and must not confuse one contact for another."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.harness.scoring import evaluate_criterion, normalize, normalize_lines, score_task
from evals.harness.suite import (
    ContainsAllCriterion,
    ContainsNoneCriterion,
    LineMatchesCriterion,
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
    assert failed == ["states-update-preference", "states-the-no-preamble-half"]


def test_criteria_are_reported_in_rubric_order() -> None:
    task = _task("identity-disambiguation")

    score = score_task(task, "Priya Raman, Data Lead at Kestrel Analytics.")

    assert [criterion.id for criterion in score.criteria] == [item.id for item in task.rubric]
    assert score.earned == score.possible


def test_an_empty_answer_earns_only_the_negative_criteria() -> None:
    task = _task("identity-disambiguation")

    score = score_task(task, "I do not know.")

    assert score.earned == 2
    assert score.possible == 6
    assert [criterion.id for criterion in score.criteria if criterion.passed] == [
        "does-not-attribute-the-other-priya",
        "asserts-rather-than-hedges",
    ]


def test_a_reversed_path_does_not_score_as_the_shortest_path() -> None:
    """Regression: an unordered name check scored a backwards route as a correct one."""
    task = _task("relationship-path")

    reversed_route = "Priya Raman -> Kofi Mensah -> Tomas Brandt"
    correct_route = "You -> Tomas Brandt -> Kofi Mensah -> Priya Raman"

    reversed_score = score_task(task, reversed_route)
    correct_score = score_task(task, correct_route)

    assert correct_score.earned == correct_score.possible
    assert reversed_score.earned < correct_score.earned
    failed = [criterion.id for criterion in reversed_score.criteria if not criterion.passed]
    assert failed == ["orders-the-path-correctly"]


def test_a_single_line_draft_does_not_earn_the_recipients_bullet_preference() -> None:
    """Regression: the drafting task must measure use of the stored preference."""
    task = _task("guided-drafting")

    plain = score_task(task, "Tomas, please confirm the September operations review date.")
    formatted = score_task(
        task,
        "Tomas - please confirm the September operations review date.\n"
        "- Proposed: week of 14 September\n"
        "- Decision needed by: 5 September",
    )

    assert formatted.earned == formatted.possible
    assert plain.earned == formatted.earned - 2
    failed = [criterion.id for criterion in plain.criteria if not criterion.passed]
    assert failed == ["uses-the-recipients-bullet-format"]


def test_scored_criteria_carry_the_operands_they_applied() -> None:
    task = _task("identity-disambiguation")

    score = score_task(task, "Priya Raman, Data Lead at Kestrel Analytics.")

    by_id = {criterion.id: criterion for criterion in score.criteria}
    assert by_id["names-the-right-priya"].pattern == r"\bpriya raman\b"
    assert by_id["names-the-right-priya"].values is None
    assert by_id["states-employer-and-role"].values == ("Kestrel Analytics", "Data Lead")
    assert by_id["states-employer-and-role"].pattern is None


def test_line_normalization_keeps_boundaries_that_plain_normalization_collapses() -> None:
    assert normalize_lines("Tomas\n\n-  Proposed: Friday\n- Confirm by Monday  ") == (
        "tomas",
        "- proposed: friday",
        "- confirm by monday",
    )


def test_dashes_inside_one_line_are_not_bullets() -> None:
    """Regression: whitespace-collapsed matching read two mid-sentence hyphens as a list."""
    criterion = LineMatchesCriterion(
        id="bullets",
        kind="answer_lines_match",
        description="two bulleted lines",
        pattern=r"^[-*•] \S",
        min_lines=2,
    )

    assert not evaluate_criterion(criterion, "Tomas - September operations review - please confirm the date.")
    assert evaluate_criterion(criterion, "Tomas\n- Proposed: Friday\n- Confirm by Monday")


def test_a_single_line_message_with_two_hyphens_misses_the_bullet_criterion() -> None:
    """The same regression, scored through the shipped rubric."""
    task = _task("guided-drafting")

    score = score_task(task, "Tomas - September operations review - please confirm the date.")

    failed = [criterion.id for criterion in score.criteria if not criterion.passed]
    assert failed == ["uses-the-recipients-bullet-format"]
    assert score.earned == score.possible - 2


def test_a_path_that_continues_past_the_target_is_not_the_shortest_path() -> None:
    """Regression: an ordered substring match accepted extra hops after the target."""
    task = _task("relationship-path")

    overshot = score_task(task, "Noor Vance -> Tomas Brandt -> Kofi Mensah -> Priya Raman -> Ingrid Solberg")
    correct = score_task(task, "You -> Tomas Brandt -> Kofi Mensah -> Priya Raman")

    assert correct.earned == correct.possible
    assert overshot.earned < correct.earned
    assert "does-not-add-people-outside-the-path" in [
        criterion.id for criterion in overshot.criteria if not criterion.passed
    ]


def test_a_route_that_loops_back_to_someone_already_on_it_is_rejected() -> None:
    """Regression: an off-path name list could not catch a repeated hop."""
    task = _task("relationship-path")

    looped = score_task(task, "Noor Vance -> Tomas Brandt -> Kofi Mensah -> Priya Raman -> Kofi Mensah")

    failed = [criterion.id for criterion in looped.criteria if not criterion.passed]
    assert failed == ["orders-the-path-correctly"]


@pytest.mark.parametrize(
    "answer",
    [
        "You -> Tomas Brandt -> Kofi Mensah -> Priya Raman",
        "You -> Tomas Brandt -> Kofi Mensah -> Priya Raman. She is the Data Lead at Kestrel Analytics.",
        "The path is You -> Tomas Brandt -> Kofi Mensah -> Priya Raman. So Tomas and Kofi connect you.",
    ],
)
def test_prose_after_a_correct_route_is_not_treated_as_another_hop(answer: str) -> None:
    """Rejecting continuations must not punish an answer that explains itself afterwards."""
    task = _task("relationship-path")

    score = score_task(task, answer)

    assert score.earned == score.possible


def test_reporting_half_a_stored_preference_earns_half_the_credit() -> None:
    """Regression: 'bullet points with a preamble' contradicts the record but scored full."""
    task = _task("context-recall")

    contradicting = score_task(
        task,
        "Tomas is the Operations Manager at Tidepool Collective and prefers bullet points with a preamble",
    )
    complete = score_task(
        task,
        "Tomas Brandt is the Operations Manager at Tidepool Collective; he prefers bullet points, no preamble.",
    )

    assert complete.earned == complete.possible
    failed = [criterion.id for criterion in contradicting.criteria if not criterion.passed]
    assert failed == ["states-the-no-preamble-half"]


def test_a_paraphrased_preference_still_counts() -> None:
    """The rubric measures the reported fact, not one exact wording of it."""
    task = _task("context-recall")

    score = score_task(
        task,
        "Tomas Brandt, Operations Manager at Tidepool Collective, likes bullet points without preamble.",
    )

    assert score.earned == score.possible


def test_claiming_a_tie_with_another_contact_is_not_the_single_overdue_answer() -> None:
    """Regression: only the most recent contact was rejected, so a false tie scored full."""
    task = _task("stale-follow-up")

    tie = score_task(task, "Ingrid Solberg and Priya Raman are tied; I last spoke with Ingrid on 2026-03-05")
    single = score_task(task, "Ingrid Solberg is the most overdue; you last spoke on 2026-03-05.")

    assert single.earned == single.possible
    failed = [criterion.id for criterion in tie.criteria if not criterion.passed]
    assert failed == ["does-not-name-another-contact"]


def test_a_hedged_attribution_does_not_score_as_a_confident_one() -> None:
    """Regression: declining to answer while naming every right string scored full."""
    task = _task("identity-disambiguation")

    hedged = score_task(task, "I cannot confirm whether Priya Raman is the Data Lead at Kestrel Analytics")
    asserted = score_task(task, "Priya Raman is the Data Lead at Kestrel Analytics.")

    assert asserted.earned == asserted.possible
    failed = [criterion.id for criterion in hedged.criteria if not criterion.passed]
    assert failed == ["asserts-rather-than-hedges"]


def test_background_prose_before_the_bullets_misses_the_no_preamble_half() -> None:
    """Regression: bullets alone satisfied a preference that also forbids preamble."""
    task = _task("guided-drafting")

    preamble = score_task(
        task,
        "Some background before the request: we have been waiting on this.\n"
        "- Tomas, confirm the September operations review date\n"
        "- Decision needed by 5 September",
    )

    failed = [criterion.id for criterion in preamble.criteria if not criterion.passed]
    assert failed == ["opens-without-preamble"]


def test_a_salutation_is_not_treated_as_preamble() -> None:
    """"No preamble" forbids background, not a greeting; the rubric must not confuse them."""
    task = _task("guided-drafting")

    score = score_task(
        task,
        "Hi Tomas,\n- Please confirm the September operations review date\n- Decision needed by 5 September",
    )

    assert score.earned == score.possible
