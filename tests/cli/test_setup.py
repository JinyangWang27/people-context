"""`pctx setup`: writing the stdio server into client configurations without clobbering anything."""

from __future__ import annotations

import json
import os
import shlex
import stat
from pathlib import Path

import pytest

from people_context import cli
from people_context.cli import setup
from people_context.cli.setup import (
    BACKUP_SUFFIX,
    SERVER_ARGS,
    SetupError,
    build_entry,
    claude_desktop_config_path,
    cli_command,
    file_target,
    merged_config,
    run_setup,
)

CANONICAL = {"command": "uvx", "args": list(SERVER_ARGS)}


def _env(home: Path, **extra: str) -> dict[str, str]:
    return {"HOME": str(home), **extra}


class TestEntry:
    def test_default_entry_is_the_documented_zero_install_invocation(self) -> None:
        entry = build_entry(None, encrypted=False)

        assert entry.as_json(with_type=False) == CANONICAL
        assert SERVER_ARGS == ("--from", "people-context", "people-context")

    def test_explicit_db_becomes_the_server_environment_not_an_argument(self, tmp_path: Path) -> None:
        entry = build_entry(str(tmp_path / "people.db"), encrypted=False)

        assert entry.env == {"PEOPLE_CONTEXT_DB": str(tmp_path / "people.db")}
        assert "--db" not in entry.args

    def test_encrypted_adds_the_flag_but_never_a_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PEOPLE_CONTEXT_DB_KEY", "secret")
        entry = build_entry(None, encrypted=True)

        assert entry.args[-1] == "--encrypted"
        assert "secret" not in json.dumps(entry.as_json(with_type=False))
        assert "PEOPLE_CONTEXT_DB_KEY" not in entry.env

    def test_vscode_entry_names_the_transport(self) -> None:
        assert build_entry(None, encrypted=False).as_json(with_type=True)["type"] == "stdio"


