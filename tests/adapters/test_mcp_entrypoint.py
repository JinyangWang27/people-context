"""Server entrypoint transport-selection tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from people_context.adapters.mcp import server as server_module


class _ServerSpy:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(host=None, port=None, transport_security=None)
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
    monkeypatch.setattr(server_module, "build_server", lambda _db: spy)

    server_module.main(["--db", "people.db"])

    assert spy.run_calls == [{}]


def test_main_configures_loopback_streamable_http_security(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _ServerSpy()
    monkeypatch.setattr(server_module, "build_server", lambda _db: spy)

    server_module.main(["--http", "--port", "9123", "--host", "127.0.0.1"])

    assert spy.settings.host == "127.0.0.1"
    assert spy.settings.port == 9123
    security = spy.settings.transport_security
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["127.0.0.1:*", "localhost:*"]
    assert security.allowed_origins == ["http://127.0.0.1:*", "http://localhost:*"]
    assert spy.run_calls == [{"transport": "streamable-http"}]
