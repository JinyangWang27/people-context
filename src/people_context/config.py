"""Database path and encryption-key resolution.

Precedence (first hit wins): explicit arg -> PEOPLE_CONTEXT_DB env ->
config.toml db_path -> agent-workspace auto-detect -> XDG data dir.

The optional at-rest encryption key has no such ladder: it comes only from the
``PEOPLE_CONTEXT_DB_KEY`` environment variable, never from a flag value or a
config file, so it is never recorded in a shell history, a process listing, or a
file on disk beside the database it protects.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path

DEFAULT_DB_FILENAME = "people.db"

#: The only accepted source of the optional at-rest encryption key.
DB_KEY_ENV = "PEOPLE_CONTEXT_DB_KEY"


class MissingDatabaseKeyError(RuntimeError):
    """Raised when encryption is requested without a usable environment key.

    The message never contains key material — only the variable name.
    """


def resolve_db_key(env: Mapping[str, str] | None = None) -> str:
    """Return the at-rest encryption key from the environment, or refuse.

    `env` defaults to os.environ (injectable for tests). An unset, empty, or
    whitespace-only value is refused; there is no fallback to plaintext and no
    other configuration source.
    """
    env = os.environ if env is None else env
    key = env.get(DB_KEY_ENV)
    if key is None or not key.strip():
        raise MissingDatabaseKeyError(
            f"Encrypted mode requires a non-empty {DB_KEY_ENV} environment variable. "
            "Refusing to continue; plaintext is never used as a fallback."
        )
    return key

# Agent workspaces to auto-detect, in priority order. Each entry maps an optional
# environment variable (checked first) to a home-relative fallback directory.
# Adding a new agent workspace is a one-line addition here.
WORKSPACE_CANDIDATES: list[tuple[str | None, str]] = [
    ("OPENCLAW_WORKSPACE", ".openclaw/workspace"),
]


def _expand(path: str | Path) -> Path:
    return Path(os.path.expanduser(path))


def _home(env: Mapping[str, str]) -> Path:
    home = env.get("HOME")
    return Path(home) if home else Path(os.path.expanduser("~"))


def _config_dir(env: Mapping[str, str]) -> Path:
    xdg = env.get("XDG_CONFIG_HOME")
    return _expand(xdg) if xdg else _home(env) / ".config"


def _data_dir(env: Mapping[str, str]) -> Path:
    xdg = env.get("XDG_DATA_HOME")
    return _expand(xdg) if xdg else _home(env) / ".local" / "share"


def _config_file_db_path(env: Mapping[str, str]) -> Path | None:
    config_file = _config_dir(env) / "people-context" / "config.toml"
    try:
        with config_file.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    db_path = data.get("db_path")
    return _expand(db_path) if isinstance(db_path, str) and db_path else None


def _workspace_db_path(env: Mapping[str, str]) -> Path | None:
    for env_var, home_relative in WORKSPACE_CANDIDATES:
        if env_var:
            value = env.get(env_var)
            if value:
                candidate = _expand(value)
                if candidate.is_dir():
                    return candidate / "people-context" / DEFAULT_DB_FILENAME
        fallback = _home(env) / home_relative
        if fallback.is_dir():
            return fallback / "people-context" / DEFAULT_DB_FILENAME
    return None


def resolve_db_path(explicit: str | Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    """Resolve the database path following the documented precedence.

    `env` defaults to os.environ (injectable for tests). Expands ~ everywhere.
    Never creates files or directories.
    """
    env = os.environ if env is None else env

    if explicit is not None:
        return _expand(explicit)

    from_env = env.get("PEOPLE_CONTEXT_DB")
    if from_env:
        return _expand(from_env)

    from_config = _config_file_db_path(env)
    if from_config is not None:
        return from_config

    from_workspace = _workspace_db_path(env)
    if from_workspace is not None:
        return from_workspace

    return _data_dir(env) / "people-context" / DEFAULT_DB_FILENAME


def describe_resolution(explicit: str | Path | None = None, env: Mapping[str, str] | None = None) -> list[str]:
    """Return human-readable lines showing each source checked and which one won."""
    env = os.environ if env is None else env
    lines: list[str] = []
    winner = resolve_db_path(explicit, env)

    def mark(source: str, hit: bool, detail: str) -> str:
        flag = "WON " if hit else "    "
        return f"[{flag}] {source}: {detail}"

    explicit_hit = explicit is not None
    lines.append(mark("explicit argument", explicit_hit, str(_expand(explicit)) if explicit_hit else "(not provided)"))

    env_val = env.get("PEOPLE_CONTEXT_DB")
    env_hit = not explicit_hit and bool(env_val)
    lines.append(mark("PEOPLE_CONTEXT_DB env", env_hit, env_val or "(unset)"))

    config_path = None if explicit_hit or env_val else _config_file_db_path(env)
    config_hit = config_path is not None
    config_file = _config_dir(env) / "people-context" / "config.toml"
    lines.append(
        mark(
            "config.toml db_path",
            config_hit,
            str(config_path) if config_hit else f"(no db_path in {config_file})",
        )
    )

    workspace_path = None if explicit_hit or env_val or config_hit else _workspace_db_path(env)
    workspace_hit = workspace_path is not None
    lines.append(
        mark(
            "agent workspace",
            workspace_hit,
            str(workspace_path) if workspace_hit else "(no workspace directory found)",
        )
    )

    xdg_hit = not (explicit_hit or env_val or config_hit or workspace_hit)
    lines.append(mark("XDG data dir", xdg_hit, str(_data_dir(env) / "people-context" / DEFAULT_DB_FILENAME)))

    lines.append(f"=> resolved: {winner}")
    return lines
