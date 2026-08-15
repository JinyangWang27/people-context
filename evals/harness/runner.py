"""Orchestration: build the world, ask each fixed question twice, score both answers.

The comparison the harness exists to make is between two conditions that differ
in exactly one thing — whether the agent can reach a people-context store. Same
model, same system prompt, same task prompt, same rubric; only the MCP server is
added or withheld.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from evals.harness import CONDITIONS
from evals.harness.errors import EvalHarnessError
from evals.harness.ports import AgentRequest, AgentRunner
from evals.harness.scoring import TaskScore, score_task
from evals.harness.suite import LoadedSuite, Task
from evals.harness.world import World, build_world_database

#: Names of the run artifacts inside the working directory.
WORLD_DB_FILENAME = "world.db"
MCP_CONFIG_FILENAME = "mcp.json"


@dataclass(frozen=True)
class RunOutcome:
    """One scored answer."""

    task_id: str
    condition: str
    model_id: str
    answer: str
    truncated: bool
    score: TaskScore


def write_mcp_config(path: Path, server_argv: tuple[str, ...], db_path: Path) -> Path:
    """Write the MCP client configuration used by the ``with_mcp`` condition.

    The database is named on the server command line rather than left to
    environment or configuration discovery, so the evaluated agent reaches the
    fictional store and can reach nothing else.
    """
    document = {
        "mcpServers": {
            "people-context": {
                "type": "stdio",
                "command": server_argv[0],
                "args": [*server_argv[1:], "--db", str(db_path)],
            }
        }
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def prepare_workspace(
    world: World,
    workdir: Path,
    server_argv: tuple[str, ...],
) -> tuple[Path, Path | None]:
    """Materialize the fictional store and, when configured, its client config."""
    workdir.mkdir(parents=True, exist_ok=True)
    db_path = build_world_database(world, workdir / WORLD_DB_FILENAME)
    if not server_argv:
        return db_path, None
    config_path = write_mcp_config(workdir / MCP_CONFIG_FILENAME, server_argv, db_path)
    return db_path, config_path


def run_suite(
    loaded: LoadedSuite,
    tasks: tuple[Task, ...],
    runner: AgentRunner,
    *,
    workdir: Path,
    mcp_config_path: Path | None,
    conditions: tuple[str, ...] = CONDITIONS,
) -> tuple[RunOutcome, ...]:
    """Run every selected task under every selected condition, in report order."""
    unknown = sorted(set(conditions) - set(CONDITIONS))
    if unknown:
        raise EvalHarnessError("unknown conditions: " + ", ".join(unknown))
    ordered = tuple(condition for condition in CONDITIONS if condition in set(conditions))
    outcomes: list[RunOutcome] = []
    for task in tasks:
        for condition in ordered:
            request = AgentRequest(
                task_id=task.id,
                condition=condition,
                system_prompt=loaded.suite.system_prompt,
                prompt=task.prompt,
                mcp_config_path=mcp_config_path if condition == "with_mcp" else None,
                working_directory=workdir,
            )
            response = runner.run(request)
            outcomes.append(
                RunOutcome(
                    task_id=task.id,
                    condition=condition,
                    model_id=response.model_id,
                    answer=response.answer,
                    truncated=response.truncated,
                    score=score_task(task, response.answer),
                )
            )
    return tuple(outcomes)