class TestPaths:
    def test_claude_desktop_path_per_platform(self, tmp_path: Path) -> None:
        env = _env(tmp_path, APPDATA=str(tmp_path / "Roaming"))

        assert claude_desktop_config_path(env, "darwin") == (
            tmp_path / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        )
        assert (
            claude_desktop_config_path(env, "win32") == tmp_path / "Roaming" / "Claude" / "claude_desktop_config.json"
        )
        assert (
            claude_desktop_config_path(env, "linux") == tmp_path / ".config" / "Claude" / "claude_desktop_config.json"
        )

    def test_linux_honours_xdg_config_home(self, tmp_path: Path) -> None:
        env = _env(tmp_path, XDG_CONFIG_HOME=str(tmp_path / "xdg"))

        assert claude_desktop_config_path(env, "linux") == tmp_path / "xdg" / "Claude" / "claude_desktop_config.json"

    @pytest.mark.parametrize(
        ("client", "scope", "relative"),
        [
            ("cursor", "user", Path(".cursor/mcp.json")),
            ("windsurf", "user", Path(".codeium/windsurf/mcp_config.json")),
        ],
    )
    def test_user_scope_files_live_under_home(self, tmp_path: Path, client: str, scope: str, relative: Path) -> None:
        target = file_target(client, scope, env=_env(tmp_path), platform="linux", cwd=tmp_path / "proj")

        assert target.path == tmp_path / relative

    def test_project_scope_files_live_under_cwd(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"

        cursor = file_target("cursor", "project", env=_env(tmp_path), platform="linux", cwd=project)
        vscode = file_target("vscode", "project", env=_env(tmp_path), platform="linux", cwd=project)

        assert cursor.path == project / ".cursor" / "mcp.json"
        assert vscode.path == project / ".vscode" / "mcp.json"
        assert vscode.root_key == "servers"

    @pytest.mark.parametrize(
        ("client", "scope"),
        [("claude-desktop", "project"), ("windsurf", "project"), ("vscode", "user")],
    )
    def test_unsupported_scopes_are_refused_with_guidance(self, tmp_path: Path, client: str, scope: str) -> None:
        with pytest.raises(SetupError):
            file_target(client, scope, env=_env(tmp_path), platform="linux", cwd=tmp_path)


class TestMerge:
    def test_other_servers_and_unrelated_keys_survive(self, tmp_path: Path) -> None:
        target = file_target("cursor", "project", env=_env(tmp_path), platform="linux", cwd=tmp_path)
        existing = {"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}

        merged = merged_config(existing, target, build_entry(None, encrypted=False))

        assert merged["theme"] == "dark"
        assert merged["mcpServers"] == {"other": {"command": "x"}, "people-context": CANONICAL}

    def test_rerun_replaces_rather_than_duplicates(self, tmp_path: Path) -> None:
        target = file_target("cursor", "project", env=_env(tmp_path), platform="linux", cwd=tmp_path)
        entry = build_entry(None, encrypted=False)

        once = merged_config(None, target, entry)
        twice = merged_config(once, target, entry)

        assert twice == once


class TestWrite:
    def test_writes_a_fresh_file_creating_parents(self, tmp_path: Path) -> None:
        lines = run_setup(
            "claude-desktop",
            scope="user",
            db_path=None,
            encrypted=False,
            dry_run=False,
            env=_env(tmp_path),
            platform="linux",
            cwd=tmp_path,
        )
        path = tmp_path / ".config" / "Claude" / "claude_desktop_config.json"

        assert json.loads(path.read_text()) == {"mcpServers": {"people-context": CANONICAL}}
        assert any(str(path) in line for line in lines)
        assert not path.with_name(path.name + BACKUP_SUFFIX).exists()

    def test_existing_file_is_merged_and_backed_up(self, tmp_path: Path) -> None:
        path = tmp_path / ".cursor" / "mcp.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))

        run_setup(
            "cursor",
            scope="user",
            db_path=None,
            encrypted=False,
            dry_run=False,
            env=_env(tmp_path),
            platform="linux",
            cwd=tmp_path,
        )

        assert set(json.loads(path.read_text())["mcpServers"]) == {"other", "people-context"}
        backup = json.loads(path.with_name(path.name + BACKUP_SUFFIX).read_text())
        assert backup == {"mcpServers": {"other": {"command": "x"}}}

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        lines = run_setup(
            "cursor",
            scope="project",
            db_path=None,
            encrypted=False,
            dry_run=True,
            env=_env(tmp_path),
            platform="linux",
            cwd=tmp_path,
        )

        assert not (tmp_path / ".cursor").exists()
        assert lines[0].startswith("Would write")
        assert json.loads("\n".join(lines[1:])) == {"mcpServers": {"people-context": CANONICAL}}

    def test_invalid_json_is_refused_not_replaced(self, tmp_path: Path) -> None:
        path = tmp_path / ".cursor" / "mcp.json"
        path.parent.mkdir()
        path.write_text("{not json")

        with pytest.raises(SetupError):
            run_setup(
                "cursor",
                scope="user",
                db_path=None,
                encrypted=False,
                dry_run=False,
                env=_env(tmp_path),
                platform="linux",
                cwd=tmp_path,
            )
        assert path.read_text() == "{not json"

    def test_symlinked_config_is_refused(self, tmp_path: Path) -> None:
        real = tmp_path / "elsewhere.json"
        real.write_text("{}")
        link = tmp_path / ".cursor" / "mcp.json"
        link.parent.mkdir()
        link.symlink_to(real)

        with pytest.raises(SetupError, match="symlink"):
            run_setup(
                "cursor",
                scope="user",
                db_path=None,
                encrypted=False,
                dry_run=False,
                env=_env(tmp_path),
                platform="linux",
                cwd=tmp_path,
            )
        assert real.read_text() == "{}"

    def test_encrypted_setup_adds_the_flag_and_never_a_key(self, tmp_path: Path) -> None:
        lines = run_setup(
            "cursor",
            scope="project",
            db_path=None,
            encrypted=True,
            dry_run=True,
            env=_env(tmp_path),
            platform="linux",
            cwd=tmp_path,
        )

        joined = "\n".join(lines)
        assert '"--encrypted"' in joined
        assert "PEOPLE_CONTEXT_DB_KEY" not in joined


class TestClientClis:
    def test_claude_code_command_shape(self, tmp_path: Path) -> None:
        entry = build_entry(str(tmp_path / "p.db"), encrypted=False)

        command = cli_command("claude-code", "project", entry)

        assert command[:5] == ["claude", "mcp", "add", "--scope", "project"]
        assert "--env" in command and f"PEOPLE_CONTEXT_DB={tmp_path / 'p.db'}" in command
        assert command[-4:] == ["uvx", "--from", "people-context", "people-context"]
        assert command[command.index("--") - 1] == "people-context"

    def test_codex_has_no_project_scope(self) -> None:
        with pytest.raises(SetupError):
            cli_command("codex", "project", build_entry(None, encrypted=False))

    def test_missing_client_binary_prints_the_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(setup.shutil, "which", lambda _name: None)

        lines = run_setup("claude-code", scope="user", db_path=None, encrypted=False, dry_run=False)

        assert "not on PATH" in lines[0]
        assert "claude mcp add --scope user people-context -- uvx --from people-context people-context" in lines[1]

    def test_present_client_binary_is_invoked_without_a_shell(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = tmp_path / "argv.txt"
        fake = tmp_path / "bin" / "codex"
        fake.parent.mkdir()
        fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{record}"\necho added\n')
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ.get('PATH', '')}")

        lines = run_setup("codex", scope="user", db_path=None, encrypted=False, dry_run=False)

        assert record.read_text().split("\n")[:3] == ["mcp", "add", "people-context"]
        assert lines[0].startswith("Registered")
        assert "added" in lines


class TestCommandLine:
    def test_json_target_prints_the_generic_snippet(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["setup", "json"]) == 0

        assert json.loads(capsys.readouterr().out) == {"mcpServers": {"people-context": CANONICAL}}

    def test_setup_needs_no_database(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        missing = tmp_path / "never-created.db"

        assert cli.main(["--db", str(missing), "setup", "json"]) == 0

        assert not missing.exists()
        assert json.loads(capsys.readouterr().out)["mcpServers"]["people-context"]["env"] == {
            "PEOPLE_CONTEXT_DB": str(missing)
        }

    def test_errors_exit_one_with_the_reason(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["setup", "windsurf", "--scope", "project"]) == 1

        assert "Error:" in capsys.readouterr().err

    def test_encrypted_json_stays_one_document_with_the_key_notice_on_stderr(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("PEOPLE_CONTEXT_DB_KEY", "secret")

        assert cli.main(["--encrypted", "setup", "json"]) == 0

        captured = capsys.readouterr()
        document = json.loads(captured.out)
        assert document["mcpServers"]["people-context"]["args"][-1] == "--encrypted"
        assert "PEOPLE_CONTEXT_DB_KEY" in captured.err and "secret" not in captured.err

    def test_relative_db_is_anchored_to_the_setup_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        entry = build_entry("data/people.db", encrypted=False)

        assert entry.env == {"PEOPLE_CONTEXT_DB": str(tmp_path / "data" / "people.db")}


class TestPrintedCommandSafety:
    def test_a_path_the_shell_would_expand_is_quoted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The printed command is documented as runnable verbatim, so it must survive the shell."""
        monkeypatch.setattr(setup.shutil, "which", lambda _name: None)
        monkeypatch.chdir(tmp_path)

        lines = run_setup("claude-code", scope="user", db_path="$HOME/`id`.db", encrypted=False, dry_run=False)

        rendered = lines[1]
        assert "'" in rendered
        assert rendered.split()[-1].startswith("people-context")
        # Everything the shell would act on sits inside single quotes.
        assert "PEOPLE_CONTEXT_DB=$HOME" not in rendered
        assert shlex.split(rendered) == cli_command(
            "claude-code", "user", build_entry("$HOME/`id`.db", encrypted=False)
        )

    def test_windows_renders_the_argv_encoding_windows_parses(self) -> None:
        """POSIX single quotes are literal characters to cmd.exe, which would still split on spaces."""
        argv = ["claude", "mcp", "add", "--env", r"PEOPLE_CONTEXT_DB=C:\Users\Jane Doe\people.db"]

        rendered = setup.render_command(argv, platform="win32")

        assert '"PEOPLE_CONTEXT_DB=C:\\Users\\Jane Doe\\people.db"' in rendered
        assert "'" not in rendered

    def test_posix_and_windows_renderings_differ_for_the_same_argv(self) -> None:
        command = cli_command("claude-code", "user", build_entry("/tmp/a b.db", encrypted=False))

        posix = setup.render_command(command, platform="linux")
        windows = setup.render_command(command, platform="win32")

        assert shlex.split(posix) == command
        assert posix != windows
        assert "'PEOPLE_CONTEXT_DB=/tmp/a b.db'" in posix
