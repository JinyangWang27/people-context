"""The workflow-cost suite: usage accounting from the event stream, and scoring by stored state."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.harness.errors import EvalHarnessError
from evals.harness.ports import AgentRequest
from evals.harness.runners.command import CommandAgentRunner
from evals.harness.scoring import _store_count, score_task
from evals.harness.suite import CommandRunnerConfig, StoreCountCriterion, load_suite
from evals.harness.world import build_world_database, load_world

ROOT = Path(__file__).parents[2]
SUITE_PATH = ROOT / "evals" / "workflow" / "suite.json"

_USAGE_AGENT = """
import json
usage = {"input_tokens": 3, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 0, "output_tokens": 5}
events = [
    {"type": "assistant", "message": {"id": "m1", "usage": usage, "content": [{"type": "text", "text": "hi"}]}},
    {"type": "assistant", "message": {"id": "m1", "usage": usage, "content": [
        {"type": "tool_use", "id": "a", "name": "Skill", "input": {}},
        {"type": "tool_use", "id": "b", "name": "mcp__people-context__add_alias", "input": {}},
    ]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "skill body"},
        {"type": "tool_result", "tool_use_id": "b", "content": [{"type": "text", "text": "{}"}]},
    ]}},
    {"type": "assistant", "message": {"id": "m2", "usage": usage, "content": [{"type": "text", "text": "done"}]}},
    {"type": "result", "is_error": False, "result": "done", "num_turns": 2, "total_cost_usd": 0.01,
     "usage": {"input_tokens": 6, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 103,
               "output_tokens": 10}},
]
for event in events:
    print(json.dumps(event))
"""


def test_the_event_stream_yields_token_turn_and_result_size_accounting(tmp_path: Path) -> None:
    script = tmp_path / "usage.py"
    script.write_text(_USAGE_AGENT, encoding="utf-8")
    config = CommandRunnerConfig.model_validate(
        {
            "kind": "command",
            "model_id": "fake/usage-1",
            "argv": [sys.executable, str(script), "{system_prompt}", "{prompt}"],
            "output_format": "stream-json",
            "timeout_seconds": 30,
            "max_output_bytes": 65536,
        }
    )
    request = AgentRequest("t", "without_mcp", "s", "p", None, tmp_path)

    usage = CommandAgentRunner(config).run(request).usage

    assert usage == {
        "api_requests": 2,  # two events of message m1 are one API request
        "num_turns": 2,
        "first_request_input_tokens": 103,
        "input_tokens": 6,
        "cache_creation_input_tokens": 100,
        "cache_read_input_tokens": 103,
        "output_tokens": 10,
        "total_cost_usd": 0.01,
        "tool_result_chars": {"mcp": 2, "skill": 10, "other": 0},
    }


def test_the_shipped_workflow_suite_fails_on_an_untouched_store(tmp_path: Path) -> None:
    """Every task must be losable: the pristine fixture cannot already satisfy the store criteria."""
    loaded = load_suite(SUITE_PATH)
    database = build_world_database(load_world(loaded.world_path), tmp_path / "world.db")

    for task in loaded.suite.tasks:
        score = score_task(task, "", database)
        assert score.earned < score.possible, task.id
        assert any(c.kind == "store_count" and not c.passed for c in score.criteria), task.id


def test_a_store_criterion_without_a_store_fails() -> None:
    task = load_suite(SUITE_PATH).suite.tasks[0]

    assert all(not c.passed for c in score_task(task, "Mimi 田中芽衣").criteria if c.kind == "store_count")


def test_store_queries_run_read_only(tmp_path: Path) -> None:
    """Scoring must never change what it scores, even if a write slipped past the SELECT check."""
    database = tmp_path / "s.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE t (x INTEGER)")

    with pytest.raises(EvalHarnessError, match="readonly"):
        _store_count(database, "INSERT INTO t VALUES (1)")
    assert sqlite3.connect(database).execute("SELECT COUNT(*) FROM t").fetchone() == (0,)


@pytest.mark.parametrize("sql", ["DELETE FROM persons", "SELECT 1; DROP TABLE persons"])
def test_a_store_criterion_must_be_one_select(sql: str) -> None:
    with pytest.raises(ValidationError, match="one SELECT"):
        StoreCountCriterion(id="c", description="d", kind="store_count", sql=sql, equals=0)
