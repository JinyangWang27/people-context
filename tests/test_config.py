"""Tests for the DB-path resolution precedence ladder.

All filesystem roots point into tmp_path via an injected env dict, so the real
home directory is never touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from people_context.config import (
    DB_KEY_ENV,
    DEFAULT_DB_FILENAME,
    LegacyDatabaseTransitionError,
    MissingDatabaseKeyError,
    blocking_legacy_databases,
    describe_resolution,
    legacy_db_candidates,
    resolve_db_key,
    resolve_db_path,
    resolve_openable_db_path,
    shared_default_db_path,
)


def _base_env(tmp_path: Path) -> dict[str, str]:
    """Env with home + XDG dirs under tmp_path and no workspace/db overrides."""
    home = tmp_path / "home"
    home.mkdir()
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }


def _write_config(env: dict[str, str], db_path: str) -> None:
    config_dir = Path(env["XDG_CONFIG_HOME"]) / "people-context"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(f'db_path = "{db_path}"\n', encoding="utf-8")


def test_explicit_wins(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["PEOPLE_CONTEXT_DB"] = str(tmp_path / "from_env.db")
    _write_config(env, str(tmp_path / "from_config.db"))
    result = resolve_db_path(explicit=tmp_path / "explicit.db", env=env)
    assert result == tmp_path / "explicit.db"


def test_env_var_wins_over_config(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["PEOPLE_CONTEXT_DB"] = str(tmp_path / "from_env.db")
    _write_config(env, str(tmp_path / "from_config.db"))
    assert resolve_db_path(env=env) == tmp_path / "from_env.db"


def test_config_file_db_path_honored(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    _write_config(env, str(tmp_path / "from_config.db"))
    assert resolve_db_path(env=env) == tmp_path / "from_config.db"


def test_config_file_missing_or_invalid_tolerated(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    config_dir = Path(env["XDG_CONFIG_HOME"]) / "people-context"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text("this is = not valid toml [[[", encoding="utf-8")
    # Falls through to the shared default without raising.
    assert resolve_db_path(env=env) == _shared(env)


def _shared(env: dict[str, str]) -> Path:
    return Path(env["HOME"]) / ".pctx" / DEFAULT_DB_FILENAME


def _xdg_legacy(env: dict[str, str]) -> Path:
    return Path(env["XDG_DATA_HOME"]) / "people-context" / DEFAULT_DB_FILENAME


def _make_file(path: Path, content: bytes = b"SQLite format 3\x00fictional") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path): (path.read_bytes() if path.is_file() else b"", path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
    }


def test_shared_default_ignores_workspaces_custom_xdg_and_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _base_env(tmp_path)
    workspace = tmp_path / "custom_workspace"
    workspace.mkdir()
    (Path(env["HOME"]) / ".openclaw" / "workspace").mkdir(parents=True)
    env["OPENCLAW_WORKSPACE"] = str(workspace)
    env["XDG_DATA_HOME"] = str(tmp_path / "custom data")
    for cwd in (tmp_path, workspace):
        monkeypatch.chdir(cwd)
        assert resolve_db_path(env=env) == _shared(env)
    assert shared_default_db_path(env) == _shared(env)


def test_home_expansion_is_preserved_for_overrides(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    home = Path(env["HOME"])
    env["PEOPLE_CONTEXT_DB"] = "~/chosen.db"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HOME", str(home))
        assert resolve_db_path(env=env) == home / "chosen.db"
        assert resolve_db_path("~/explicit.db", env=env) == home / "explicit.db"


def test_resolution_and_diagnostics_create_nothing(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    legacy = _make_file(_xdg_legacy(env))
    before = _snapshot(tmp_path)
    resolve_db_path(env=env)
    blocking_legacy_databases(env=env)
    describe_resolution(env=env)
    with pytest.raises(LegacyDatabaseTransitionError):
        resolve_openable_db_path(env=env)
    assert _snapshot(tmp_path) == before
    assert not _shared(env).parent.exists()
    assert legacy.exists()


def test_fresh_environment_without_legacy_is_not_blocked(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    assert blocking_legacy_databases(env=env) == []
    assert resolve_openable_db_path(env=env) == _shared(env)


@pytest.mark.parametrize("candidate", ["openclaw_env", "openclaw_home", "xdg_custom", "xdg_default"])
def test_each_legacy_candidate_blocks_the_fresh_default(tmp_path: Path, candidate: str) -> None:
    env = _base_env(tmp_path)
    home = Path(env["HOME"])
    if candidate == "openclaw_env":
        workspace = tmp_path / "workspace"
        env["OPENCLAW_WORKSPACE"] = str(workspace)
        legacy = _make_file(workspace / "people-context" / DEFAULT_DB_FILENAME)
    elif candidate == "openclaw_home":
        legacy = _make_file(home / ".openclaw" / "workspace" / "people-context" / DEFAULT_DB_FILENAME)
    elif candidate == "xdg_custom":
        legacy = _make_file(_xdg_legacy(env))
    else:
        del env["XDG_DATA_HOME"]
        legacy = _make_file(home / ".local" / "share" / "people-context" / DEFAULT_DB_FILENAME)

    assert blocking_legacy_databases(env=env) == [legacy]
    with pytest.raises(LegacyDatabaseTransitionError) as exc_info:
        resolve_openable_db_path(env=env)
    message = str(exc_info.value)
    assert str(legacy) in message
    assert "--db" in message and "PEOPLE_CONTEXT_DB" in message and "db_path" in message
    assert "other agent environments were not inventoried" in message


def test_multiple_legacy_databases_all_reported_and_require_explicit_choice(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    workspace = tmp_path / "workspace"
    env["OPENCLAW_WORKSPACE"] = str(workspace)
    found = [
        _make_file(workspace / "people-context" / DEFAULT_DB_FILENAME),
        _make_file(Path(env["HOME"]) / ".openclaw" / "workspace" / "people-context" / DEFAULT_DB_FILENAME),
        _make_file(_xdg_legacy(env)),
    ]
    assert blocking_legacy_databases(env=env) == found
    with pytest.raises(LegacyDatabaseTransitionError) as exc_info:
        resolve_openable_db_path(env=env)
    assert all(str(path) in str(exc_info.value) for path in found)
    assert "Several legacy databases" in str(exc_info.value)


def test_workspace_directory_without_database_does_not_block(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "people-context").mkdir(parents=True)
    env["OPENCLAW_WORKSPACE"] = str(workspace)
    (Path(env["HOME"]) / ".openclaw" / "workspace").mkdir(parents=True)
    assert blocking_legacy_databases(env=env) == []


def test_encrypted_looking_legacy_file_blocks_without_being_read(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    legacy = _make_file(_xdg_legacy(env), bytes(range(256)) * 4)
    legacy.chmod(0o000)
    try:
        assert blocking_legacy_databases(env=env) == [legacy]
    finally:
        legacy.chmod(0o600)


def test_existing_shared_default_wins_despite_legacy_files(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    _make_file(_xdg_legacy(env))
    _make_file(_shared(env))
    assert blocking_legacy_databases(env=env) == []
    assert resolve_openable_db_path(env=env) == _shared(env)


def test_explicit_environment_and_config_overrides_bypass_the_guard(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    legacy = _make_file(_xdg_legacy(env))
    assert resolve_openable_db_path(legacy, env=env) == legacy
    assert resolve_openable_db_path(_shared(env), env=env) == _shared(env)
    assert resolve_openable_db_path(env={**env, "PEOPLE_CONTEXT_DB": str(legacy)}) == legacy
    _write_config(env, str(legacy))
    assert resolve_openable_db_path(env=env) == legacy


@pytest.mark.parametrize("variable", ["XDG_DATA_HOME", "OPENCLAW_WORKSPACE"])
def test_store_hidden_in_another_environment_is_preserved_only_by_its_pin(tmp_path: Path, variable: str) -> None:
    """Discovery cannot see another client's custom location; the pre-upgrade pin is what protects it."""
    home = tmp_path / "home"
    home.mkdir()
    shared_config = str(tmp_path / "config")
    custom = tmp_path / "agent-a-custom"
    # Both legacy layouts store `<root>/people-context/people.db`.
    hidden = _make_file(custom / "people-context" / DEFAULT_DB_FILENAME)
    agent_a_old = {"HOME": str(home), "XDG_CONFIG_HOME": shared_config, variable: str(custom)}
    agent_b = {"HOME": str(home), "XDG_CONFIG_HOME": shared_config}

    # Agent B's environment cannot discover agent A's store: nothing blocks it.
    assert hidden not in legacy_db_candidates(agent_b)
    assert blocking_legacy_databases(env=agent_b) == []
    # Inventory taken with the old version in A's real environment, then pinned before upgrading.
    assert hidden in legacy_db_candidates(agent_a_old)
    agent_a_pinned = {**agent_a_old, "PEOPLE_CONTEXT_DB": str(hidden)}
    assert resolve_openable_db_path(env=agent_a_pinned) == hidden

    # B creates the shared default; A's pin still selects its own store.
    _make_file(_shared(agent_b))
    assert resolve_openable_db_path(env=agent_b) == _shared(agent_b)
    assert resolve_openable_db_path(env=agent_a_pinned) == hidden
    assert hidden.read_bytes().startswith(b"SQLite format 3")


