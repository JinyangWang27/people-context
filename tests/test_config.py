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
    MissingDatabaseKeyError,
    describe_resolution,
    resolve_db_key,
    resolve_db_path,
    shared_default_db_path,
)


def _base_env(tmp_path: Path) -> dict[str, str]:
    """Env with home + config dir under tmp_path and no db overrides."""
    home = tmp_path / "home"
    home.mkdir()
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
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


def _make_file(path: Path, content: bytes = b"SQLite format 3\x00fictional") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path): (path.read_bytes() if path.is_file() else b"", path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
    }


def test_shared_default_ignores_the_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _base_env(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
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
    before = _snapshot(tmp_path)
    resolve_db_path(env=env)
    describe_resolution(env=env)
    assert _snapshot(tmp_path) == before
    assert not _shared(env).parent.exists()


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


def test_describe_resolution_reports_whether_the_shared_default_exists(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    selected = "\n".join(describe_resolution(env=env))
    assert f"[WON ] shared default: {_shared(env)} (absent; created on first database open)" in selected

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
