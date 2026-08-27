"""Optional import-receipt metadata on the `stage_candidates` MCP tool."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import anyio
from mcp.client import Client

from people_context.adapters.mcp.server import build_server
from people_context.adapters.sqlite import open_db

_DIGEST = "a" * 64
_FINGERPRINT = "b" * 64

_CANDIDATES = [
    {"type": "person", "ref": "a", "name": "Alice Ahmed", "aliases": [{"value": "alice@example.com", "kind": "handle"}]}
]


def _run(server: Any, coro_factory: Any) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await coro_factory(client)

    return anyio.run(main)


def _call(server: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    async def call(client: Client) -> dict[str, Any]:
        result = await client.call_tool("stage_candidates", arguments)
        assert result.structured_content is not None
        return result.structured_content

    payload = _run(server, call)
    assert isinstance(payload, dict)
    return payload


def _sessions(db_path: Path) -> list[sqlite3.Row]:
    conn = open_db(db_path)
    try:
        return conn.execute("SELECT * FROM import_source_sessions").fetchall()
    finally:
        conn.close()


def test_the_released_call_shape_records_no_receipt(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    server = build_server(db_path=db_path)

    payload = _call(server, {"source": "weekly-sync", "candidates": _CANDIDATES})

    assert payload["candidate_count"] == 1
    assert payload["source_session_id"] is None
    assert payload["duplicate"] is False
    assert _sessions(db_path) == []


def test_a_digest_backed_agent_session_participates_in_a_claim(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    server = build_server(db_path=db_path)
    arguments = {
        "source": "weekly-sync",
        "candidates": _CANDIDATES,
        "source_kind": "meeting_transcript",
        "content_digest": _DIGEST,
        "extraction_fingerprint": _FINGERPRINT,
        "label": "Weekly sync",
        "external_source_id": "NOTES-1",
    }

    first = _call(server, arguments)
    second = _call(server, arguments)

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["batch_id"] == first["batch_id"]
    sessions = _sessions(db_path)
    assert len(sessions) == 1
    assert sessions[0]["label"] == "Weekly sync"
    assert sessions[0]["extraction_fingerprint"] == _FINGERPRINT


def test_a_digestless_agent_session_promises_no_idempotency(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    server = build_server(db_path=db_path)
    arguments = {
        "source": "weekly-sync",
        "candidates": _CANDIDATES,
        "source_kind": "meeting_transcript",
    }

    first = _call(server, arguments)
    second = _call(server, arguments)

    assert first["duplicate"] is False
    assert second["duplicate"] is False
    assert first["batch_id"] != second["batch_id"]
    assert [row["claim_key"] for row in _sessions(db_path)] == [None, None]


def test_a_source_kind_that_reads_as_a_description_is_refused(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    server = build_server(db_path=db_path)

    payload = _call(
        server,
        {
            "source": "weekly-sync",
            "candidates": _CANDIDATES,
            "source_kind": "interview with alice",
        },
    )

    assert payload["error"] == "invalid_source_metadata"
    assert payload["field"] == "source_kind"
    assert _sessions(db_path) == []
    conn = open_db(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0
    finally:
        conn.close()


def test_a_rejected_label_is_not_echoed_back(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    server = build_server(db_path=db_path)
    sentinel = "AGENT-LABEL-MUST-NOT-LEAK-3c9d"

    payload = _call(
        server,
        {
            "source": "weekly-sync",
            "candidates": _CANDIDATES,
            "source_kind": "meeting_transcript",
            "label": sentinel + "x" * 300,
        },
    )

    assert payload["error"] == "invalid_source_metadata"
    assert sentinel not in str(payload)
