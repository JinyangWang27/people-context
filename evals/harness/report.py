"""The report document: what was asked, what was answered, and what it scored.

A published evaluation number is only credible if a reader can re-derive it, so
the report carries the harness version, the suite and world identity, the model
id, the exact prompts, every recorded answer, and every per-criterion outcome.
It is versioned and additive under the same promise as the CLI JSON surfaces.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from evals.harness import HARNESS_VERSION, REPORT_FORMAT, REPORT_VERSION
from evals.harness.runner import RunOutcome
from evals.harness.suite import LoadedSuite, Task
from evals.harness.world import World


def _instant(value: datetime) -> str:
    """Render one timezone-aware instant as canonical UTC."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _percent(earned: int, possible: int) -> float:
    return 0.0 if possible == 0 else round(earned * 100 / possible, 1)


def build_report(
    loaded: LoadedSuite,
    world: World,
    tasks: tuple[Task, ...],
    outcomes: tuple[RunOutcome, ...],
    *,
    runner_name: str,
    runner_kind: str,
    generated_at: datetime,
    mcp_server_argv: tuple[str, ...] = (),
    client_version: str | None = None,
) -> dict[str, Any]:
    """Assemble the deterministic report document for one run."""
    ordered = sorted(outcomes, key=lambda outcome: (outcome.task_id, outcome.condition))
    return {
        "format": REPORT_FORMAT,
        "version": REPORT_VERSION,
        "harness_version": HARNESS_VERSION,
        "generated_at": _instant(generated_at),
        "suite": {
            "id": loaded.suite.suite_id,
            "version": loaded.suite.suite_version,
            "world_id": world.world_id,
            "world_as_of": _instant(world.as_of),
        },
        "runner": {
            "name": runner_name,
            "kind": runner_kind,
            # Recorded unsubstituted: it names the code the run evaluated without
            # baking a local home directory into a published document.
            "mcp_server_argv": list(mcp_server_argv),
            # The agent CLI build that produced the answers. Flags and built-in
            # prompts move between releases, so a score names its client or admits
            # it does not know which one ran.
            "client_version": client_version,
        },
        "prompts": {
            "system": loaded.suite.system_prompt,
            "tasks": [{"id": task.id, "title": task.title, "prompt": task.prompt} for task in tasks],
        },
        "totals": _totals(ordered),
        "runs": [
            {
                "task_id": outcome.task_id,
                "condition": outcome.condition,
                "model_id": outcome.model_id,
                "earned": outcome.score.earned,
                "possible": outcome.score.possible,
                "percent": outcome.score.percent,
                "criteria": [
                    {
                        "id": criterion.id,
                        "kind": criterion.kind,
                        "description": criterion.description,
                        "weight": criterion.weight,
                        "passed": criterion.passed,
                        "values": list(criterion.values) if criterion.values is not None else None,
                        "pattern": criterion.pattern,
                        "min_lines": criterion.min_lines,
                        "scope": criterion.scope,
                    }
                    for criterion in outcome.score.criteria
                ],
                "answer": outcome.answer,
            }
            for outcome in ordered
        ],
    }


def _totals(outcomes: Iterable[RunOutcome]) -> list[dict[str, Any]]:
    """Aggregate one row per condition, ordered by condition name."""
    buckets: dict[str, list[RunOutcome]] = {}
    for outcome in outcomes:
        buckets.setdefault(outcome.condition, []).append(outcome)
    rows: list[dict[str, Any]] = []
    for condition in sorted(buckets):
        runs = buckets[condition]
        earned = sum(run.score.earned for run in runs)
        possible = sum(run.score.possible for run in runs)
        rows.append(
            {
                "condition": condition,
                "tasks": len(runs),
                "earned": earned,
                "possible": possible,
                "percent": _percent(earned, possible),
                "model_ids": sorted({run.model_id for run in runs}),
            }
        )
    return rows


def render_summary(report: dict[str, Any]) -> str:
    """Render the human summary printed after a run."""
    lines = [
        f"suite {report['suite']['id']} v{report['suite']['version']} "
        f"(world {report['suite']['world_id']}, harness {report['harness_version']})",
        f"runner {report['runner']['name']} ({report['runner']['kind']})",
        "",
        "| condition | tasks | earned | possible | percent |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for total in report["totals"]:
        lines.append(
            f"| {total['condition']} | {total['tasks']} | {total['earned']} "
            f"| {total['possible']} | {total['percent']} |"
        )
    lines.extend(("", "| task | condition | score |", "| --- | --- | ---: |"))
    for run in report["runs"]:
        lines.append(f"| {run['task_id']} | {run['condition']} | {run['earned']}/{run['possible']} |")
    return "\n".join(lines) + "\n"
