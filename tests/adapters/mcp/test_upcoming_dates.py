"""In-memory MCP-session tests for the M13 upcoming-dates tool."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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


def _in_days(days: int) -> date:
    return datetime.now(UTC).date() + timedelta(days=days)


def _partial(day: date) -> str:
    return f"--{day.month:02d}-{day.day:02d}"


def test_upcoming_dates_reports_ordinary_birthdays_and_reminders(tmp_path: Path) -> None:
    server = build_server(tmp_path / "upcoming.db")
    soon = _in_days(5)
    outside = _in_days(200)
    due = _in_days(3)

    async def flow(client: Client) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Any]:
        ids: dict[str, str] = {}
        for name in ("Alice", "Bob", "Carol"):
            created = await client.call_tool("remember_person", {"name": name})
            ids[name] = created.structured_content["person"]["id"]
        await client.call_tool(
            "record_fact",
            {"person_id": ids["Alice"], "predicate": "birthday", "value": _partial(soon)},
        )
        await client.call_tool(
            "record_fact",
            {"person_id": ids["Bob"], "predicate": "birthday", "value": _partial(outside)},
        )
        # An elevated birthday must stay invisible to an ordinary report.
        await client.call_tool(
            "record_fact",
            {
                "person_id": ids["Carol"],
                "predicate": "birthday",
                "value": _partial(soon),
                "sensitivity": "restricted",
            },
        )
        await client.call_tool(
            "set_reminder",
            {
                "person_id": ids["Bob"],
                "text": "return the borrowed book",
                "kind": "follow_up",
                "due_at": datetime.combine(due, datetime.min.time(), tzinfo=UTC).isoformat(),
            },
        )
        upcoming = await client.call_tool("upcoming_dates", {})
        only_alice = await client.call_tool("upcoming_dates", {"person_id": ids["Alice"]})
        invalid = await client.call_tool("upcoming_dates", {"window_days": 400})
        tools = await client.list_tools()
        return (
            upcoming.structured_content,
            only_alice.structured_content,
            invalid.structured_content,
            {tool.name: tool for tool in tools.tools},
        )

    upcoming, only_alice, invalid, tools = _run(server, flow)

    assert [(entry["name"], entry["kind"], entry["date"]) for entry in upcoming["entries"]] == [
        ("Bob", "reminder", due.isoformat()),
        ("Alice", "birthday", soon.isoformat()),
    ]
    assert upcoming["entries"][0]["label"] == "return the borrowed book"
    assert upcoming["entries"][1]["label"] == "Birthday"
    assert upcoming["skipped_unparseable"] == 0
    assert [entry["name"] for entry in only_alice["entries"]] == ["Alice"]
    assert invalid["error"] == "invalid_parameter"
    assert tools["upcoming_dates"].annotations.read_only_hint is True
    assert tools["upcoming_dates"].input_schema["properties"]["window_days"]["default"] == 30


def test_upcoming_dates_counts_unparseable_ordinary_birthdays_only(tmp_path: Path) -> None:
    server = build_server(tmp_path / "unparseable.db")

    async def flow(client: Client) -> dict[str, Any]:
        ordinary = (await client.call_tool("remember_person", {"name": "Vague"})).structured_content["person"]["id"]
        elevated = (await client.call_tool("remember_person", {"name": "Hidden"})).structured_content["person"]["id"]
        await client.call_tool(
            "record_fact",
            {"person_id": ordinary, "predicate": "birthday", "value": "sometime in spring"},
        )
        await client.call_tool(
            "record_fact",
            {
                "person_id": elevated,
                "predicate": "birthday",
                "value": "sometime in spring",
                "sensitivity": "sensitive",
            },
        )
        return (await client.call_tool("upcoming_dates", {"window_days": 366})).structured_content

    result = _run(server, flow)

    assert result["entries"] == []
    assert result["skipped_unparseable"] == 1
