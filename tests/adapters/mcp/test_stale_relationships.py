"""In-memory MCP-session tests for the M13 staleness tool."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
from mcp.client import Client

from people_context.adapters.mcp.server import build_server


def _run(server: Any, flow: Any) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await flow(client)

    return anyio.run(main)


def _iso(days_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def test_stale_tool_reports_ordinary_recency_and_is_read_only(tmp_path: Path) -> None:
    server = build_server(tmp_path / "stale.db")

    async def flow(client: Client) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Any]:
        me = (await client.call_tool("remember_person", {"name": "Me", "is_self": True})).structured_content["person"]
        ids: dict[str, str] = {"me": me["id"]}
        for name in ("Alice", "Bob", "Carol"):
            created = await client.call_tool("remember_person", {"name": name})
            ids[name] = created.structured_content["person"]["id"]
        await client.call_tool(
            "set_relationship",
            {"subject_id": ids["me"], "object_id": ids["Alice"], "type": "colleague of"},
        )
        await client.call_tool(
            "set_relationship",
            {"subject_id": ids["me"], "object_id": ids["Bob"], "type": "friend of"},
        )
        await client.call_tool(
            "record_interaction",
            {"summary": "quarterly sync", "participant_ids": [ids["Alice"]], "occurred_at": _iso(200)},
        )
        await client.call_tool(
            "record_interaction",
            {"summary": "coffee", "participant_ids": [ids["Bob"]], "occurred_at": _iso(2)},
        )
        # An elevated interaction must not make Carol look recently contacted.
        await client.call_tool(
            "record_interaction",
            {
                "summary": "private matter",
                "participant_ids": [ids["Carol"]],
                "occurred_at": _iso(1),
                "sensitivity": "restricted",
            },
        )
        stale = await client.call_tool("get_stale_relationships", {})
        filtered = await client.call_tool("get_stale_relationships", {"category": "professional"})
        invalid = await client.call_tool("get_stale_relationships", {"threshold_days": 40000})
        tools = await client.list_tools()
        return (
            stale.structured_content,
            filtered.structured_content,
            invalid.structured_content,
            {tool.name: tool for tool in tools.tools},
        )

    stale, filtered, invalid, tools = _run(server, flow)

    names = [row["name"] for row in stale["people"]]
    assert names == ["Carol", "Alice"]
    assert stale["people"][0]["last_interaction_at"] is None
    assert stale["people"][0]["days_since"] is None
    assert stale["people"][0]["interaction_count"] == 0
    assert stale["people"][1]["categories"] == ["professional"]
    assert stale["people"][1]["days_since"] == 200
    assert stale["truncated"] is False
    assert [row["name"] for row in filtered["people"]] == ["Alice"]
    assert invalid["error"] == "invalid_parameter"
    assert tools["get_stale_relationships"].annotations.read_only_hint is True
    assert tools["get_stale_relationships"].input_schema["properties"]["threshold_days"]["default"] == 90
    assert tools["get_stale_relationships"].input_schema["properties"]["limit"]["default"] == 20
