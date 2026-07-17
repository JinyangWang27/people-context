"""FastMCP stdio server wiring: build the server, register tools, run over stdio.

This adapter is the only place the MCP SDK is imported. It resolves the database
path, opens the SQLite store, constructs the repository/audit/clock and the app
use cases, and injects them into the tool layer. All logging goes to STDERR — the
stdio transport uses STDOUT for the protocol itself, so anything printed there
would corrupt the stream.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from people_context.adapters.mcp.tools import register_all
from people_context.adapters.sqlite import SqliteAuditLog, SqlitePeopleRepository, open_db
from people_context.app import RememberPerson, ResolvePerson, SearchPeople
from people_context.config import resolve_db_path
from people_context.ports.clock import SystemClock

SERVER_NAME = "people-context"

SERVER_INSTRUCTIONS = (
    "people-context stores durable, local-first knowledge about the people in the user's life: "
    "their names and aliases, how they relate to the user, their organisations and roles, and "
    "relevant past interactions. "
    "When the user mentions someone by name, nickname, or partial reference, call `resolve_person` "
    "first to find who they mean — prefer resolving before asking the user who someone is. "
    "Use `search_people` for broader browsing and `remember_person` to record a new or updated person. "
    "Read-only tools are safe to call freely; write and destructive tools are annotated so the client "
    "can gate them behind its normal approval flow."
)

_LOG_LOGGER_NAME = "people_context"


@dataclass(frozen=True)
class ToolDeps:
    """Use-case dependencies injected into the tool layer (no globals/singletons)."""

    resolve_person: ResolvePerson
    search_people: SearchPeople
    remember_person: RememberPerson


def _configure_logging() -> logging.Logger:
    """Configure a single STDERR handler for the package logger and return it.

    Rebinds the handler to the current ``sys.stderr`` on every call so the log
    line is never emitted to STDOUT (which carries the stdio protocol).
    """
    logger = logging.getLogger(_LOG_LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def build_server(db_path: str | Path | None = None) -> FastMCP:
    """Build a fully wired FastMCP server backed by the resolved SQLite database.

    Resolves ``db_path`` via :func:`resolve_db_path`, logs the chosen path to
    STDERR, opens the database, constructs the repository/audit/clock and the
    three use cases, and registers every tool. Does not start any transport.
    """
    logger = _configure_logging()
    path = resolve_db_path(db_path)
    logger.info("people-context MCP server using database at %s", path)

    conn = open_db(path)
    repository = SqlitePeopleRepository(conn)
    audit = SqliteAuditLog(conn)
    clock = SystemClock()

    deps = ToolDeps(
        resolve_person=ResolvePerson(repository),
        search_people=SearchPeople(repository),
        remember_person=RememberPerson(repository, repository, audit, clock),
    )

    mcp = FastMCP(name=SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    register_all(mcp, deps)
    return mcp


def main() -> None:
    """CLI entry point: parse ``--db`` and run the server over stdio."""
    parser = argparse.ArgumentParser(
        prog="people-context-mcp",
        description="Local-first MCP server with contextual knowledge about the people in your life.",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help="Path to the SQLite database file (overrides env/config/auto-detect).",
    )
    args = parser.parse_args()
    build_server(args.db).run()


if __name__ == "__main__":
    main()
