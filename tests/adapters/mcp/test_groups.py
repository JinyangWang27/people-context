"""In-memory MCP-session tests for group management, ordinary group reads (M28.1), and shared connections (M28.2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import anyio
from mcp.client import Client

from people_context.adapters.mcp.server import build_server

_READS = ("find_groups", "get_group", "list_group_memberships", "explain_shared_connections")
_WRITES = ("create_group", "add_group_membership", "close_group_membership")


def _run(server: Any, flow: Any) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await flow(client)

    return anyio.run(main)


async def _call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    return dict(result.structured_content)


async def _person(client: Client, name: str) -> str:
    return str((await _call(client, "remember_person", name=name))["person"]["id"])


def _history(database: Path) -> tuple[int, int]:
    conn = sqlite3.connect(database)
    try:
        return (
            conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0],
        )
    finally:
        conn.close()


def test_tools_are_annotated_and_reads_take_no_disclosure_parameter(tmp_path: Path) -> None:
    server = build_server(tmp_path / "groups.db")

    tools = {tool.name: tool for tool in _run(server, lambda client: client.list_tools()).tools}

    for name in _READS:
        assert tools[name].annotations.read_only_hint is True
        assert "include_sensitive" not in tools[name].input_schema["properties"]
    for name in _WRITES:
        assert tools[name].annotations.read_only_hint is False
        assert tools[name].annotations.destructive_hint is False
        assert not {"source", "session"} & set(tools[name].input_schema["properties"])


def test_manage_a_membership_through_its_history(tmp_path: Path) -> None:
    server = build_server(tmp_path / "groups.db")

    async def flow(client: Client) -> dict[str, Any]:
        alice = await _person(client, "Alice")
        group = await _call(client, "create_group", name="Class 1, Grade 6", kind="class")
        membership = await _call(
            client,
            "add_group_membership",
            person_id=alice,
            group_id=group["id"],
            role="student",
            valid_from="2015-09-01",
            temporal_basis="ongoing",
        )
        closed = await _call(client, "close_group_membership", membership_id=membership["id"], ended_on="2016-07-15")
        refused = await client.call_tool(
            "close_group_membership", {"membership_id": membership["id"], "ended_on": "2016-08-01"}
        )
        corrected = await _call(
            client,
            "correct_record",
            entity_type="group_membership",
            entity_id=membership["id"],
            fields={"valid_to": "2016-07-01"},
        )
        listed = await _call(client, "list_group_memberships", person="Alice")
        return {
            "closed": closed,
            "refused": (refused.is_error, dict(refused.structured_content)),
            "corrected": corrected,
            "listed": listed,
        }

    outcome = _run(server, flow)

    assert outcome["closed"]["period"] == {"valid_from": "2015-09-01", "valid_to": "2016-07-15"}
    assert outcome["closed"]["temporal_basis"] == "period"
    is_error, payload = outcome["refused"]
    assert is_error is True
    assert (payload["error"], payload["reason"]) == ("invalid_membership_closure", "already_ended")
    assert outcome["corrected"]["period"]["valid_to"] == "2016-07-01"
    [entry] = outcome["listed"]["memberships"]
    assert entry["group"]["group"]["name"] == "Class 1, Grade 6"
    assert outcome["listed"]["format"] == "people-context-person-memberships"


def test_protected_groups_and_memberships_stay_out_of_ordinary_reads(tmp_path: Path) -> None:
    database = tmp_path / "groups.db"
    server = build_server(database)

    async def flow(client: Client) -> tuple[dict[str, Any], ...]:
        alice, bob = await _person(client, "Alice"), await _person(client, "Bob")
        public = await _call(client, "create_group", name="Chess club", kind="club", sensitivity="public")
        hidden = await _call(client, "create_group", name="Support circle", kind="community", sensitivity="sensitive")
        await _call(client, "add_group_membership", person_id=alice, group_id=public["id"])
        await _call(client, "add_group_membership", person_id=bob, group_id=public["id"], sensitivity="restricted")
        await _call(client, "add_group_membership", person_id=alice, group_id=hidden["id"])
        before = _history(database)
        reads = (
            await _call(client, "get_group", group_id=public["id"], limit=1),
            await _call(client, "get_group", group_id=hidden["id"]),
            await _call(client, "find_groups", name="support"),
            await _call(client, "list_group_memberships", person_id=alice),
        )
        assert _history(database) == before
        return reads

    public_group, hidden_group, search, memberships = _run(server, flow)

    assert len(public_group["memberships"]) == 1
    assert public_group["truncated"] is False
    assert hidden_group["found"] is False and hidden_group["group"] is None
    assert search["groups"] == []
    assert [entry["group"]["group"]["name"] for entry in memberships["memberships"]] == ["Chess club"]


def test_refusals_are_structured(tmp_path: Path) -> None:
    server = build_server(tmp_path / "groups.db")

    async def flow(client: Client) -> list[dict[str, Any]]:
        alice = await _person(client, "Alice")
        return [
            await _call(client, "create_group", name="Team", kind="team", organization_id="01J0000000000000000000ORG1"),
            await _call(client, "add_group_membership", person_id=alice, group_id="01J0000000000000000000GRP1"),
            await _call(client, "find_groups", limit=0),
            await _call(client, "forget", target="group_membership:missing", scope="record"),
        ]

    missing_org, missing_group, bad_limit, missing_membership = _run(server, flow)

    assert missing_org["error"] == "organization_not_found"
    assert (missing_group["error"], missing_group["entity_type"]) == ("record_not_found", "group")
    assert bad_limit["error"] == "invalid_parameter"
    assert missing_membership["error"] == "record_not_found"


def test_shared_connections_resolve_names_use_ordinary_disclosure_and_write_nothing(tmp_path: Path) -> None:
    database = tmp_path / "groups.db"
    server = build_server(database)

    async def flow(client: Client) -> tuple[dict[str, Any], ...]:
        alice, bob = await _person(client, "Alice"), await _person(client, "Bob")
        klass = await _call(client, "create_group", name="Class 1, Grade 6", kind="class")
        hidden = await _call(client, "create_group", name="Support circle", kind="community", sensitivity="sensitive")
        for person, end in ((alice, "2016-06-30"), (bob, "2016-07-15")):
            await _call(
                client,
                "add_group_membership",
                person_id=person,
                group_id=klass["id"],
                role="student",
                valid_from="2015-09-01",
                valid_to=end,
            )
            await _call(client, "add_group_membership", person_id=person, group_id=hidden["id"])
        before = _history(database)
        results = (
            await _call(client, "explain_shared_connections", person_a="Alice", person_b_id=bob),
            await _call(client, "explain_shared_connections", person_a_id=alice, person_b_id=alice),
            await _call(client, "explain_shared_connections", person_a_id=alice, person_b="Nobody"),
            await _call(client, "explain_shared_connections", person_a="Nobody", person_b_id=bob),
        )
        assert _history(database) == before
        return results

    shared, same, missing, missing_first = _run(server, flow)

    assert shared["format"] == "people-context-shared-connections"
    [connection] = shared["connections"]
    assert connection["group"]["group"]["name"] == "Class 1, Grade 6"
    assert (connection["connection"], connection["label"]) == ("derived_relation", "classmates")
    assert connection["overlap"] == {"valid_from": "2015-09-01", "valid_to": "2016-06-30"}
    assert shared["truncated"] is False and shared["memberships_truncated"] is False
    assert same["error"] == "invalid_parameter"
    assert missing["error"] == missing_first["error"] == "person_not_found"
