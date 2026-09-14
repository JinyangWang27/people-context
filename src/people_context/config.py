"""Database path and encryption-key resolution.

Precedence (first hit wins): explicit arg -> PEOPLE_CONTEXT_DB env ->
config.toml db_path -> the shared per-user default ``~/.pctx/people.db``.

Earlier releases fell back to an OpenClaw workspace or the XDG data directory. Those
locations are no longer selected; while one holds a database and the shared default does
not exist yet, opening refuses instead of creating a fresh store beside it.

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

#: Where current-environment legacy databases are documented for an explicit transition.
TRANSITION_DOCS = "docs/cli.md#upgrading-to-the-shared-default"


class LegacyDatabaseTransitionError(RuntimeError):
    """Raised instead of creating the shared default while a legacy database is visible.

    An earlier release selected a workspace or XDG data path implicitly. Creating a fresh
    `~/.pctx/people.db` next to such a store would quietly strand its records, so the user has
    to choose explicitly. The message names only local paths the current process can already see.
    """

    def __init__(self, default: Path, legacy: list[Path]) -> None:
        self.default = default
        self.legacy = legacy
        found = "\n".join(f"  - {path}" for path in legacy)
        choice = (
            "Several legacy databases exist; choose one explicitly — they are never selected by discovery order "
            "or merged."
            if len(legacy) > 1
            else "Select it explicitly to keep using it."
        )
        super().__init__(
            f"Refusing to create the shared default database {default} because a database from an earlier "
            f"release exists at:\n{found}\n{choice} Pass --db PATH, set PEOPLE_CONTEXT_DB, or set db_path in "
            "the people-context config.toml; or migrate deliberately with every client stopped, then retry. "
            f"See {TRANSITION_DOCS}. Only this process's environment was checked; other agent environments "
            "were not inventoried."
        )


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


def shared_default_db_path(env: Mapping[str, str] | None = None) -> Path:
    """Return `~/.pctx/people.db` for the current user's home directory."""
    env = os.environ if env is None else env
    return _home(env) / SHARED_DB_DIRNAME / DEFAULT_DB_FILENAME


def legacy_db_candidates(env: Mapping[str, str] | None = None) -> list[Path]:
    """Return every legacy default location visible to this environment, in documented order.

    Workspace candidates count only when their workspace directory exists, matching where an
    earlier release could have put a database. Nothing is created or opened.
    """
    env = os.environ if env is None else env
    candidates: list[Path] = []
    workspace = env.get("OPENCLAW_WORKSPACE")
    if workspace and _expand(workspace).is_dir():
        candidates.append(_expand(workspace) / "people-context" / DEFAULT_DB_FILENAME)
    home_workspace = _home(env) / ".openclaw" / "workspace"
    if home_workspace.is_dir():
        candidates.append(home_workspace / "people-context" / DEFAULT_DB_FILENAME)
    candidates.append(_data_dir(env) / "people-context" / DEFAULT_DB_FILENAME)
    return list(dict.fromkeys(candidates))


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
    Never creates files or directories, and never checks legacy locations: use
    :func:`resolve_openable_db_path` before opening.
    """
    env = os.environ if env is None else env
    override = _override_db_path(explicit, env)
    return override if override is not None else shared_default_db_path(env)


def blocking_legacy_databases(explicit: str | Path | None = None, env: Mapping[str, str] | None = None) -> list[Path]:
    """Return the legacy databases that forbid implicitly creating the shared default.

    Empty when an explicit argument, environment variable, or config file selects the path, or
    when the shared default already exists. Paths are only stat'ed (a dangling link still
    counts), so encrypted stores are never read.
    """
    env = os.environ if env is None else env
    if _override_db_path(explicit, env) is not None or os.path.lexists(shared_default_db_path(env)):
        return []
    return [path for path in legacy_db_candidates(env) if os.path.lexists(path)]


def resolve_openable_db_path(explicit: str | Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    """Resolve the path a database-opening command may use, refusing a blocked transition."""
    env = os.environ if env is None else env
    legacy = blocking_legacy_databases(explicit, env)
    if legacy:
        raise LegacyDatabaseTransitionError(shared_default_db_path(env), legacy)
    return resolve_db_path(explicit, env)


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

    legacy = blocking_legacy_databases(explicit, env)
    if os.path.lexists(default):
        state = "exists"
    elif legacy:
        state = "BLOCKED: absent, and a legacy database exists"
    else:
        state = "absent; created on first database open"
    lines.append(mark("shared default", True, f"{default} ({state})"))
    for candidate in legacy_db_candidates(env):
        found = os.path.lexists(candidate)
        lines.append(f"[     ] legacy location: {candidate} ({'FOUND' if found else 'not found'})")
    lines.append(f"=> resolved: {winner}")
    if legacy:
        lines.append(
            "=> blocked: commands that open the database refuse rather than create a fresh default. Select a "
            "legacy database with --db, PEOPLE_CONTEXT_DB, or config.toml db_path, or migrate deliberately; "
            f"see {TRANSITION_DOCS}."
        )
    lines.append("=> note: only this environment was checked; other agent environments were not inventoried.")
    return lines
