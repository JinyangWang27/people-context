"""Orchestration: build the world, ask each fixed question twice, score both answers.

The comparison the harness exists to make is between two conditions that differ
in exactly one thing — whether the agent can reach a people-context store. Same
model, same system prompt, same task prompt, same rubric; only the MCP server is
added or withheld.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evals.harness import CONDITIONS
from evals.harness.errors import EvalHarnessError
from evals.harness.ports import AgentRequest, AgentRunner
from evals.harness.scoring import TaskScore, score_task
from evals.harness.suite import LoadedSuite, Task
from evals.harness.world import World, build_world_database

#: Names of the run artifacts inside the working directory.
ARTIFACTS_DIRNAME = "artifacts"
WORLD_DB_FILENAME = "world.db"
MCP_CONFIG_FILENAME = "mcp.json"

#: The checkout this harness belongs to, substituted into a server command vector.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RunOutcome:
    """One scored answer."""

    task_id: str
    condition: str
    model_id: str
    answer: str
    truncated: bool
    score: TaskScore


def write_mcp_config(path: Path, server_argv: Sequence[str], db_path: Path) -> Path:
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


def resolve_server_argv(server_argv: tuple[str, ...]) -> list[str]:
    """Substitute the checkout path, so a recorded run names the code it evaluated.

    Resolving the server from PyPI by bare name would let a later release answer the
    same suite differently, which would make a dated result impossible to reproduce.
    """
    resolved: list[str] = []
    for argument in server_argv:
        if argument == "{project_root}":
            resolved.append(str(PROJECT_ROOT))
        elif "{" in argument or "}" in argument:
            raise EvalHarnessError(
                f"argument {argument!r} is not a whole placeholder; expected: {{project_root}}"
            )
        else:
            resolved.append(argument)
    return resolved


def prepare_workspace(
    world: World,
    workdir: Path,
    server_argv: tuple[str, ...],
) -> tuple[Path, Path | None]:
    """Materialize the fictional store and, when configured, its client config.

    Both land under an ``artifacts`` subdirectory that is deliberately *not* the
    directory the agent runs in — see ``run_suite``.
    """
    artifacts = workdir / ARTIFACTS_DIRNAME
    artifacts.mkdir(parents=True, exist_ok=True)
    db_path = build_world_database(world, artifacts / WORLD_DB_FILENAME)
    if not server_argv:
        return db_path, None
    config_path = write_mcp_config(artifacts / MCP_CONFIG_FILENAME, resolve_server_argv(server_argv), db_path)
    return db_path, config_path


def run_suite(
    loaded: LoadedSuite,
    tasks: tuple[Task, ...],
    runner: AgentRunner,
    *,
    agent_root: Path,
    mcp_config_path: Path | None,
    conditions: tuple[str, ...] = CONDITIONS,
) -> tuple[RunOutcome, ...]:
    """Run every selected task under every selected condition, in report order.

    Each invocation gets its own empty directory under ``agent_root``, which must sit
    outside the artifacts tree. Two separate leaks are being closed: an agent running
    beside ``world.db`` could read the fixture off disk instead of going through the
    server, and an agent that writes session or memory files into its working directory
    during ``with_mcp`` would hand that fixture-derived state to the ``without_mcp``
    control that runs after it.
    """
    unknown = sorted(set(conditions) - set(CONDITIONS))
    if unknown:
        raise EvalHarnessError("unknown conditions: " + ", ".join(unknown))
    ordered = tuple(condition for condition in CONDITIONS if condition in set(conditions))
    outcomes: list[RunOutcome] = []
    for task in tasks:
        for condition in ordered:
            directory = agent_root / f"{task.id}.{condition}"
            directory.mkdir(parents=True, exist_ok=False)
            request = AgentRequest(
                task_id=task.id,
                condition=condition,
                system_prompt=loaded.suite.system_prompt,
                prompt=task.prompt,
                mcp_config_path=mcp_config_path if condition == "with_mcp" else None,
                working_directory=directory,
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