def test_describe_resolution_mentions_winner(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["PEOPLE_CONTEXT_DB"] = str(tmp_path / "from_env.db")
    lines = describe_resolution(env=env)
    joined = "\n".join(lines)
    assert str(tmp_path / "from_env.db") in joined
    won_lines = [line for line in lines if line.startswith("[WON ]")]
    assert len(won_lines) == 1
    assert "PEOPLE_CONTEXT_DB" in won_lines[0]
    assert any(line.startswith("=> resolved:") for line in lines)
    assert not any(line.startswith("=> blocked:") for line in lines)


def test_describe_resolution_distinguishes_selected_default_from_blocked_transition(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    selected = "\n".join(describe_resolution(env=env))
    assert f"[WON ] shared default: {_shared(env)} (absent; created on first database open)" in selected
    assert f"legacy location: {_xdg_legacy(env)} (not found)" in selected
    assert "=> blocked:" not in selected
    assert "other agent environments were not inventoried" in selected

    _make_file(_xdg_legacy(env))
    blocked = describe_resolution(env=env)
    joined = "\n".join(blocked)
    assert "BLOCKED" in joined
    assert f"legacy location: {_xdg_legacy(env)} (FOUND)" in joined
    assert any(line.startswith("=> blocked:") for line in blocked)
    assert "other agent environments were not inventoried" in joined

    _make_file(_shared(env))
    assert f"shared default: {_shared(env)} (exists)" in "\n".join(describe_resolution(env=env))


# -- optional at-rest encryption key ------------------------------------------


def test_resolve_db_key_returns_the_environment_value_verbatim() -> None:
    key = "  padded but not empty  "
    assert resolve_db_key(env={DB_KEY_ENV: key}) == key


@pytest.mark.parametrize("env", [{}, {DB_KEY_ENV: ""}, {DB_KEY_ENV: " "}, {DB_KEY_ENV: "\t\n"}])
def test_resolve_db_key_refuses_missing_empty_or_whitespace_values(env: dict[str, str]) -> None:
    with pytest.raises(MissingDatabaseKeyError) as exc_info:
        resolve_db_key(env=env)
    assert DB_KEY_ENV in str(exc_info.value)


def test_resolve_db_key_ignores_config_files_and_db_path_sources(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    _write_config(env, str(tmp_path / "from_config.db"))
    (Path(env["XDG_CONFIG_HOME"]) / "people-context" / "config.toml").write_text(
        f'db_path = "{tmp_path / "from_config.db"}"\ndb_key = "never-read"\n', encoding="utf-8"
    )

    with pytest.raises(MissingDatabaseKeyError):
        resolve_db_key(env=env)
