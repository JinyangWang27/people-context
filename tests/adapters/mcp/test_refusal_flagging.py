"""Refusals reach MCP clients as tool errors, and `add_alias` publishes its `kind` enum.

Regression cover for #117: a refused write was returned as an ordinary result, so a client applying
the standard `isError` check read a dropped alias as a saved one, and `kind` was advertised as a bare
string, so nothing rejected the natural-looking `"OTHER"` before it reached the server.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
from mcp.client import Client
from mcp.types import TextContent, Tool

from people_context.adapters.mcp.server import build_server
from people_context.domain.person import AliasKind


def _run(server: Any, coro_factory: Any) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await coro_factory(client)

    return anyio.run(main)


def _tool(tools: list[Tool], name: str) -> Tool:
    return next(tool for tool in tools if tool.name == name)


def _resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Follow one local `$ref` into `$defs` so the test reads the published enum, not the pointer."""
    ref = node.get("$ref")
    if not isinstance(ref, str):
        return node
    return dict(schema["$defs"][ref.removeprefix("#/$defs/")])


def test_a_refused_write_is_flagged_as_an_error_and_keeps_its_payload(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "refused.db")

    async def flow(client: Client) -> tuple[Any, Any]:
        created = await client.call_tool("remember_person", {"name": "Jinyang Wang"})
        person_id = created.structured_content["person"]["id"]
        refused = await client.call_tool("add_alias", {"person_id": "missing", "value": "JY"})
        added = await client.call_tool("add_alias", {"person_id": person_id, "value": "JY"})
        return refused, added

    refused, added = _run(server, flow)

    assert refused.is_error is True
    # The structured refusal survives the flag: the model still reads why the write did not happen.
    assert refused.structured_content == {
        "error": "person_not_found",
        "message": refused.structured_content["message"],
        "person_id": "missing",
    }
    block = refused.content[0]
    assert isinstance(block, TextContent)
    assert json.loads(block.text) == refused.structured_content

    # A successful write is untouched.
    assert added.is_error is False
    assert [alias["value"] for alias in added.structured_content["aliases"]] == ["JY"]


def test_every_refusal_shape_across_the_tool_surface_is_flagged(tmp_path: Path) -> None:
    """Reads, writes, imports, and lifecycle tools all share the refusal contract, so all are flagged."""
    server = build_server(db_path=tmp_path / "surface.db")
    calls = {
        "add_alias": {"person_id": "missing", "value": "JY"},
        "record_fact": {"person_id": "missing", "predicate": "location", "value": "Dubai"},
        "record_trait": {"person_id": "missing", "category": "temperament", "value": "calm"},
        "set_affiliation": {"person_id": "missing", "org": "Acme", "role": "CTO"},
        "set_reminder": {"person_id": "missing", "text": "Follow up", "kind": "follow_up"},
        "correct_record": {"entity_type": "fact", "entity_id": "missing", "fields": {"value": "x"}},
        "merge_people": {"primary_id": "missing", "duplicate_id": "other"},
        "forget": {"target": "missing", "scope": "person"},
        "get_person_context": {"person": "nobody at all"},
        "get_person_timeline": {},
        "get_relationship_graph": {"person_id": "missing"},
        "find_connection": {"person_a": "missing", "person_b": "other"},
        "get_stale_relationships": {"threshold_days": -1},
        "upcoming_dates": {"window_days": -1},
        "import_content": {"source_type": "email", "content": "x"},
        "review_import": {"batch_id": "missing"},
    }

    async def flow(client: Client) -> dict[str, Any]:
        return {name: await client.call_tool(name, arguments) for name, arguments in calls.items()}

    results = _run(server, flow)

    for name, result in results.items():
        assert result.is_error is True, name
        assert result.structured_content["error"], name


def test_add_alias_publishes_the_kind_enum_and_refuses_an_unlisted_value(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "kind.db")

    async def flow(client: Client) -> tuple[Tool, Any, Any]:
        listed = (await client.list_tools()).tools
        created = await client.call_tool("remember_person", {"name": "Jinyang Wang"})
        person_id = created.structured_content["person"]["id"]
        wrong_case = await client.call_tool("add_alias", {"person_id": person_id, "value": "Jinyang", "kind": "OTHER"})
        accepted = await client.call_tool("add_alias", {"person_id": person_id, "value": "Jinyang", "kind": "nickname"})
        return _tool(listed, "add_alias"), wrong_case, accepted

    add_alias, wrong_case, accepted = _run(server, flow)

    schema = add_alias.input_schema
    kind = _resolve(schema, schema["properties"]["kind"]["anyOf"][0])
    assert kind["enum"] == [member.value for member in AliasKind]

    # An unlisted value is refused by the published schema rather than silently dropped.
    assert wrong_case.is_error is True
    block = wrong_case.content[0]
    assert isinstance(block, TextContent)
    assert "'nickname'" in block.text

    assert accepted.is_error is False
    assert [alias["kind"] for alias in accepted.structured_content["aliases"]] == ["nickname"]


def test_flagging_leaves_the_published_tool_definitions_unchanged(tmp_path: Path) -> None:
    """The wrapper is registered in place of each tool, so its schema and description must be the tool's."""
    server = build_server(db_path=tmp_path / "schemas.db")

    async def flow(client: Client) -> list[Tool]:
        return (await client.list_tools()).tools

    tools = _run(server, flow)

    record_fact = _tool(tools, "record_fact")
    assert record_fact.description is not None
    assert record_fact.description.startswith("Record a time-aware fact about an existing person.")
    assert record_fact.input_schema["required"] == ["person_id", "predicate", "value"]
    assert record_fact.output_schema is not None

    resolve_person = _tool(tools, "resolve_person")
    assert resolve_person.annotations is not None and resolve_person.annotations.read_only_hint is True
    assert resolve_person.input_schema["properties"]["limit"]["default"] == 5


def test_every_registered_tool_flags_its_refusals(tmp_path: Path, monkeypatch: Any) -> None:
    """Structural guard: a tool added without `@flag_refusals` would refuse silently again.

    Reaches into the SDK's tool registry because the wrapper is invisible on the wire; the behavioural
    sweep above covers the tools that can be refused with plain arguments, and this covers the rest.
    The elevated tools are switched on so the guard sees the whole surface, not just the ordinary one.
    """
    monkeypatch.setenv("PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE", "1")
    monkeypatch.setenv("PEOPLE_CONTEXT_MCP_ENABLE_EXPORT", "1")
    server = build_server(db_path=tmp_path / "registry.db")

    registered = server._tool_manager.list_tools()

    assert {tool.name for tool in registered} >= {
        "add_alias",
        "resolve_person",
        "export_data",
        "get_sensitive_person_context",
    }
    unflagged = [tool.name for tool in registered if getattr(tool.fn, "__wrapped__", None) is None]
    assert unflagged == []
