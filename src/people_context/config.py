"""Database path and encryption-key resolution.

Precedence (first hit wins): explicit arg -> PEOPLE_CONTEXT_DB env ->
config.toml db_path -> the shared per-user default ``~/.pctx/people.db``.

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

#: Operator elevation variables for the two high-disclosure MCP capabilities. They live here
#: rather than beside the tools that consult them because more than one process boundary now
#: reads them, and two readers that disagreed about what counts as "enabled" would report a
#: gate state that does not match the one actually in force.
SENSITIVE_CONTEXT_ENV = "PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE"
EXPORT_ENV = "PEOPLE_CONTEXT_MCP_ENABLE_EXPORT"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def process_elevation_enabled(variable: str, env: Mapping[str, str] | None = None) -> bool:
    """Return whether an operator explicitly enabled a process capability.

    These environment variables are read from the local process — the MCP server that would
    expose the capability, or the CLI reporting on its own environment — and never from
    model-supplied tool arguments. They are therefore suitable as an operator elevation
    boundary for tools that must not be enabled by prompt content.
    """
    env = os.environ if env is None else env
    return env.get(variable, "").strip().lower() in _TRUTHY


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


#: Home-relative directory of the shared per-user default database.
SHARED_DB_DIRNAME = ".pctx"

def _expand(path: str | Path) -> Path:
    return Path(os.path.expanduser(path))


def _home(env: Mapping[str, str]) -> Path:
    home = env.get("HOME")
    return Path(home) if home else Path(os.path.expanduser("~"))


def _config_dir(env: Mapping[str, str]) -> Path:
    xdg = env.get("XDG_CONFIG_HOME")
    return _expand(xdg) if xdg else _home(env) / ".config"


def _config_file_db_path(env: Mapping[str, str]) -> Path | None:
    config_file = _config_dir(env) / "people-context" / "config.toml"
    try:
        with config_file.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    db_path = data.get("db_path")
    return _expand(db_path) if isinstance(db_path, str) and db_path else None


def shared_default_db_path(env: Mapping[str, str] | None = None) -> Path:
    """Return `~/.pctx/people.db` for the current user's home directory."""
    env = os.environ if env is None else env
    return _home(env) / SHARED_DB_DIRNAME / DEFAULT_DB_FILENAME


def _override_db_path(explicit: str | Path | None, env: Mapping[str, str]) -> Path | None:
    if explicit is not None:
        return _expand(explicit)
    from_env = env.get("PEOPLE_CONTEXT_DB")
    if from_env:
        return _expand(from_env)
    return _config_file_db_path(env)


def resolve_db_path(explicit: str | Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    """Resolve the database path following the documented precedence.

    `env` defaults to os.environ (injectable for tests). Expands ~ everywhere.
    Never creates files or directories.
    """
    env = os.environ if env is None else env
    override = _override_db_path(explicit, env)
    return override if override is not None else shared_default_db_path(env)


def describe_resolution(explicit: str | Path | None = None, env: Mapping[str, str] | None = None) -> list[str]:
    """Return human-readable lines showing each source checked and which one won."""
    env = os.environ if env is None else env
    lines: list[str] = []
    winner = resolve_db_path(explicit, env)

    def mark(source: str, hit: bool, detail: str) -> str:
        flag = "WON " if hit else "    "
        return f"[{flag}] {source}: {detail}"

    explicit_hit = explicit is not None
    lines.append(
        mark("explicit argument", explicit_hit, str(_expand(explicit)) if explicit is not None else "(not provided)")
    )

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

    default = shared_default_db_path(env)
    default_hit = not (explicit_hit or env_val or config_hit)
    if not default_hit:
        lines.append(mark("shared default", False, f"{default} (not consulted; an override won)"))
        lines.append(f"=> resolved: {winner}")
        return lines

    state = "exists" if os.path.lexists(default) else "absent; created on first database open"
    lines.append(mark("shared default", True, f"{default} ({state})"))
    lines.append(f"=> resolved: {winner}")
    return lines
