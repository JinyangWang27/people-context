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
    assert context["truncated"] is False
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


def test_elevated_records_leave_no_trace_on_the_ordinary_surface(tmp_path: Path) -> None:
    """The privacy contract: a person whose every assertion is elevated reads like a person with none."""
    server = build_server(db_path=tmp_path / "gated.db")

    async def flow(client: Client) -> tuple[dict[str, Any], dict[str, Any]]:
        gated = await client.call_tool(
            "remember", {"person": "Alice Ng", "note": "in treatment", "kind": "fact", "sensitivity": "restricted"}
        )
        await client.call_tool(
            "record_fact",
            {
                "person_id": gated.structured_content["person_id"],
                "predicate": "x",
                "value": "y",
                "sensitivity": "sensitive",
            },
        )
        await client.call_tool("remember_person", {"name": "Bob Reyes"})
        with_gated = await client.call_tool("get_person_context", {"person": "Alice Ng"})
        with_nothing = await client.call_tool("get_person_context", {"person": "Bob Reyes"})
        return with_gated.structured_content, with_nothing.structured_content

    with_gated, with_nothing = _run(server, flow)

    assert with_gated["facts"] == [] and with_gated["truncated"] is False
    # Byte-identical but for identity: no counter, flag, or field distinguishes the two.
    for payload in (with_gated, with_nothing):
        payload.pop("identity")
        payload.pop("person_id")
    assert with_gated == with_nothing


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


def test_graph_and_consolidation_accept_a_name_and_refuse_nothing(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "graph.db")

    async def flow(client: Client) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        await client.call_tool("remember", {"person": "Alice Ng", "note": "moved to Berlin"})
        graph = await client.call_tool("get_relationship_graph", {"person": "Alice Ng"})
        consolidation = await client.call_tool("get_consolidation_context", {"person": "alice ng"})
        errors = []
        for tool in ("get_relationship_graph", "get_consolidation_context"):
            errors.append((await client.call_tool(tool, {})).structured_content["error"])
        return graph.structured_content, consolidation.structured_content, errors

    graph, consolidation, errors = _run(server, flow)

    assert [node["name"] for node in graph["nodes"]] == ["Alice Ng"]
    assert consolidation["found"] is True
    assert errors == ["missing_person", "missing_person"]


def test_record_trait_forwards_evidence_ids(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "evidence.db")

    async def flow(client: Client) -> dict[str, Any]:
        remembered = await client.call_tool(
            "remember", {"person": "Alice Ng", "note": "met today, pushed back on the timeline"}
        )
        person_id = remembered.structured_content["person_id"]
        interaction_id = remembered.structured_content["recorded"][0]["id"]
        await client.call_tool(
            "record_trait",
            {
                "person_id": person_id,
                "category": "communication_style",
                "value": "pushes back on timelines",
                "evidence_note": "seen once",
                "evidence_ids": [interaction_id],
            },
        )
        context = await client.call_tool("get_person_context", {"person_id": person_id, "include_communication": True})
        return context.structured_content

    context = _run(server, flow)

    assert len(context["traits"]) == 1
    assert [link["evidence_type"] for link in context["trait_evidence"]] == ["interaction"]


def test_elevated_structural_capture_is_refused(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "elevated.db")

    async def flow(client: Client) -> tuple[dict[str, Any], dict[str, Any]]:
        refused = await client.call_tool(
            "remember", {"person": "Alice Ng", "org": "Mayo Clinic", "role": "patient", "sensitivity": "sensitive"}
        )
        lookup = await client.call_tool("resolve_person", {"query": "Alice Ng"})
        return refused.structured_content, lookup.structured_content

    refused, lookup = _run(server, flow)

    assert refused["status"] == "invalid_request" and refused["recorded"] == []
    assert lookup["candidates"] == []


def test_an_empty_name_filter_does_not_widen_a_scoped_read(tmp_path: Path) -> None:
    """Regression: `person: ""` fell through the filter guard and returned every person's rows."""
    server = build_server(db_path=tmp_path / "empty-filter.db")

    async def flow(client: Client) -> dict[str, Any]:
        for name in ("Alice Ng", "Bob Reyes"):
            remembered = await client.call_tool("remember", {"person": name, "note": "moved to Berlin"})
            await client.call_tool(
                "set_reminder",
                {
                    "person_id": remembered.structured_content["person_id"],
                    "text": f"follow up with {name}",
                    "kind": "follow_up",
                    "due_at": "2027-01-15T09:00:00Z",
                },
            )
        return {
            "unfiltered": (await client.call_tool("list_reminders", {})).structured_content,
            "empty_name": (await client.call_tool("list_reminders", {"person": ""})).structured_content,
            "empty_name_upcoming": (await client.call_tool("upcoming_dates", {"person": ""})).structured_content,
        }

    result = _run(server, flow)

    # Omitting both arguments is still a legitimate unscoped read.
    assert len(result["unfiltered"]["reminders"]) == 2
    # Supplying an empty one is a caller mistake, answered as such rather than with everyone's rows.
    assert result["empty_name"]["error"] == "missing_person"
    assert "reminders" not in result["empty_name"]
    assert result["empty_name_upcoming"]["error"] == "missing_person"


def test_a_typo_is_confirmed_rather_than_read_as_another_person(tmp_path: Path) -> None:
    """A fuzzy-only candidate means the store holds no such name; reading it would misattribute records."""
    server = build_server(db_path=tmp_path / "typo.db")

    async def flow(client: Client) -> dict[str, Any]:
        await client.call_tool("remember", {"person": "Daniel Okafor", "note": "moved to Berlin"})
        return {
            tool: (await client.call_tool(tool, {"person": "Danial Okafor"})).structured_content
            for tool in (
                "get_person_context",
                "get_communication_guidance",
                "get_person_timeline",
                "get_relationship_graph",
                "get_consolidation_context",
                "list_reminders",
            )
        }

    results = _run(server, flow)

    for tool, payload in results.items():
        assert payload["error"] == "unconfirmed_person", tool
        assert [candidate["canonical_name"] for candidate in payload["candidates"]] == ["Daniel Okafor"], tool
        assert "facts" not in payload and "reminders" not in payload, tool


def test_a_partial_name_still_reads_without_a_round_trip(tmp_path: Path) -> None:
    """Regression guard on the fix above: `Who is Amina?` is the documented flow and must keep working."""
    server = build_server(db_path=tmp_path / "partial.db")

    async def flow(client: Client) -> dict[str, Any]:
        await client.call_tool(
            "remember", {"person": "Amina Hassan", "note": "moved to Berlin", "org": "Open City Lab"}
        )
        return (await client.call_tool("get_person_context", {"person": "Amina"})).structured_content

    context = _run(server, flow)

    assert context["found"] is True
    assert context["identity"]["canonical_name"] == "Amina Hassan"
    assert [item["organization_name"] for item in context["affiliations"]] == ["Open City Lab"]
