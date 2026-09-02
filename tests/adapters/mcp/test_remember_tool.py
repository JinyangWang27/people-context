"""The `remember` tool and name-addressed reads over the in-memory MCP client."""

from __future__ import annotations

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


def test_remember_then_read_by_name_is_two_calls(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "remember.db")

    async def flow(client: Client) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        remembered = await client.call_tool(
            "remember",
            {"person": "Alice Ng", "note": "prefers short emails", "org": "Acme", "role": "CTO"},
        )
        context = await client.call_tool("get_person_context", {"person": "alice ng", "include_communication": True})
        guidance = await client.call_tool("get_communication_guidance", {"person": "Alice Ng"})
        return remembered.structured_content, context.structured_content, guidance.structured_content

    remembered, context, guidance = _run(server, flow)

    assert remembered["status"] == "recorded" and remembered["created"] is True
    assert [record["kind"] for record in remembered["recorded"]] == ["affiliation", "trait"]
    assert context["found"] is True
    assert context["identity"]["canonical_name"] == "Alice Ng"
    assert [item["organization_name"] for item in context["affiliations"]] == ["Acme"]
    assert [trait["category"] for trait in context["traits"]] == ["communication_style"]
    assert context["withheld"] == {"sensitive": 0, "restricted": 0, "truncated": False}
    assert guidance["person_id"] == remembered["person_id"]


def test_ambiguous_name_on_a_read_returns_candidates_not_context(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "ambiguous.db")

    async def flow(client: Client) -> tuple[dict[str, Any], dict[str, Any]]:
        await client.call_tool("remember_person", {"name": "Priya Raman"})
        await client.call_tool("remember_person", {"name": "Priya Shah"})
        context = await client.call_tool("get_person_context", {"person": "Priya"})
        write = await client.call_tool("remember", {"person": "Priya", "note": "moved to Berlin"})
        return context.structured_content, write.structured_content

    context, write = _run(server, flow)

    assert context["error"] == "ambiguous_person"
    assert len(context["candidates"]) == 2
    assert write["status"] == "ambiguous" and write["recorded"] == []


def test_read_without_id_or_name_is_a_structured_error(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "missing.db")

    async def flow(client: Client) -> dict[str, Any]:
        result = await client.call_tool("get_person_timeline", {})
        return result.structured_content

    assert _run(server, flow)["error"] == "missing_person"


def test_withheld_counts_surface_gated_records(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "withheld.db")

    async def flow(client: Client) -> dict[str, Any]:
        remembered = await client.call_tool(
            "remember", {"person": "Alice Ng", "note": "in treatment", "kind": "fact", "sensitivity": "restricted"}
        )
        await client.call_tool(
            "record_fact",
            {
                "person_id": remembered.structured_content["person_id"],
                "predicate": "x",
                "value": "y",
                "sensitivity": "sensitive",
            },
        )
        context = await client.call_tool(
            "get_person_context", {"person_id": remembered.structured_content["person_id"]}
        )
        return context.structured_content

    context = _run(server, flow)

    assert context["facts"] == []
    assert context["withheld"] == {"sensitive": 1, "restricted": 1, "truncated": False}


def test_schemas_spell_out_the_vocabulary(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "schema.db")

    async def flow(client: Client) -> dict[str, Any]:
        return {tool.name: tool for tool in (await client.list_tools()).tools}

    tools = _run(server, flow)

    def enum_of(tool: str, param: str) -> list[str]:
        schema = tools[tool].input_schema
        prop = schema["properties"][param]
        if "$ref" in prop:
            ref = prop["$ref"].rsplit("/", 1)[1]
            return list(schema["$defs"][ref]["enum"])
        for option in prop.get("anyOf", []):
            if "$ref" in option:
                ref = option["$ref"].rsplit("/", 1)[1]
                return list(schema["$defs"][ref]["enum"])
            if "enum" in option:
                return list(option["enum"])
        return list(prop.get("enum", []))

    assert enum_of("record_fact", "sensitivity") == ["public", "personal", "sensitive", "restricted"]
    assert "communication_style" in enum_of("record_trait", "category")
    assert enum_of("set_reminder", "kind") == ["follow_up", "occasion", "communication_note"]
    assert "auto" in enum_of("remember", "kind")
    hints = tools["resolve_person"].input_schema
    assert "org" in str(hints)
    assert tools["review_import"].annotations.read_only_hint is True
    assert tools["remember"].annotations.read_only_hint is False
