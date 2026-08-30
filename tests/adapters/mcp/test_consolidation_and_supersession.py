"""In-memory MCP-session tests for the M19.2 consolidation read and supersession write."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import anyio
from mcp.client import Client

from people_context.adapters.mcp.server import build_server
from people_context.app.insights import MAX_CONSOLIDATION_LIMIT
from people_context.app.records import (
    REASON_AFTER_VALID_TO,
    REASON_NOT_AFTER_VALID_FROM,
)


def _run(server: Any, flow: Any) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await flow(client)

    return anyio.run(main)


async def _person(client: Client, name: str = "Alice") -> str:
    created = await client.call_tool("remember_person", {"name": name})
    return str(created.structured_content["person"]["id"])


async def _fact(client: Client, person_id: str, **fields: Any) -> dict[str, Any]:
    result = await client.call_tool("record_fact", {"person_id": person_id, **fields})
    return dict(result.structured_content)


class TestConsolidationTool:
    """The read is registered, bounded, ordinary-disclosure, and explains what it found."""

    def test_it_reports_a_contradiction_between_two_overlapping_facts(self, tmp_path: Path) -> None:
        server = build_server(tmp_path / "consolidation.db")

        async def flow(client: Client) -> dict[str, Any]:
            alice = await _person(client)
            await _fact(client, alice, predicate="employer", value="Acme", valid_from="2024-01-01")
            await _fact(client, alice, predicate="employer", value="Globex", valid_from="2026-07-01")
            result = await client.call_tool("get_consolidation_context", {"person_id": alice})
            return dict(result.structured_content)

        payload = _run(server, flow)

        assert payload["found"] is True
        assert payload["include_sensitive"] is False
        assert [signal["kind"] for signal in payload["signals"]] == ["contradictory_fact"]
        assert payload["signals"][0]["entity_type"] == "fact"
        assert payload["signals"][0]["key"] == "employer"
        assert len(payload["signals"][0]["entity_ids"]) == 2
        assert [fact["predicate"] for fact in payload["facts"]] == ["employer", "employer"]

    def test_a_well_formed_succession_is_reported_as_history(self, tmp_path: Path) -> None:
        server = build_server(tmp_path / "succession.db")

        async def flow(client: Client) -> dict[str, Any]:
            alice = await _person(client)
            await _fact(
                client,
                alice,
                predicate="employer",
                value="Acme",
                valid_from="2024-01-01",
                valid_to="2026-06-30",
            )
            await _fact(client, alice, predicate="employer", value="Globex", valid_from="2026-07-01")
            result = await client.call_tool("get_consolidation_context", {"person_id": alice})
            return dict(result.structured_content)

        payload = _run(server, flow)

        assert [signal["kind"] for signal in payload["signals"]] == ["succeeding_fact"]

    def test_traits_and_observations_are_reported_with_their_evidence_links(self, tmp_path: Path) -> None:
        server = build_server(tmp_path / "evidence.db")

        async def flow(client: Client) -> dict[str, Any]:
            alice = await _person(client)
            observation = (
                await client.call_tool(
                    "record_observation",
                    {"person_id": alice, "text": "asked for numbers before agreeing"},
                )
            ).structured_content
            await client.call_tool(
                "record_trait",
                {
                    "person_id": alice,
                    "category": "communication_style",
                    "value": "evidence-led",
                    "evidence_note": "from the March review",
                },
            )
            result = await client.call_tool("get_consolidation_context", {"person_id": alice})
            return {"observation": observation, "payload": dict(result.structured_content)}

        outcome = _run(server, flow)
        payload = outcome["payload"]

        assert [trait["category"] for trait in payload["traits"]] == ["communication_style"]
        assert payload["traits"][0]["evidence_note"] == "from the March review"
        assert [entry["observation_id"] for entry in payload["observations"]] == [outcome["observation"]["id"]]
        # No trait cites this observation, so the reverse link is empty rather than guessed at.
        assert payload["observations"][0]["cited_by_trait_ids"] == []
        # Nothing here came from an import, and every type still reports who asserted it.
        for record in (payload["traits"][0], payload["observations"][0]):
            assert record["source_session_id"] is None
            assert record["provenance"]["source"] == "agent"

    def test_elevated_records_are_never_returned_by_the_ordinary_tool(self, tmp_path: Path) -> None:
        server = build_server(tmp_path / "disclosure.db")

        async def flow(client: Client) -> dict[str, Any]:
            alice = await _person(client)
            await _fact(client, alice, predicate="employer", value="Acme")
            await _fact(
                client,
                alice,
                predicate="employer",
                value="Umbrella",
                sensitivity="restricted",
            )
            result = await client.call_tool("get_consolidation_context", {"person_id": alice})
            return dict(result.structured_content)

        payload = _run(server, flow)

        assert [fact["value"] for fact in payload["facts"]] == ["Acme"]
        # The withheld fact must not contradict the visible one from behind the disclosure gate.
        assert payload["signals"] == []

    def test_an_unknown_person_is_reported_rather_than_raised(self, tmp_path: Path) -> None:
        server = build_server(tmp_path / "missing.db")

        async def flow(client: Client) -> dict[str, Any]:
            result = await client.call_tool("get_consolidation_context", {"person_id": "nobody"})
            return dict(result.structured_content)

        payload = _run(server, flow)

        assert payload["found"] is False
        assert payload["facts"] == payload["traits"] == payload["observations"] == payload["signals"] == []

    def test_an_out_of_range_limit_returns_a_structured_parameter_error(self, tmp_path: Path) -> None:
        server = build_server(tmp_path / "limit.db")

        async def flow(client: Client) -> dict[str, Any]:
            alice = await _person(client)
            result = await client.call_tool(
                "get_consolidation_context",
                {"person_id": alice, "limit": MAX_CONSOLIDATION_LIMIT + 1},
            )
            return dict(result.structured_content)

        payload = _run(server, flow)

        assert payload["error"] == "invalid_parameter"
        assert "limit" in payload["message"]


class TestSupersedeFactTool:
    """The write is registered, atomic, and refuses a date that describes no transition."""

    def test_it_closes_the_old_fact_and_opens_the_replacement(self, tmp_path: Path) -> None:
        server = build_server(tmp_path / "supersede.db")

        async def flow(client: Client) -> dict[str, Any]:
            alice = await _person(client)
            original = await _fact(
                client,
                alice,
                predicate="employer",
                value="Acme",
                valid_from="2024-01-01",
                valid_to="2026-12-31",
            )
            result = await client.call_tool(
                "supersede_fact",
                {"fact_id": original["id"], "new_value": "Globex", "effective_from": "2026-07-01"},
            )
            return dict(result.structured_content)

        payload = _run(server, flow)

        assert payload["superseded"]["value"] == "Acme"
        assert payload["superseded"]["period"] == {"valid_from": "2024-01-01", "valid_to": "2026-06-30"}
        assert payload["replacement"]["value"] == "Globex"
        # The replacement inherits the original endpoint rather than becoming open-ended.
        assert payload["replacement"]["period"] == {"valid_from": "2026-07-01", "valid_to": "2026-12-31"}
        assert payload["transaction_id"]

    def test_the_replacement_inherits_confidence_and_sensitivity_unless_supplied(self, tmp_path: Path) -> None:
        server = build_server(tmp_path / "inherit.db")

        async def flow(client: Client) -> dict[str, Any]:
            alice = await _person(client)
            first = await _fact(
                client, alice, predicate="employer", value="Acme", confidence=0.4, sensitivity="sensitive"
            )
            second = await _fact(client, alice, predicate="city", value="Berlin", confidence=0.4)
            inherited = await client.call_tool(
                "supersede_fact",
                {"fact_id": first["id"], "new_value": "Globex", "effective_from": "2026-07-01"},
            )
            explicit = await client.call_tool(
                "supersede_fact",
                {
                    "fact_id": second["id"],
                    "new_value": "Lisbon",
                    "effective_from": "2026-07-01",
                    "confidence": 0.9,
                    "sensitivity": "public",
                },
            )
            return {
                "inherited": dict(inherited.structured_content),
                "explicit": dict(explicit.structured_content),
            }

        payload = _run(server, flow)

        assert payload["inherited"]["replacement"]["confidence"] == 0.4
        assert payload["inherited"]["replacement"]["sensitivity"] == "sensitive"
        assert payload["explicit"]["replacement"]["confidence"] == 0.9
        assert payload["explicit"]["replacement"]["sensitivity"] == "public"

    def test_a_date_inside_no_transition_returns_a_structured_reason(self, tmp_path: Path) -> None:
        server = build_server(tmp_path / "refusal.db")

        async def flow(client: Client) -> dict[str, Any]:
            alice = await _person(client)
            open_ended = await _fact(client, alice, predicate="employer", value="Acme", valid_from="2026-01-01")
            ended = await _fact(
                client,
                alice,
                predicate="city",
                value="Lisbon",
                valid_from="2020-01-01",
                valid_to="2020-12-31",
            )
            too_early = await client.call_tool(
                "supersede_fact",
                {"fact_id": open_ended["id"], "new_value": "Globex", "effective_from": "2025-06-01"},
            )
            too_late = await client.call_tool(
                "supersede_fact",
                {"fact_id": ended["id"], "new_value": "Porto", "effective_from": "2026-07-01"},
            )
            return {"early": dict(too_early.structured_content), "late": dict(too_late.structured_content)}

        payload = _run(server, flow)

        assert payload["early"]["error"] == "invalid_supersession"
        assert payload["early"]["reason"] == REASON_NOT_AFTER_VALID_FROM
        assert payload["late"]["reason"] == REASON_AFTER_VALID_TO

    def test_an_unknown_fact_returns_the_shared_record_not_found_payload(self, tmp_path: Path) -> None:
        server = build_server(tmp_path / "unknown.db")

        async def flow(client: Client) -> dict[str, Any]:
            result = await client.call_tool(
                "supersede_fact",
                {"fact_id": "missing", "new_value": "Globex", "effective_from": "2026-07-01"},
            )
            return dict(result.structured_content)

        payload = _run(server, flow)

        assert payload["error"] == "record_not_found"
        assert (payload["entity_type"], payload["entity_id"]) == ("fact", "missing")

    def test_a_supersession_leaves_the_consolidation_read_showing_a_succession(self, tmp_path: Path) -> None:
        """The two M19.2 surfaces agree: a completed transition is history, not a conflict."""
        server = build_server(tmp_path / "round-trip.db")

        async def flow(client: Client) -> dict[str, Any]:
            alice = await _person(client)
            original = await _fact(client, alice, predicate="employer", value="Acme", valid_from="2024-01-01")
            await client.call_tool(
                "supersede_fact",
                {"fact_id": original["id"], "new_value": "Globex", "effective_from": "2026-07-01"},
            )
            result = await client.call_tool("get_consolidation_context", {"person_id": alice})
            return dict(result.structured_content)

        payload = _run(server, flow)

        assert [signal["kind"] for signal in payload["signals"]] == ["succeeding_fact"]
        assert sorted(fact["value"] for fact in payload["facts"]) == ["Acme", "Globex"]


class TestScriptedMaintenanceWorkflow:
    """The packaged skill's maintenance flow, driven end to end over the real tool surface.

    The workflow the skill describes is read → explain → propose → *wait* → write. These tests
    drive the tool calls that flow makes and assert the property the prose promises: the review
    phase leaves the store byte-identical, and the approved write is the one the situation calls
    for — correction for erroneous data, supersession for a value that changed.
    """

    def test_the_review_phase_writes_nothing(self, tmp_path: Path) -> None:
        database = tmp_path / "review.db"
        server = build_server(database)

        async def flow(client: Client) -> dict[str, Any]:
            alice = await _person(client)
            await _fact(client, alice, predicate="employer", value="Acme", valid_from="2024-01-01")
            await _fact(client, alice, predicate="employer", value="Globex", valid_from="2026-07-01")
            before = _history(database)
            # Everything the skill tells an agent to read before proposing anything.
            await client.call_tool("resolve_person", {"query": "Alice"})
            await client.call_tool("get_person_context", {"person_id": alice})
            await client.call_tool("get_person_timeline", {"person_id": alice})
            await client.call_tool("get_consolidation_context", {"person_id": alice})
            return {"before": before, "after": _history(database)}

        counts = _run(server, flow)

        assert counts["after"] == counts["before"]

    def test_an_erroneous_value_is_corrected_in_place(self, tmp_path: Path) -> None:
        """A typo was never true, so nothing about it is worth preserving as history."""
        server = build_server(tmp_path / "correction.db")

        async def flow(client: Client) -> dict[str, Any]:
            alice = await _person(client)
            wrong = await _fact(client, alice, predicate="employer", value="Acme Corp.", valid_from="2024-01-01")
            await client.call_tool(
                "correct_record",
                {"entity_type": "fact", "entity_id": wrong["id"], "fields": {"value": "Acme"}},
            )
            result = await client.call_tool("get_consolidation_context", {"person_id": alice})
            return dict(result.structured_content)

        payload = _run(server, flow)

        assert [fact["value"] for fact in payload["facts"]] == ["Acme"]
        assert payload["facts"][0]["valid_from"] == "2024-01-01"
        # One row, so there is no pair and nothing to consolidate.
        assert payload["signals"] == []

    def test_a_value_that_changed_is_superseded_without_losing_the_old_one(self, tmp_path: Path) -> None:
        server = build_server(tmp_path / "transition.db")

        async def flow(client: Client) -> dict[str, Any]:
            alice = await _person(client)
            original = await _fact(
                client,
                alice,
                predicate="employer",
                value="Acme",
                valid_from="2024-01-01",
                valid_to="2027-12-31",
            )
            await client.call_tool(
                "supersede_fact",
                {"fact_id": original["id"], "new_value": "Globex", "effective_from": "2026-07-01"},
            )
            result = await client.call_tool("get_consolidation_context", {"person_id": alice})
            return {"original_id": original["id"], "payload": dict(result.structured_content)}

        outcome = _run(server, flow)
        by_id = {fact["fact_id"]: fact for fact in outcome["payload"]["facts"]}

        # The old assertion is still readable, with its value intact and its period closed.
        old = by_id[outcome["original_id"]]
        assert (old["value"], old["valid_from"], old["valid_to"]) == ("Acme", "2024-01-01", "2026-06-30")
        # The replacement took over and inherited the original endpoint rather than widening it.
        replacement = next(fact for fact in by_id.values() if fact["fact_id"] != outcome["original_id"])
        assert (replacement["value"], replacement["valid_from"], replacement["valid_to"]) == (
            "Globex",
            "2026-07-01",
            "2027-12-31",
        )
        assert [signal["kind"] for signal in outcome["payload"]["signals"]] == ["succeeding_fact"]


def _history(database: Path) -> tuple[int, int]:
    """Return the durable accountability and replay row counts, read through a fresh connection."""
    conn = sqlite3.connect(database)
    try:
        return (
            conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM changelog").fetchone()[0],
        )
    finally:
        conn.close()


def test_both_tools_are_registered_with_the_expected_annotations(tmp_path: Path) -> None:
    server = build_server(tmp_path / "annotations.db")

    async def flow(client: Client) -> dict[str, Any]:
        tools = await client.list_tools()
        return {tool.name: tool for tool in tools.tools}

    by_name = _run(server, flow)

    assert by_name["get_consolidation_context"].annotations.read_only_hint is True
    assert by_name["supersede_fact"].annotations.read_only_hint is False
    assert by_name["supersede_fact"].annotations.destructive_hint is False
    # The read has no elevated variant; disclosure stays a process-level gate.
    assert "include_sensitive" not in by_name["get_consolidation_context"].input_schema["properties"]
