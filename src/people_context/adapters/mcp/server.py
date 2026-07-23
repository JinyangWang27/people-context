"""FastMCP server wiring with stdio and loopback Streamable HTTP transports.

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
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from people_context.adapters.mcp.tools import register_all
from people_context.adapters.runtime import build_runtime

SERVER_NAME = "people-context"

SERVER_INSTRUCTIONS = (
    "people-context stores durable, local-first knowledge about the people in the user's life: "
    "their names and aliases, how they relate to the user, their organisations and roles, and "
    "relevant past interactions. "
    "When the user mentions someone by name, nickname, or partial reference, call `resolve_person` "
    "first to find who they mean — prefer resolving before asking the user who someone is. "
    "After resolving an identity, use `get_person_context` for a bounded, sensitivity-aware context bundle. "
    "After resolving a person, use `get_communication_guidance` when communication help is requested. "
    "Use `search_people` for broader browsing and `remember_person` to record a new or updated person. "
    "Use `stage_candidates` only for concise structured proposals — not raw source text — that are left for "
    "later user review and never committed automatically. "
    "Read-only tools are safe to call freely; write and destructive tools are annotated so the client "
    "can gate them behind its normal approval flow."
)

_LOG_LOGGER_NAME = "people_context"


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
    application use cases, and registers every tool. Does not start any transport.
    """
    logger = _configure_logging()
    runtime = build_runtime(db_path, warning=logger.warning)
    logger.info("people-context MCP server using database at %s", runtime.path)

    mcp = FastMCP(name=SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    register_all(mcp, runtime.use_cases)
    return mcp


def _build_parser() -> argparse.ArgumentParser:
    """Build the server entrypoint parser without constructing application state."""
    parser = argparse.ArgumentParser(
        description="Local-first MCP server with contextual knowledge about the people in your life.",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help="Path to the SQLite database file (overrides env/config/auto-detect).",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve Streamable HTTP on loopback instead of stdio.",
    )
    parser.add_argument(
        "--host",
        choices=("127.0.0.1",),
        default="127.0.0.1",
        help="HTTP bind host; only 127.0.0.1 is accepted (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="HTTP bind port (default: 8765).",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Select stdio by default or explicitly configured loopback Streamable HTTP."""
    args = _build_parser().parse_args(argv)
    server = build_server(args.db)
    if not args.http:
        server.run()
        return

    server.settings.host = args.host
    server.settings.port = args.port
    server.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
    )
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
