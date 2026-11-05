"""Tests for deprecation notices on the capture tools scheduled for removal in 2.0 (#234)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client import Client

from people_context.adapters.mcp.server import build_server
from people_context.adapters.mcp.tools.deprecation import DEPRECATION_ISSUE_URL

DEPRECATED_TOOLS = {"record_fact", "record_trait", "record_interaction", "set_affiliation", "set_relationship"}

#: The published input parameters of each deprecated tool. A deprecation must not change them.
_EXPECTED_PARAMETERS = {
    "record_fact": {"person_id", "predicate", "value", "valid_from", "valid_to", "confidence", "sensitivity"},
    "record_trait": {
        "person_id",
        "category",
        "value",
        "evidence_note",
        "confidence",
        "sensitivity",
        "evidence_ids",
    },
    "record_interaction": {"summary", "participant_ids", "occurred_at", "channel", "sensitivity"},
    "set_affiliation": {"person_id", "org", "role", "valid_from", "valid_to", "confidence"},
    "set_relationship": {"subject_id", "object_id", "type", "label", "valid_from", "valid_to", "confidence"},
}


def _run(server: Any, flow: Any) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await flow(client)

    return anyio.run(main)


def test_only_the_scheduled_tools_carry_a_deprecation_notice(tmp_path: Path) -> None:
    async def flow(client: Client) -> Any:
        return (await client.list_tools()).tools

    tools = _run(build_server(db_path=tmp_path / "t.db"), flow)
    flagged = {tool.name for tool in tools if (tool.description or "").startswith("Deprecated: removed in 2.0")}

    assert flagged == DEPRECATED_TOOLS
    for tool in tools:
        if tool.name in DEPRECATED_TOOLS:
            assert DEPRECATION_ISSUE_URL in (tool.description or "")
            assert "`remember`" in (tool.description or "")


def test_deprecation_leaves_parameters_and_annotations_unchanged(tmp_path: Path) -> None:
    async def flow(client: Client) -> Any:
        return {tool.name: tool for tool in (await client.list_tools()).tools}

    by_name = _run(build_server(db_path=tmp_path / "t.db"), flow)

    for name, parameters in _EXPECTED_PARAMETERS.items():
        assert set(by_name[name].input_schema["properties"]) == parameters
        assert by_name[name].annotations.read_only_hint is False
        assert by_name[name].annotations.destructive_hint is False


def test_a_deprecated_tool_still_writes_and_warns_the_operator_once(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    async def flow(client: Client) -> list[Any]:
        person = (await client.call_tool("remember_person", {"name": "Alice"})).structured_content["person"]
        results = []
        for value in ("Lisbon", "Porto"):
            results.append(
                await client.call_tool(
                    "record_fact", {"person_id": person["id"], "predicate": "lives_in", "value": value}
                )
            )
        return results

    results = _run(build_server(db_path=tmp_path / "t.db"), flow)

    assert all(result.is_error is False for result in results)
    # The server logs to stderr, which the stdio transport keeps free of protocol traffic.
    captured = capfd.readouterr()
    warnings = [line for line in captured.err.splitlines() if "is deprecated" in line]
    assert warnings == [
        "WARNING people_context: MCP tool record_fact is deprecated and will be removed in 2.0; "
        f"see {DEPRECATION_ISSUE_URL}"
    ]
    assert "is deprecated" not in captured.out
    # The operator log names the tool only; the arguments are personal data.
    assert not any("Lisbon" in message or "Alice" in message for message in warnings)
