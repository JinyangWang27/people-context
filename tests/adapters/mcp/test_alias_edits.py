"""`remove_alias` and `update_alias` end to end over SQLite (#221).

An email imported as an alias must be removable through the published tools: afterwards it no longer
resolves, sibling aliases still do, the person's other records survive, and the change reaches the
replay changelog as a full person snapshot.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import anyio
from mcp.client import Client

from people_context.adapters.mcp.server import build_server


def _run(server: Any, coro_factory: Any) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await coro_factory(client)

    return anyio.run(main)


def _exact_ids(resolved: Any) -> list[str]:
    return [
        candidate["person_id"]
        for candidate in resolved.structured_content["candidates"]
        if candidate["match_reason"].startswith("exact")
    ]


def test_remove_and_update_alias_keep_the_person_and_maintain_resolution(tmp_path: Path) -> None:
    db_path = tmp_path / "aliases.db"
    server = build_server(db_path=db_path)

    async def flow(client: Client) -> dict[str, Any]:
        created = await client.call_tool(
            "remember_person",
            {"name": "Priya Sharma", "aliases": [{"value": "priya@example.com", "kind": "handle"}, {"value": "Pri"}]},
        )
        person_id = created.structured_content["person"]["id"]
        await client.call_tool("record_fact", {"person_id": person_id, "predicate": "city", "value": "Pune"})
        return {
            "person_id": person_id,
            "removed": await client.call_tool("remove_alias", {"person_id": person_id, "value": "priya@example.com"}),
            "again": await client.call_tool("remove_alias", {"person_id": person_id, "value": "priya@example.com"}),
            "updated": await client.call_tool(
                "update_alias", {"person_id": person_id, "value": "pri", "kind": "nickname"}
            ),
            "by_email": await client.call_tool("resolve_person", {"query": "priya@example.com"}),
            "by_nickname": await client.call_tool("resolve_person", {"query": "Pri"}),
            "context": await client.call_tool("get_person_context", {"person_id": person_id}),
            "missing": await client.call_tool("update_alias", {"person_id": person_id, "value": "Nobody"}),
            "canonical": await client.call_tool("remove_alias", {"person_id": person_id, "value": "Priya Sharma"}),
            "email": await client.call_tool("add_alias", {"person_id": person_id, "value": "p.s@example.com"}),
        }

    out = _run(server, flow)
    person_id = out["person_id"]

    assert out["removed"].structured_content == {"person_id": person_id, "alias": "priya@example.com", "removed": True}
    assert out["again"].is_error is False
    assert out["again"].structured_content["removed"] is False
    assert out["updated"].structured_content["changed"] == ["kind"]
    assert person_id not in _exact_ids(out["by_email"])
    assert _exact_ids(out["by_nickname"]) == [person_id]
    assert [fact["value"] for fact in out["context"].structured_content["facts"]] == ["Pune"]
    assert out["missing"].is_error is True
    assert out["missing"].structured_content["error"] == "alias_not_found"
    assert out["canonical"].is_error is True
    assert out["canonical"].structured_content["reason"] == "canonical_name"
    assert out["email"].is_error is True
    assert out["email"].structured_content["details"][0]["type"] == "alias_email_not_handle"

    conn = sqlite3.connect(db_path)
    try:
        aliases = conn.execute("SELECT value, kind FROM aliases WHERE person_id = ?", (person_id,)).fetchall()
        replay = conn.execute(
            "SELECT payload_json FROM changelog WHERE entity_type = 'person' AND entity_id = ? "
            "ORDER BY hlc_physical_ms DESC, hlc_logical DESC LIMIT 1",
            (person_id,),
        ).fetchone()
    finally:
        conn.close()
    assert aliases == [("Pri", "nickname")]
    assert [alias["value"] for alias in json.loads(replay[0])["aliases"]] == ["Pri"]


def test_remove_alias_is_published_as_destructive_and_idempotent(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "annotations.db")

    async def flow(client: Client) -> Any:
        return (await client.list_tools()).tools

    tools = {tool.name: tool for tool in _run(server, flow)}

    assert tools["remove_alias"].annotations.destructive_hint is True
    assert tools["remove_alias"].annotations.idempotent_hint is True
    assert tools["update_alias"].annotations.destructive_hint is True
