"""`pctx setup`: write the stdio server into an MCP client's configuration.

The server itself needs no configuration; what stops a first run is the copy-paste step between "installed"
and "my client launches it". Each target here knows one thing: where that client keeps its MCP server list
and what shape one entry takes. The entry is always the canonical invocation documented in
``docs/desktop-and-editors.md`` — ``uvx --from people-context people-context`` — so a config written here and
one written by hand are interchangeable.

Writing is conservative: an existing file is parsed, only the ``people-context`` entry is replaced, every
other server is preserved byte-for-byte in meaning, a copy of the previous file is kept beside it, and the
result is written atomically. A symlink or an unparseable file is refused rather than overwritten. Clients
that own their configuration through a CLI (Claude Code, Codex) are driven through that CLI, or the exact
command is printed when it is not installed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from people_context.config import DB_KEY_ENV

#: The one server name every target registers under, so a re-run replaces rather than duplicates.
SERVER_NAME = "people-context"

#: The canonical zero-install invocation. Kept as data so tests can assert the documented command.
SERVER_COMMAND = "uvx"
SERVER_ARGS: tuple[str, ...] = ("--from", "people-context", "people-context")

#: Targets accepted on the command line, in the order they are listed in help output.
CLIENTS: tuple[str, ...] = (
    "claude-desktop",
    "claude-code",
    "codex",
    "cursor",
    "windsurf",
    "vscode",
    "json",
)

SCOPES: tuple[str, ...] = ("user", "project")

#: Suffix of the copy kept beside a configuration file before it is rewritten.
BACKUP_SUFFIX = ".bak"


class SetupError(RuntimeError):
    """A target could not be configured safely; the message says why and nothing was written."""


@dataclass(frozen=True)
class ServerEntry:
    """The JSON object one client stores for this server."""

    command: str
    args: tuple[str, ...]
    env: dict[str, str]

    def as_json(self, *, with_type: bool) -> dict[str, object]:
        entry: dict[str, object] = {"command": self.command, "args": list(self.args)}
        if with_type:
            # VS Code requires the transport to be named; the other clients infer stdio from `command`.
            entry = {"type": "stdio", **entry}
        if self.env:
            entry["env"] = dict(self.env)
        return entry


@dataclass(frozen=True)
class FileTarget:
    """A client configured by editing one JSON file."""

    client: str
    path: Path
    root_key: str
    with_type: bool
    restart_hint: str


def build_entry(db_path: str | None, *, encrypted: bool) -> ServerEntry:
    """Build the server entry for the resolved options.

    ``PEOPLE_CONTEXT_DB`` is only pinned when the operator passed ``--db``; otherwise the server resolves the
    path the same way the CLI does. ``--encrypted`` adds the flag but never the key: the key is read from the
    server process environment only, which the caller must arrange in the client.
    """
    args = list(SERVER_ARGS)
    env: dict[str, str] = {}
    if encrypted:
        args.append("--encrypted")
    if db_path:
        env["PEOPLE_CONTEXT_DB"] = str(Path(os.path.expanduser(db_path)))
    return ServerEntry(command=SERVER_COMMAND, args=tuple(args), env=env)


def _home(env: Mapping[str, str]) -> Path:
    home = env.get("HOME")
    return Path(home) if home else Path(os.path.expanduser("~"))


def claude_desktop_config_path(env: Mapping[str, str], platform: str) -> Path:
    """Return Claude Desktop's configuration file for one platform."""
    if platform == "darwin":
        return _home(env) / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if platform.startswith("win"):
        appdata = env.get("APPDATA")
        base = Path(appdata) if appdata else _home(env) / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    xdg = env.get("XDG_CONFIG_HOME")
    base = Path(os.path.expanduser(xdg)) if xdg else _home(env) / ".config"
    return base / "Claude" / "claude_desktop_config.json"


def file_target(
    client: str,
    scope: str,
    *,
    env: Mapping[str, str],
    platform: str,
    cwd: Path,
) -> FileTarget:
    """Resolve the configuration file one JSON-configured client reads at the requested scope."""
    if client == "claude-desktop":
        if scope == "project":
            raise SetupError("Claude Desktop has no project scope; omit --scope or use --scope user.")
        return FileTarget(
            client,
            claude_desktop_config_path(env, platform),
            "mcpServers",
            False,
            "Restart Claude Desktop.",
        )
    if client == "cursor":
        path = cwd / ".cursor" / "mcp.json" if scope == "project" else _home(env) / ".cursor" / "mcp.json"
        return FileTarget(client, path, "mcpServers", False, "Reload the MCP list in Cursor settings.")
    if client == "windsurf":
        if scope == "project":
            raise SetupError("Windsurf reads only its user-level mcp_config.json; omit --scope or use --scope user.")
        path = _home(env) / ".codeium" / "windsurf" / "mcp_config.json"
        return FileTarget(client, path, "mcpServers", False, "Refresh the MCP servers panel in Windsurf.")
    if client == "vscode":
        if scope == "user":
            raise SetupError(
                "VS Code keeps user-level MCP servers in its own settings UI; run `MCP: Add Server` there, or "
                "use --scope project to write .vscode/mcp.json."
            )
        return FileTarget(client, cwd / ".vscode" / "mcp.json", "servers", True, "Run `MCP: List Servers` in VS Code.")
    raise SetupError(f"{client} is not configured through a file.")


