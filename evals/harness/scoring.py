"""Deterministic, rule-based scoring.

No model judges another model here. A criterion is a textual rule over the final
answer, so the same transcript scores identically on every machine and every run,
and a published number can be re-derived from the recorded answer.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from evals.harness.errors import EvalHarnessError
from evals.harness.suite import (
    ContainsAllCriterion,
    ContainsNoneCriterion,
    Criterion,
    MatchesCriterion,
    Task,
)

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Fold an answer to the form criteria are matched against.

    Unicode is compatibility-composed, case is folded, and runs of whitespace
    collapse to one space, so a line-wrapped answer and a single-line answer
    containing the same words score the same.
    """
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text)).strip().casefold()


@dataclass(frozen=True)
class CriterionOutcome:
    """Whether one criterion held, what it was worth, and the exact rule applied.

    The operands travel with the outcome so a report stays self-contained: a reader
    holding an older published result can re-derive it from the recorded answer even
    after the suite has moved on and `suite.json` no longer contains that rule.
    """

    id: str
    description: str
    kind: str
    weight: int
    passed: bool
    values: tuple[str, ...] | None
    pattern: str | None


@dataclass(frozen=True)
class TaskScore:
    """The scored rubric for one answer."""

    earned: int
    possible: int
    criteria: tuple[CriterionOutcome, ...]

    @property
    def percent(self) -> float:
        """Return the earned share of the possible weight, to one decimal place."""
        if self.possible == 0:
            return 0.0
        return round(self.earned * 100 / self.possible, 1)


def evaluate_criterion(criterion: Criterion, answer: str) -> bool:
    """Return whether one criterion holds for ``answer``."""
    folded = normalize(answer)
    if isinstance(criterion, ContainsAllCriterion):
        return all(normalize(value) in folded for value in criterion.values)
    if isinstance(criterion, ContainsNoneCriterion):
        return not any(normalize(value) in folded for value in criterion.values)
    if isinstance(criterion, MatchesCriterion):
        return re.search(criterion.pattern, folded, re.IGNORECASE) is not None
    raise EvalHarnessError(f"unsupported criterion kind: {type(criterion).__name__}")


def score_task(task: Task, answer: str) -> TaskScore:
    """Score one answer against one task's rubric, in rubric order."""
    outcomes = tuple(
        CriterionOutcome(
            id=criterion.id,
            description=criterion.description,
            kind=criterion.kind,
            weight=criterion.weight,
            passed=evaluate_criterion(criterion, answer),
            values=getattr(criterion, "values", None),
            pattern=getattr(criterion, "pattern", None),
        )
        for criterion in task.rubric
    )
    earned = sum(outcome.weight for outcome in outcomes if outcome.passed)
    return TaskScore(earned=earned, possible=task.possible_weight, criteria=outcomes)
