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
from evals.harness.runner import prepare_workspace, run_suite, source_identity
from evals.harness.runners import build_runner
from evals.harness.runners.command import CommandAgentRunner
from evals.harness.suite import CommandRunnerConfig, load_suite
from evals.harness.world import load_world
from people_context.adapters.filesystem.private_file import atomic_write_private_text
from people_context.cli.portability import collides_with_database
from people_context.config import resolve_db_path
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
    # Read the checkout state before the run writes anything. A --workdir inside the
    # checkout would otherwise make its own untracked database and config the reason a
    # clean tree is recorded as dirty, which misidentifies the code behind a result.
    source = source_identity() if isinstance(runner, CommandAgentRunner) else None

    with _workspace(args.workdir) as workdir, _agent_root() as agent_root:
        workspace = prepare_workspace(world, workdir, server_argv)
        outcomes = run_suite(
            loaded,
            tasks,
            runner,
            agent_root=agent_root,
            workspace=workspace,
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
        client_version=runner.probe_client_version() if isinstance(runner, CommandAgentRunner) else None,
        source=source,
    )
    document = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    # The summary goes out before the write is attempted. Publication is the last step
    # of a run that may have cost real model invocations, and a missing or unwritable
    # destination must not be the reason those results are never seen.
    print(render_summary(report), end="")
    if args.out:
        destination = _report_destination(args.out)
        try:
            written = atomic_write_private_text(destination, document)
        except OSError as exc:
            print(f"Cannot write the report to {destination}: {exc.strerror or exc}", file=sys.stderr)
            print("Recovering the completed report on stdout instead:", file=sys.stderr)
            print(document, end="")
            return 1
        print(f"Report written: {written}")
    return 0


def _report_destination(requested: str) -> Path:
    """Return the report path, or refuse one that would replace a live database.

    Publication is an atomic replace, so a mistyped ``--out`` naming the configured
    store — or one of its WAL, shared-memory, or rollback sidecars — would destroy it.
    This reuses the guard the brief, vCard, and reminder exporters already apply rather
    than reimplementing a weaker version of it.
    """
    destination = Path(requested).expanduser()
    if collides_with_database(destination, resolve_db_path(None)):
        raise EvalHarnessError(
            f"refusing to write the report to {destination}: that path is the configured "
            "people-context database or one of its sidecars"
        )
    return destination


@contextmanager
def _workspace(requested: str | None) -> Iterator[Path]:
    """Yield the requested working directory, or a temporary one that is removed."""
    if requested is not None:
        yield Path(requested).expanduser().absolute()
        return
    with tempfile.TemporaryDirectory(prefix="people-context-evals-") as temporary:
        yield Path(temporary)


@contextmanager
def _agent_root() -> Iterator[Path]:
    """Yield the parent of the per-invocation agent directories.

    It is always a fresh temporary tree, never a child of ``--workdir``, so no relative
    path from an agent's working directory reaches the fictional database or the MCP
    configuration. A control run that could read `world.db` directly would not be a
    control run.
    """
    with tempfile.TemporaryDirectory(prefix="people-context-evals-agent-") as temporary:
        yield Path(temporary)


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
