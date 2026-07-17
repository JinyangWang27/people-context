"""Tests for the DB-path resolution precedence ladder.

All filesystem roots point into tmp_path via an injected env dict, so the real
home directory is never touched.
"""

from __future__ import annotations

from pathlib import Path

from people_context.config import DEFAULT_DB_FILENAME, describe_resolution, resolve_db_path


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
    # Falls through to XDG data dir without raising.
    assert resolve_db_path(env=env) == Path(env["XDG_DATA_HOME"]) / "people-context" / DEFAULT_DB_FILENAME


def test_openclaw_workspace_env_detected(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    workspace = tmp_path / "custom_workspace"
    workspace.mkdir()
    env["OPENCLAW_WORKSPACE"] = str(workspace)
    assert resolve_db_path(env=env) == workspace / "people-context" / DEFAULT_DB_FILENAME


def test_openclaw_home_workspace_detected(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    workspace = Path(env["HOME"]) / ".openclaw" / "workspace"
    workspace.mkdir(parents=True)
    assert resolve_db_path(env=env) == workspace / "people-context" / DEFAULT_DB_FILENAME


def test_xdg_fallback(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    assert resolve_db_path(env=env) == Path(env["XDG_DATA_HOME"]) / "people-context" / DEFAULT_DB_FILENAME


def test_does_not_create_anything(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    resolve_db_path(env=env)
    assert not (Path(env["XDG_DATA_HOME"]) / "people-context").exists()


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
