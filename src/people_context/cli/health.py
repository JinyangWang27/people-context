"""`pctx health`: a read-only check of what a client needs to start the MCP server.

The selected database is only ever inspected over a read-only connection, and the server
handshake runs against a throwaway database. The two results are reported separately so a
passing handshake is never mistaken for verification of the user's own store.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Literal

from people_context import __version__
from people_context.adapters.sqlite.db import (
    EncryptedDatabaseError,
    UnsafeDatabasePathError,
    UnsupportedSchemaError,
    inspect_schema,
    latest_schema_version,
    require_sqlcipher,
    require_supported_schema,
)
from people_context.config import MissingDatabaseKeyError, resolve_db_key, resolve_db_path

HANDSHAKE_TIMEOUT_SECONDS = 30
_MAX_DETAIL_CHARS = 200
_INITIALIZE = (
    json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pctx-health", "version": __version__},
            },
        }
    )
    + "\n"
)
_LIST_TOOLS = (
    json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    + "\n"
    + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    + "\n"
)
_CLIENT_NOTE = (
    "not checkable from this command. Visible skills do not prove the tools are connected: "
    "use /mcp in Claude Code, or start a new session in Codex and ask it to list the people-context tools."
)

Status = Literal["ok", "warn", "fail"]


@dataclass(frozen=True)
class Check:
    """One health result and, when something is wrong, what to do next."""

    name: str
    status: Status
    detail: str
    next_action: str | None = None


def check_uv() -> Check:
    """Plugin launches go through `uv`; a direct install can run without it."""
    found = shutil.which("uv")
    if found:
        return Check("uv", "ok", found)
    return Check(
        "uv",
        "warn",
        "not on PATH; plugin launches need it, direct installs do not.",
        "Install uv (https://docs.astral.sh/uv/) and restart the client.",
    )


def check_dependencies() -> Check:
    """Confirm the required runtime packages and report the optional semantic extra."""
    requirements = importlib.metadata.requires("people-context") or []
    required = [re.split(r"[\s<>=!~;\[(]", item, maxsplit=1)[0] for item in requirements if "extra ==" not in item]
    missing = [name for name in required if not _is_installed(name)]
    if missing:
        return Check(
            "dependencies",
            "fail",
            f"missing {', '.join(missing)}.",
            "Run `uv sync --no-dev` in the install directory, with network access.",
        )
    semantic = "installed" if importlib.util.find_spec("model2vec") else "not installed (optional)"
    return Check("dependencies", "ok", f"{len(required)} required packages present; semantic extra {semantic}.")


def _is_installed(name: str) -> bool:
    try:
        importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def check_database(db: str | None, *, encrypted: bool) -> Check:
    """Inspect the selected database without creating, migrating, or writing to it."""
    name = "your database"
    try:
        # Before any early return: encrypted mode refuses to start without these even for a
        # database that does not exist yet, and the handshake below never uses encrypted mode.
        key = resolve_db_key() if encrypted else None
        if encrypted:
            require_sqlcipher()
        path = resolve_db_path(db)
        if str(path) == ":memory:":
            return Check(name, "ok", ":memory: (nothing stored between launches).")
        if not path.exists():
            return _check_future_database(path)
        stored = inspect_schema(path, key)
        if stored is not None:
            require_supported_schema(stored.version)
    except UnsupportedSchemaError as exc:
        return Check(name, "fail", _short(str(exc)))  # The message already says how to upgrade.
    except (MissingDatabaseKeyError, EncryptedDatabaseError, UnsafeDatabasePathError) as exc:
        return Check(name, "fail", _short(str(exc)), "Fix this, then rerun. `pctx db-path -v` shows the selected path.")
    if stored is None:
        return Check(
            name,
            "fail",
            f"cannot read {path}.",
            "Check its permissions; an encrypted database also needs --encrypted and PEOPLE_CONTEXT_DB_KEY.",
        )
    if not stored.is_people_context:
        return Check(
            name,
            "fail",
            f"{path} is not a people-context database.",
            "Point PEOPLE_CONTEXT_DB at the right file; `pctx db-path -v` shows how the path was chosen.",
        )
    if not os.access(path, os.W_OK):
        return Check(name, "fail", f"{path} is read-only; the server needs to write to it.", "Fix its permissions.")
    latest = latest_schema_version()
    if stored.version < latest:
        return Check(name, "warn", f"{path} (schema {stored.version}); the next server start upgrades it to {latest}.")
    if stored.version > latest:
        return Check(
            name,
            "warn",
            f"{path} (schema {stored.version}) is newer than this release supports ({latest}).",
            "Update people-context.",
        )
    return Check(name, "ok", f"{path} (schema {stored.version}; inspected read-only).")


def _check_future_database(path: Path) -> Check:
    """Report whether the first server start will be able to create a missing database."""
    ancestor = next(parent for parent in path.absolute().parents if parent.exists())
    if os.access(ancestor, os.W_OK):
        return Check("your database", "ok", f"{path} does not exist yet; the first server start creates it.")
    return Check(
        "your database",
        "fail",
        f"cannot create {path}: {ancestor} is not writable.",
        "Set PEOPLE_CONTEXT_DB to a writable path.",
    )


def check_server() -> Check:
    """Run a real stdio MCP handshake in a child process against a throwaway database."""
    name = "server"
    with tempfile.TemporaryDirectory(prefix="pctx-health-") as scratch, (Path(scratch) / "stderr").open("w+") as errors:
        try:
            process = subprocess.Popen(
                _server_command(Path(scratch) / "health.db"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            return Check(name, "fail", _short(f"could not start: {exc}"), "Reinstall the plugin or package.")
        timer = threading.Timer(HANDSHAKE_TIMEOUT_SECONDS, process.kill)
        timer.start()
        try:
            tools = _exchange(process)
        finally:
            timed_out = not timer.is_alive()
            timer.cancel()
            process.kill()
            process.wait()
            # The pipes hold no further reads; closing them here keeps the scratch directory removable.
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    with contextlib.suppress(OSError):
                        pipe.close()
        if tools is not None:
            return Check(name, "ok", f"MCP handshake passed with {tools} tools, using a temporary database, not yours.")
        if timed_out:
            return Check(
                name,
                "fail",
                f"no MCP handshake within {HANDSHAKE_TIMEOUT_SECONDS}s.",
                "Retry once; if it repeats, run `people-context-mcp` directly and read its error.",
            )
        errors.seek(0)
        lines = errors.read().strip().splitlines()
        reason = lines[-1] if lines else f"exit code {process.returncode}"
        return Check(
            name,
            "fail",
            _short(f"handshake failed: {reason}"),
            "Run `people-context-mcp` directly to see the full error.",
        )


def _server_command(db: Path) -> list[str]:
    return [sys.executable, "-m", "people_context.adapters.mcp.server", "--db", str(db)]


def _exchange(process: subprocess.Popen[str]) -> int | None:
    """Return the number of listed tools, or None when the server stops before answering.

    Each reply is awaited before the next request: closing stdin early makes the server cancel
    whatever is still in flight, which would fail a healthy server intermittently.
    """
    assert process.stdin is not None and process.stdout is not None
    try:
        process.stdin.write(_INITIALIZE)
        process.stdin.flush()
        if _read_reply(process.stdout, 1) is None:
            return None
        process.stdin.write(_LIST_TOOLS)
        process.stdin.flush()
    except OSError:
        return None
    reply = _read_reply(process.stdout, 2)
    tools = reply.get("result", {}).get("tools") if reply else None
    return len(tools) if isinstance(tools, list) else None


def _read_reply(stream: IO[str], request_id: int) -> dict[str, Any] | None:
    """Read protocol lines until the reply to `request_id`, or None at end of stream."""
    for line in stream:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            return message
    return None


def _short(text: str) -> str:
    return text if len(text) <= _MAX_DETAIL_CHARS else f"{text[: _MAX_DETAIL_CHARS - 3]}..."


def cmd_health(args: argparse.Namespace) -> int:
    """Print each check and exit 1 when one fails, so a script can gate on the result."""
    print(f"people-context {__version__}")
    checks = (
        check_uv(),
        check_dependencies(),
        check_database(args.db, encrypted=args.encrypted),
        check_server(),
    )
    for check in checks:
        print(f"[{check.status}] {check.name}: {check.detail}")
        if check.next_action:
            print(f"       Next: {check.next_action}")
    print(f"[info] client: {_CLIENT_NOTE}")
    return 1 if any(check.status == "fail" for check in checks) else 0
