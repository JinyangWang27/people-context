"""Orchestration: build the world, ask each fixed question twice, score both answers.

The comparison the harness exists to make is between two conditions that differ
in exactly one thing — whether the agent can reach a people-context store. Same
model, same system prompt, same task prompt, same rubric; only the MCP server is
added or withheld.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evals.harness import CONDITIONS
from evals.harness.errors import EvalHarnessError
from evals.harness.ports import AgentRequest, AgentRunner
from evals.harness.scoring import TaskScore, score_task
from evals.harness.suite import LoadedSuite, Task
from evals.harness.world import DATABASE_SIDECARS, World, build_world_database

#: Names of the run artifacts inside the working directory.
ARTIFACTS_DIRNAME = "artifacts"
INVOCATIONS_DIRNAME = "invocations"
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


def resolve_server_argv(server_argv: tuple[str, ...], world_as_of: datetime) -> list[str]:
    """Substitute the checkout path and the fixture instant into the server command.

    Two things a dated result depends on: resolving the server from PyPI by bare name
    would let a later release answer the same suite differently, and leaving the
    server on the system clock would make time-dependent reads answer differently on
    a different day. Both are pinned here.
    """
    substitutions = {
        "{project_root}": str(PROJECT_ROOT),
        "{world_as_of}": world_as_of.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    resolved: list[str] = []
    for argument in server_argv:
        if argument in substitutions:
            resolved.append(substitutions[argument])
        elif "{" in argument or "}" in argument:
            expected = ", ".join(sorted(substitutions))
            raise EvalHarnessError(
                f"argument {argument!r} is not a whole placeholder; expected one of: {expected}"
            )
        else:
            resolved.append(argument)
    return resolved


class FixtureWorkspace:
    """Hands every ``with_mcp`` invocation its own copy of the fictional store.

    The evaluated server exposes write and destructive tools — ``record_fact``,
    ``remember_person``, ``forget`` — and nothing stops an agent from calling one.
    Sharing a single database would let a mutation made during one task change what
    every later task is scored against, so results would depend on task order and on
    what the model happened to do earlier. Each invocation therefore starts from a
    byte-identical copy of the pristine store, which is never handed out itself.
    """

    def __init__(
        self,
        pristine: Path,
        artifacts: Path,
        server_argv: tuple[str, ...],
        world_as_of: datetime,
    ) -> None:
        self.pristine = pristine
        self._artifacts = artifacts
        self._server_argv = server_argv
        self._world_as_of = world_as_of

    def configuration_for(self, task_id: str, condition: str) -> Path | None:
        """Return a client configuration bound to a fresh copy, or ``None``."""
        if condition != "with_mcp" or not self._server_argv:
            return None
        directory = self._artifacts / INVOCATIONS_DIRNAME / f"{task_id}.{condition}"
        directory.mkdir(parents=True, exist_ok=False)
        database = directory / WORLD_DB_FILENAME
        shutil.copyfile(self.pristine, database)
        for suffix in DATABASE_SIDECARS:
            companion = Path(f"{self.pristine}{suffix}")
            if companion.is_file():
                shutil.copyfile(companion, Path(f"{database}{suffix}"))
        return write_mcp_config(
            directory / MCP_CONFIG_FILENAME,
            resolve_server_argv(self._server_argv, self._world_as_of),
            database,
        )


def prepare_workspace(
    world: World,
    workdir: Path,
    server_argv: tuple[str, ...],
) -> FixtureWorkspace:
    """Materialize the pristine fictional store and return the per-invocation factory.

    Artifacts land under an ``artifacts`` subdirectory that is deliberately *not* the
    directory the agent runs in — see ``run_suite``.
    """
    artifacts = workdir / ARTIFACTS_DIRNAME
    artifacts.mkdir(parents=True, exist_ok=True)
    pristine = build_world_database(world, artifacts / WORLD_DB_FILENAME)
    return FixtureWorkspace(pristine, artifacts, server_argv, world.as_of)


def run_suite(
    loaded: LoadedSuite,
    tasks: tuple[Task, ...],
    runner: AgentRunner,
    *,
    agent_root: Path,
    workspace: FixtureWorkspace,
    conditions: tuple[str, ...] = CONDITIONS,
) -> tuple[RunOutcome, ...]:
    """Run every selected task under every selected condition, in report order.

    Each invocation is isolated twice over: it gets its own empty directory under
    ``agent_root``, which must sit outside the artifacts tree, and — under ``with_mcp``
    — its own copy of the fictional store. An agent running beside ``world.db`` could
    read the fixture off disk instead of going through the server; one writing session
    state into its working directory would hand it to the control run that follows; and
    one calling a write tool would change what every later task is scored against.
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
                mcp_config_path=workspace.configuration_for(task.id, condition),
                working_directory=directory,
            )
            response = runner.run(request)
            outcomes.append(
                RunOutcome(
                    task_id=task.id,
                    condition=condition,
                    model_id=response.model_id,
                    answer=response.answer,
                    score=score_task(task, response.answer),
                )
            )
    return tuple(outcomes)
