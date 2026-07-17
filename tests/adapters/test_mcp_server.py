"""Tests for the FastMCP stdio adapter via the SDK's in-memory client/server."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from people_context.adapters.mcp.server import build_server
from people_context.adapters.sqlite import SqliteAuditLog, open_db

EXPECTED_TOOLS = {
    # real
    "resolve_person",
    "search_people",
    "remember_person",
    # read-only stubs
    "get_person_context",
    "get_communication_guidance",
    "list_reminders",
    # write stubs
    "add_alias",
    "set_relationship",
    "set_affiliation",
    "record_fact",
    "record_observation",
    "record_trait",
    "record_interaction",
    "correct_record",
    "set_reminder",
    "complete_reminder",
    "set_communication_philosophy",
    "import_content",
    "review_import",
    "commit_import",
    # destructive stubs
    "merge_people",
    "forget",
    # export
    "export_data",
}


def _run(server: Any, coro_factory: Any) -> Any:
    async def main() -> Any:
        async with create_connected_server_and_client_session(server) as client:
            await client.initialize()
            return await coro_factory(client)

    return anyio.run(main)


def test_tools_list_surface_and_annotations(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "t.db")

    async def collect(client: ClientSession) -> Any:
        return await client.list_tools()

    result = _run(server, collect)
    by_name = {tool.name: tool for tool in result.tools}

    assert set(by_name) >= EXPECTED_TOOLS
    assert by_name["resolve_person"].annotations.readOnlyHint is True
    assert by_name["merge_people"].annotations.destructiveHint is True
    assert by_name["remember_person"].annotations.readOnlyHint is False


def test_remember_then_resolve_and_audit_row(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    server = build_server(db_path=db_path)

    async def flow(client: ClientSession) -> tuple[dict[str, Any], dict[str, Any]]:
        remembered = await client.call_tool(
            "remember_person",
            {"name": "Jinyang Wang", "aliases": [{"value": "JW", "kind": "nickname"}], "summary": "me"},
        )
        resolved = await client.call_tool("resolve_person", {"query": "Jinyang"})
        return remembered.structuredContent, resolved.structuredContent

    remember_payload, resolve_payload = _run(server, flow)

    assert remember_payload["created"] is True
    person_id = remember_payload["person"]["id"]

    candidates = resolve_payload["candidates"]
    assert any(candidate["person_id"] == person_id for candidate in candidates)

    # An audit row must have been persisted to the DB file.
    conn = open_db(db_path)
    try:
        entries = SqliteAuditLog(conn).list_entries()
    finally:
        conn.close()
    assert any(entry.entity_id == person_id and entry.op == "create" for entry in entries)


def test_stub_tool_returns_not_implemented(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "t.db")

    async def call(client: ClientSession) -> dict[str, Any]:
        result = await client.call_tool("record_fact", {"person_id": "x", "predicate": "p", "value": "v"})
        return result.structuredContent

    payload = _run(server, call)
    assert payload == {"status": "not_implemented", "planned_milestone": "M2"}


def test_build_server_logs_to_stderr_not_stdout(tmp_path: Path, capsys: Any) -> None:
    db_path = tmp_path / "t.db"
    build_server(db_path=db_path)
    captured = capsys.readouterr()

    # Nothing must go to stdout — stdio transport carries the protocol there.
    assert captured.out == ""
    # The resolved DB path is logged to stderr at startup.
    assert str(db_path) in captured.err
