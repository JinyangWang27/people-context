"""The evaluated MCP server, frozen to the fixture's instant.

    python -m evals.harness.server --as-of 2026-08-01T09:00:00Z --db <world.db>

This is the shipped server — the same ``build_server`` wiring, the same tools, the
same stdio transport — with one dependency injected: a clock stopped at the world
fixture's ``as_of``. Without that, time-dependent reads such as
``get_stale_relationships`` would compute "days since" against whatever day the run
happened on, so the same suite, model, and fixture could produce different tool
output and a different score in June than in December. A dated result has to mean
the same thing when it is checked.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from evals.harness.world import FixedClock
from people_context.adapters.mcp.server import build_server


def build_parser() -> argparse.ArgumentParser:
    """Build the wrapper parser without touching the filesystem."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.harness.server",
        description="Run the people-context MCP server against a fictional store at a fixed instant.",
    )
    parser.add_argument("--db", required=True, metavar="PATH", help="The fictional evaluation database.")
    parser.add_argument(
        "--as-of",
        required=True,
        metavar="INSTANT",
        help="The fixture instant the server's clock is frozen at, as ISO-8601 UTC.",
    )
    return parser


def parse_instant(value: str) -> datetime:
    """Parse one timezone-aware instant, refusing a naive one."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--as-of must be timezone-aware")
    return parsed.astimezone(UTC)


def main(argv: list[str] | None = None) -> None:
    """Serve the fictional store over stdio with the fixture's clock."""
    args = build_parser().parse_args(argv)
    server = build_server(args.db, clock=FixedClock(parse_instant(args.as_of)))
    server.run()


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    main()
