"""Command line for the evaluation harness.

    uv run python -m evals.harness --runner stub
    uv run python -m evals.harness --runner claude-cli --out evals/results/<date>.json

The stub runner needs no network and no API key; it is the dry run that proves
the suite, the fixture, and the scoring still work.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from evals.harness import CONDITIONS
from evals.harness.errors import EvalHarnessError
from evals.harness.ports import AgentRunner
from evals.harness.report import build_report, render_summary
from evals.harness.runner import prepare_workspace, run_suite
from evals.harness.runners import build_runner
from evals.harness.suite import CommandRunnerConfig, load_suite
from evals.harness.world import load_world
from people_context.adapters.filesystem.private_file import atomic_write_private_text
from people_context.ports.clock import Clock, SystemClock

#: The suite shipped with the repository, resolved relative to this package.
DEFAULT_SUITE = Path(__file__).resolve().parents[1] / "suite" / "suite.json"


def build_parser() -> argparse.ArgumentParser:
    """Build the harness parser without touching the filesystem."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.harness",
        description="Run the fictional-data people-context evaluation suite.",
    )
    parser.add_argument("--suite", default=str(DEFAULT_SUITE), metavar="PATH", help="Suite definition to run.")
    parser.add_argument("--runner", default="stub", metavar="NAME", help="Runner defined by the suite.")
    parser.add_argument(
        "--workdir",
        default=None,
        metavar="DIR",
        help="Directory for the fictional database and MCP config (default: a temporary directory).",
    )
    parser.add_argument("--out", default=None, metavar="PATH", help="Write the JSON report to this path.")
    parser.add_argument("--only", default=None, metavar="TASK[,TASK...]", help="Run only these task ids.")
    parser.add_argument(
        "--condition",
        action="append",
        choices=CONDITIONS,
        default=None,
        help="Run only this condition; repeatable.",
    )
    return parser


def main(argv: list[str] | None = None, *, clock: Clock | None = None) -> int:
    """Run the suite and print a summary; return a process exit code."""
    args = build_parser().parse_args(argv)
    try:
        return _run(args, clock or SystemClock())
    except EvalHarnessError as exc:
        print(f"Evaluation refused: {exc}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace, clock: Clock) -> int:
    loaded = load_suite(Path(args.suite).expanduser())
    world = load_world(loaded.world_path)
    only = tuple(item.strip() for item in args.only.split(",") if item.strip()) if args.only else ()
    tasks = loaded.select_tasks(only)
    config = loaded.runner_config(args.runner)
    runner: AgentRunner = build_runner(config, loaded)
    server_argv = config.mcp_server_argv if isinstance(config, CommandRunnerConfig) else ()
    conditions = tuple(args.condition) if args.condition else CONDITIONS

    with _workspace(args.workdir) as workdir, _agent_directory() as agent_directory:
        _, mcp_config_path = prepare_workspace(world, workdir, server_argv)
        outcomes = run_suite(
            loaded,
            tasks,
            runner,
            agent_directory=agent_directory,
            mcp_config_path=mcp_config_path,
            conditions=conditions,
        )

    report = build_report(
        loaded,
        world,
        tasks,
        outcomes,
        runner_name=args.runner,
        runner_kind=runner.kind,
        generated_at=clock.now(),
        mcp_server_argv=server_argv,
    )
    document = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        destination = atomic_write_private_text(Path(args.out).expanduser(), document)
        print(f"Report written: {destination}")
    print(render_summary(report), end="")
    return 0


@contextmanager
def _workspace(requested: str | None) -> Iterator[Path]:
    """Yield the requested working directory, or a temporary one that is removed."""
    if requested is not None:
        yield Path(requested).expanduser().absolute()
        return
    with tempfile.TemporaryDirectory(prefix="people-context-evals-") as temporary:
        yield Path(temporary)


@contextmanager
def _agent_directory() -> Iterator[Path]:
    """Yield an empty directory for the agent process, outside the artifacts tree.

    It is always a fresh temporary directory, never a child of ``--workdir``, so no
    relative path from the agent's own working directory reaches the fictional
    database or the MCP configuration. A control run that could read `world.db`
    directly would not be a control run.
    """
    with tempfile.TemporaryDirectory(prefix="people-context-evals-agent-") as temporary:
        yield Path(temporary)


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
