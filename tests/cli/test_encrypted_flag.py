"""Global `--encrypted` flag behaviour for the human CLI.

These assertions are binding-independent: refusal happens before any SQLCipher
import, so they run on every platform.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from people_context.cli.main import main
from people_context.cli.parser import build_parser
from people_context.config import DB_KEY_ENV

KEY_SENTINEL = "battery staple"


def test_encrypted_defaults_to_off() -> None:
    assert build_parser().parse_args(["list"]).encrypted is False
    assert build_parser().parse_args(["--encrypted", "list"]).encrypted is True


@pytest.mark.parametrize("key", [None, "", "   "])
def test_encrypted_without_a_usable_key_refuses_before_touching_the_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    key: str | None,
) -> None:
    target = tmp_path / "people.db"
    if key is None:
        monkeypatch.delenv(DB_KEY_ENV, raising=False)
    else:
        monkeypatch.setenv(DB_KEY_ENV, key)

    exit_code = main(["--db", str(target), "--encrypted", "list"])

    assert exit_code == 2
    captured = capsys.readouterr()
    assert DB_KEY_ENV in captured.err
    assert captured.out == ""
    # No fallback: the refusal must not have created a plaintext database.
    assert not target.exists()


def test_refusal_message_never_echoes_key_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(DB_KEY_ENV, " ")

    main(["--db", str(tmp_path / "people.db"), "--encrypted", "list"])

    captured = capsys.readouterr()
    assert KEY_SENTINEL not in captured.err + captured.out


def test_plaintext_commands_are_unchanged_without_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(DB_KEY_ENV, raising=False)
    target = tmp_path / "people.db"

    assert main(["--db", str(target), "list"]) == 0
    assert target.exists()
    capsys.readouterr()


def test_demo_database_stays_plaintext_even_with_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The isolated demo store ignores real database settings, encryption included."""
    data_home = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv(DB_KEY_ENV, "a perfectly usable key")

    assert main(["--encrypted", "demo", "--reset"]) == 0
    capsys.readouterr()

    demo_path = data_home / "people-context" / "demo.db"
    plain = sqlite3.connect(demo_path)
    try:
        assert plain.execute("SELECT count(*) FROM persons").fetchone()[0] > 0
    finally:
        plain.close()
