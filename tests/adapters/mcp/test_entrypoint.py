"""Server entrypoint transport-selection tests."""

from __future__ import annotations

from typing import Any

import pytest

from people_context.adapters.mcp import server as server_module
from people_context.config import DB_KEY_ENV


class _ServerSpy:
    def __init__(self) -> None:
        self.run_calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> None:
        self.run_calls.append(kwargs)


def test_parser_defaults_to_stdio_and_rejects_non_loopback_host() -> None:
    args = server_module._build_parser().parse_args([])

    assert args.http is False
    assert args.host == "127.0.0.1"
    assert args.port == 8765

    with pytest.raises(SystemExit) as exc_info:
        server_module._build_parser().parse_args(["--http", "--host", "0.0.0.0"])
    assert exc_info.value.code == 2


def test_main_keeps_default_stdio_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _ServerSpy()
    monkeypatch.setattr(server_module, "build_server", lambda _db, **_options: spy)

    server_module.main(["--db", "people.db"])

    assert spy.run_calls == [{}]


def test_main_configures_loopback_streamable_http_security(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _ServerSpy()
    monkeypatch.setattr(server_module, "build_server", lambda _db, **_options: spy)

    server_module.main(["--http", "--port", "9123", "--host", "127.0.0.1"])

    assert len(spy.run_calls) == 1
    run_args = spy.run_calls[0]
    assert run_args["host"] == "127.0.0.1"
    assert run_args["port"] == 9123
    security = run_args["transport_security"]
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["127.0.0.1:*", "localhost:*"]
    assert security.allowed_origins == ["http://127.0.0.1:*", "http://localhost:*"]
    assert run_args["transport"] == "streamable-http"


def test_parser_defaults_to_plaintext_and_accepts_encrypted() -> None:
    assert server_module._build_parser().parse_args([]).encrypted is False
    assert server_module._build_parser().parse_args(["--encrypted"]).encrypted is True


def test_main_passes_the_encrypted_selection_to_the_server_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _ServerSpy()
    seen: list[dict[str, Any]] = []

    def _build(db_path: str | None, **options: Any) -> _ServerSpy:
        seen.append({"db": db_path, **options})
        return spy

    monkeypatch.setattr(server_module, "build_server", _build)

    server_module.main(["--encrypted", "--db", "people.db"])

    assert seen == [{"db": "people.db", "encrypted": True}]


def test_main_refuses_encrypted_start_without_a_key(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(DB_KEY_ENV, raising=False)

    with pytest.raises(SystemExit) as exc_info:
        server_module.main(["--encrypted", "--db", "people.db"])

    assert exc_info.value.code == 2
    captured = capfd.readouterr()
    assert DB_KEY_ENV in captured.err
    # The stdio transport owns STDOUT; a refusal must never write to it.
    assert captured.out == ""
