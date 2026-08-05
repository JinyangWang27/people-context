"""In-memory MCP-session tests for M7 graph tools."""

from __future__ import annotations

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


def test_mcp_graph_tools_normalize_edges_and_render_path(tmp_path: Path) -> None:
    server = build_server(tmp_path / "graph.db")

    async def flow(client: Client) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        ids: dict[str, str] = {}
        for name in ("A", "B", "C", "D"):
            result = await client.call_tool("remember_person", {"name": name})
            ids[name] = result.structured_content["person"]["id"]
        await client.call_tool(
            "set_relationship",
            {"subject_id": ids["B"], "object_id": ids["A"], "type": "manager of"},
        )
        await client.call_tool(
            "set_relationship",
            {"subject_id": ids["B"], "object_id": ids["C"], "type": "reports to"},
        )
        await client.call_tool(
            "set_relationship",
            {"subject_id": ids["D"], "object_id": ids["A"], "type": "friend of"},
        )
        graph = await client.call_tool("get_relationship_graph", {"person_id": ids["A"], "depth": 2})
        path = await client.call_tool(
            "find_connection",
            {"person_a": ids["C"], "person_b": ids["D"]},
        )
        tools = await client.list_tools()
        return graph.structured_content, path.structured_content, {tool.name: tool for tool in tools.tools}

    graph, path, tools = _run(server, flow)
    assert {edge["type"] for edge in graph["edges"]} == {"reports_to", "friend_of"}
    assert graph["truncated"] is False
    assert path["connected"] is True
    assert [hop["edge"]["display_type"] for hop in path["hops"]] == ["manages", "manages", "friend_of"]
    assert tools["get_relationship_graph"].annotations.read_only_hint is True
    assert tools["find_connection"].annotations.read_only_hint is True
