"""The recorded M31.2 perspective baseline: the frozen model-backed record M31.3 compares against."""

from __future__ import annotations

import json
from pathlib import Path

from evals.harness.suite import load_suite

ROOT = Path(__file__).parents[2]
SUITE_PATH = ROOT / "evals" / "perspective" / "suite.json"
BASELINE = ROOT / "evals" / "results" / "2026-09-23-perspective-baseline-claude-sonnet-5.json"


def test_the_baseline_records_every_scenario_under_both_conditions() -> None:
    """The frozen baseline M31.3 compares against: exact prompts, a clean checkout, and full outputs."""
    report = json.loads(BASELINE.read_text(encoding="utf-8"))
    suite = load_suite(SUITE_PATH).suite

    assert report["review"] == "human"
    assert report["totals"] == []
    assert report["runner"]["kind"] == "command"
    assert report["runner"]["client_version"]
    assert report["source"]["dirty"] is False
    assert report["suite"]["id"] == suite.suite_id
    assert report["prompts"]["system"] == suite.system_prompt
    assert [(item["id"], item["prompt"]) for item in report["prompts"]["tasks"]] == [
        (task.id, task.prompt) for task in suite.tasks
    ]
    recorded = {(run["task_id"], run["condition"]) for run in report["runs"]}
    assert recorded == {(task.id, condition) for task in suite.tasks for condition in ("with_mcp", "without_mcp")}
    assert all(run["answer"].strip() and run["model_id"] == "claude-sonnet-5" for run in report["runs"])
    # Tool use is what shows a guessed read or an unrequested write the answer never mentions.
    assert all(isinstance(run["tool_calls"], list) for run in report["runs"])
