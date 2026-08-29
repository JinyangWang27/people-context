"""In-memory MCP-session tests for the M19 person timeline tool."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
from mcp.client import Client

from people_context.adapters.mcp.server import build_server
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.adapters.sqlite.trait_evidence import SqliteTraitEvidenceStore
from people_context.app.insights import MAX_TIMELINE_LIMIT
from people_context.app.records import (
    RecordObservation,
    RecordObservationInput,
    RecordTrait,
    RecordTraitInput,
)
from people_context.domain.person import Person
from people_context.domain.shared import Sensitivity
from people_context.domain.trait import TraitCategory
from people_context.ports.clock import SystemClock


def _run(server: Any, flow: Any) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await flow(client)

    return anyio.run(main)


def _iso(month: int, day: int) -> str:
    return datetime(2026, month, day, 12, 0, tzinfo=UTC).isoformat()


def test_the_timeline_tool_returns_ordinary_history_newest_first(tmp_path: Path) -> None:
    server = build_server(tmp_path / "timeline.db")

    async def flow(client: Client) -> dict[str, Any]:
        alice = (await client.call_tool("remember_person", {"name": "Alice"})).structured_content["person"]
        await client.call_tool(
            "record_interaction",
            {"summary": "quarterly sync", "participant_ids": [alice["id"]], "occurred_at": _iso(3, 1)},
        )
        await client.call_tool(
            "record_observation",
            {"person_id": alice["id"], "text": "prefers written updates", "observed_at": _iso(2, 1)},
        )
        await client.call_tool(
            "record_fact",
            {"person_id": alice["id"], "predicate": "city", "value": "Berlin", "valid_from": "2026-01-15"},
        )
        result = await client.call_tool("get_person_timeline", {"person_id": alice["id"]})
        return dict(result.structured_content)

    payload = _run(server, flow)

    assert payload["found"] is True
    assert payload["include_sensitive"] is False
    assert [entry["entry_type"] for entry in payload["entries"]] == ["interaction", "observation", "fact"]
    assert payload["entries"][0]["basis"] == "occurred_at"
    assert payload["entries"][2]["basis"] == "valid_from"
    assert payload["truncated"] is False


def test_the_ordinary_tool_never_discloses_elevated_records(tmp_path: Path) -> None:
    server = build_server(tmp_path / "elevated.db")

    async def flow(client: Client) -> dict[str, Any]:
        alice = (await client.call_tool("remember_person", {"name": "Alice"})).structured_content["person"]
        await client.call_tool(
            "record_observation",
            {
                "person_id": alice["id"],
                "text": "a restricted matter",
                "observed_at": _iso(5, 1),
                "sensitivity": "restricted",
            },
        )
        await client.call_tool(
            "record_interaction",
            {
                "summary": "a sensitive call",
                "participant_ids": [alice["id"]],
                "occurred_at": _iso(5, 2),
                "sensitivity": "sensitive",
            },
        )
        await client.call_tool(
            "record_observation",
            {"person_id": alice["id"], "text": "an ordinary note", "observed_at": _iso(4, 1)},
        )
        result = await client.call_tool("get_person_timeline", {"person_id": alice["id"]})
        return dict(result.structured_content)

    payload = _run(server, flow)

    assert [entry["summary"] for entry in payload["entries"]] == ["an ordinary note"]


def test_a_visible_trait_never_names_evidence_the_caller_may_not_read(tmp_path: Path) -> None:
    """Naming a restricted observation beside an ordinary trait would disclose that it exists.

    The links are seeded through the application writers because the released `record_trait` tool
    takes no `evidence_ids`; M18.3 links are created by the import/staging path. What is under test
    here is the ordinary tool's disclosure of links that already exist.
    """
    db_path = tmp_path / "evidence.db"
    conn = open_db(db_path)
    try:
        people = SqlitePeopleRepository(conn)
        records = SqliteRecordStore(conn)
        audit = SqliteAuditLog(conn)
        clock = SystemClock()
        alice = Person(canonical_name="Alice")
        people.save_person(alice)
        observations = RecordObservation(people, records, audit, clock)
        ordinary = observations.execute(
            RecordObservationInput(
                person_id=alice.id,
                text="answers in one line",
                observed_at=datetime(2026, 4, 1, tzinfo=UTC),
            )
        )
        restricted = observations.execute(
            RecordObservationInput(
                person_id=alice.id,
                text="a restricted matter",
                observed_at=datetime(2026, 4, 2, tzinfo=UTC),
                sensitivity=Sensitivity.RESTRICTED,
            )
        )
        RecordTrait(people, records, audit, clock, SqliteTraitEvidenceStore(conn)).execute(
            RecordTraitInput(
                person_id=alice.id,
                category=TraitCategory.COMMUNICATION_STYLE,
                value="terse",
                evidence_ids=[ordinary.id, restricted.id],
            )
        )
    finally:
        conn.close()

    server = build_server(db_path)

    async def flow(client: Client) -> dict[str, Any]:
        result = await client.call_tool("get_person_timeline", {"person_id": alice.id})
        return dict(result.structured_content)

    payload = _run(server, flow)
    trait = next(entry for entry in payload["entries"] if entry["entry_type"] == "trait")

    assert trait["evidence_ids"] == [ordinary.id]
    assert trait["evidence_truncated"] is False
    # The restricted observation itself is absent from the ordinary page, and naming it beside the
    # trait would have disclosed it anyway.
    assert all(entry["summary"] != "a restricted matter" for entry in payload["entries"])


def test_the_page_is_bounded_and_says_when_more_exists(tmp_path: Path) -> None:
    server = build_server(tmp_path / "bounded.db")

    async def flow(client: Client) -> dict[str, Any]:
        alice = (await client.call_tool("remember_person", {"name": "Alice"})).structured_content["person"]
        for day in range(1, 6):
            await client.call_tool(
                "record_observation",
                {"person_id": alice["id"], "text": f"note {day}", "observed_at": _iso(5, day)},
            )
        result = await client.call_tool("get_person_timeline", {"person_id": alice["id"], "limit": 2})
        return dict(result.structured_content)

    payload = _run(server, flow)

    assert len(payload["entries"]) == 2
    assert payload["truncated"] is True
    assert [entry["summary"] for entry in payload["entries"]] == ["note 5", "note 4"]


def test_an_out_of_range_limit_returns_a_structured_error(tmp_path: Path) -> None:
    server = build_server(tmp_path / "invalid.db")

    async def flow(client: Client) -> dict[str, Any]:
        alice = (await client.call_tool("remember_person", {"name": "Alice"})).structured_content["person"]
        result = await client.call_tool(
            "get_person_timeline",
            {"person_id": alice["id"], "limit": MAX_TIMELINE_LIMIT + 1},
        )
        return dict(result.structured_content)

    payload = _run(server, flow)

    assert payload["error"] == "invalid_parameter"
    assert "limit" in payload["message"]


def test_an_unknown_person_is_not_found_rather_than_an_error(tmp_path: Path) -> None:
    server = build_server(tmp_path / "unknown.db")

    async def flow(client: Client) -> dict[str, Any]:
        result = await client.call_tool("get_person_timeline", {"person_id": "nobody"})
        return dict(result.structured_content)

    payload = _run(server, flow)

    assert payload["found"] is False
    assert payload["entries"] == []


def test_the_timeline_tool_is_annotated_read_only(tmp_path: Path) -> None:
    server = build_server(tmp_path / "annotations.db")

    async def flow(client: Client) -> Any:
        return await client.list_tools()

    result = _run(server, flow)
    by_name = {tool.name: tool for tool in result.tools}

    assert by_name["get_person_timeline"].annotations.read_only_hint is True