def merged_config(existing: Mapping[str, object] | None, target: FileTarget, entry: ServerEntry) -> dict[str, object]:
    """Return the configuration document with this server set and everything else untouched."""
    document: dict[str, object] = dict(existing or {})
    servers_value = document.get(target.root_key)
    servers: dict[str, object] = dict(servers_value) if isinstance(servers_value, Mapping) else {}
    servers[SERVER_NAME] = entry.as_json(with_type=target.with_type)
    document[target.root_key] = servers
    return document


def _read_existing(path: Path) -> dict[str, object] | None:
    if path.is_symlink():
        raise SetupError(f"{path} is a symlink; refusing to write through it. Replace it with a regular file first.")
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(f"{path} is not valid JSON ({exc}); fix or move it, then re-run.") from exc
    if not isinstance(loaded, dict):
        raise SetupError(f"{path} does not hold a JSON object; refusing to replace it.")
    return loaded


def _write_atomically(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + BACKUP_SUFFIX))
    text = json.dumps(document, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def write_file_target(target: FileTarget, entry: ServerEntry, *, dry_run: bool) -> list[str]:
    """Merge the entry into the target file, or describe what would be written."""
    existing = _read_existing(target.path)
    document = merged_config(existing, target, entry)
    rendered = json.dumps(document, indent=2)
    if dry_run:
        return [f"Would write {target.path}:", rendered]
    _write_atomically(target.path, document)
    lines = [f"Wrote `{SERVER_NAME}` into {target.path}."]
    if existing is not None:
        lines.append(f"Previous file kept at {target.path}{BACKUP_SUFFIX}.")
    lines.append(target.restart_hint)
    return lines


def cli_command(client: str, scope: str, entry: ServerEntry) -> list[str]:
    """Return the client's own `mcp add` command for this server."""
    if client == "claude-code":
        command = ["claude", "mcp", "add", "--scope", scope]
        for key, value in entry.env.items():
            command.extend(["--env", f"{key}={value}"])
        command.extend([SERVER_NAME, "--", entry.command, *entry.args])
        return command
    if client == "codex":
        if scope == "project":
            raise SetupError("Codex registers MCP servers per user; omit --scope or use --scope user.")
        command = ["codex", "mcp", "add"]
        for key, value in entry.env.items():
            command.extend(["--env", f"{key}={value}"])
        command.extend([SERVER_NAME, "--", entry.command, *entry.args])
        return command
    raise SetupError(f"{client} is not configured through a client CLI.")


def run_cli_target(client: str, scope: str, entry: ServerEntry, *, dry_run: bool) -> list[str]:
    """Drive the client's CLI, or print the exact command when it is not installed."""
    command = cli_command(client, scope, entry)
    rendered = " ".join(_quote(part) for part in command)
    if dry_run:
        return [f"Would run: {rendered}"]
    if shutil.which(command[0]) is None:
        return [f"`{command[0]}` is not on PATH. Run this once it is:", f"  {rendered}"]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)  # noqa: S603
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise SetupError(f"`{rendered}` failed with exit code {completed.returncode}: {detail}")
    output = completed.stdout.strip()
    lines = [f"Registered `{SERVER_NAME}` with {command[0]}."]
    if output:
        lines.append(output)
    return lines


def _quote(part: str) -> str:
    return part if all(ch.isalnum() or ch in "-_./=:@" for ch in part) else json.dumps(part)


def generic_json(entry: ServerEntry) -> list[str]:
    """Return the client-agnostic snippet for clients not listed here."""
    document = {"mcpServers": {SERVER_NAME: entry.as_json(with_type=False)}}
    return [json.dumps(document, indent=2)]


def run_setup(
    client: str,
    *,
    scope: str,
    db_path: str | None,
    encrypted: bool,
    dry_run: bool,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    cwd: Path | None = None,
) -> list[str]:
    """Configure one client and return the lines to print. Raises SetupError instead of writing partially."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    cwd = Path.cwd() if cwd is None else cwd
    entry = build_entry(db_path, encrypted=encrypted)
    if client == "json":
        lines = generic_json(entry)
    elif client in ("claude-code", "codex"):
        lines = run_cli_target(client, scope, entry, dry_run=dry_run)
    else:
        target = file_target(client, scope, env=env, platform=platform, cwd=cwd)
        lines = write_file_target(target, entry, dry_run=dry_run)
    if encrypted:
        lines.append(
            f"Encrypted mode: the client must launch the server with {DB_KEY_ENV} set. "
            "The key is never written to a configuration file; add it to the client's environment yourself."
        )
    return lines


def cmd_setup(args: argparse.Namespace) -> int:
    """`pctx setup CLIENT`: write or print the client configuration for the stdio server."""
    try:
        lines = run_setup(
            args.client,
            scope=args.scope,
            db_path=args.db,
            encrypted=args.encrypted,
            dry_run=args.dry_run,
        )
    except SetupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0
