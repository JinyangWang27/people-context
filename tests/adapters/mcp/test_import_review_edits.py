"""The two review verbs over MCP (M29.1).

What is checked here is the transport half: both tools are writes, both answer with the refreshed
batch rather than the row they touched, and a refusal reaches the client as a tool error carrying
the use case's own code. The rules themselves are tested against the use cases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client import Client

from people_context.adapters.mcp.server import build_server


def _run(server: Any, coro_factory: Any) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await coro_factory(client)

    return anyio.run(main)


_CANDIDATES = [
    {"type": "person", "ref": "p1", "name": "Nadia Okonkwo", "aliases": []},
    {"type": "affiliation", "person_ref": "p1", "org": "Acme", "role": "Engineer"},
]


async def _staged(client: Client) -> tuple[str, list[dict[str, Any]]]:
    batch = await client.call_tool(
        "stage_candidates",
        {"source": "review", "candidates": _CANDIDATES, "source_kind": "notes"},
    )
    batch_id = batch.structured_content["batch_id"]
    review = await client.call_tool("review_import", {"batch_id": batch_id})
    return batch_id, review.structured_content["candidates"]


def test_both_verbs_are_registered_as_writes(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "edits.db")

    async def flow(client: Client) -> Any:
        return await client.list_tools()

    tools = {tool.name: tool for tool in _run(server, flow).tools}

    for name in ("amend_candidate", "withdraw_candidates"):
        assert name in tools
        assert tools[name].annotations is not None
        assert tools[name].annotations.read_only_hint is False


def test_review_import_carries_the_digest_a_caller_acts_on(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "edits.db")

    async def flow(client: Client) -> Any:
        batch_id, _rows = await _staged(client)
        return await client.call_tool("review_import", {"batch_id": batch_id})

    review = _run(server, flow)

    assert review.structured_content["batch_digest"]


def test_amend_returns_the_whole_revised_batch(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "edits.db")

    async def flow(client: Client) -> Any:
        batch_id, rows = await _staged(client)
        affiliation = next(row for row in rows if row["candidate"]["type"] == "affiliation")
        return await client.call_tool(
            "amend_candidate",
            {"batch_id": batch_id, "candidate_id": affiliation["id"], "patch": {"role": "Staff Engineer"}},
        )

    result = _run(server, flow)

    assert result.is_error is False
    roles = [row["candidate"].get("role") for row in result.structured_content["candidates"]]
    assert roles == [None, "Staff Engineer"]
    assert len(result.structured_content["candidates"]) == 2


def test_withdraw_returns_the_whole_revised_batch_with_the_row_still_listed(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "edits.db")

    async def flow(client: Client) -> Any:
        batch_id, rows = await _staged(client)
        affiliation = next(row for row in rows if row["candidate"]["type"] == "affiliation")
        return await client.call_tool(
            "withdraw_candidates", {"batch_id": batch_id, "candidate_ids": [affiliation["id"]]}
        )

    result = _run(server, flow)

    assert [row["status"] for row in result.structured_content["candidates"]] == ["pending", "rejected"]


@pytest.mark.parametrize(
    ("tool", "arguments", "code"),
    [
        ("amend_candidate", {"patch": {"type": "fact"}}, "invalid_candidates"),
        ("amend_candidate", {"patch": {"role": "x"}, "expected_batch_digest": "stale"}, "batch_changed"),
        ("withdraw_candidates", {"expected_batch_digest": "stale"}, "batch_changed"),
    ],
)
def test_a_refusal_reaches_the_client_as_a_tool_error_with_its_own_code(
    tmp_path: Path, tool: str, arguments: dict[str, Any], code: str
) -> None:
    server = build_server(db_path=tmp_path / "edits.db")

    async def flow(client: Client) -> Any:
        batch_id, rows = await _staged(client)
        affiliation = next(row for row in rows if row["candidate"]["type"] == "affiliation")
        target = (
            {"candidate_id": affiliation["id"]}
            if tool == "amend_candidate"
            else {"candidate_ids": [affiliation["id"]]}
        )
        return await client.call_tool(tool, {"batch_id": batch_id, **target, **arguments})

    refused = _run(server, flow)

    assert refused.is_error is True
    assert refused.structured_content["error"] == code


def test_a_refused_amendment_never_echoes_an_undeclared_patch_key(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "edits.db")
    private = "Nadia is being treated at the Mayo Clinic"

    async def flow(client: Client) -> Any:
        batch_id, rows = await _staged(client)
        return await client.call_tool(
            "amend_candidate",
            {"batch_id": batch_id, "candidate_id": rows[0]["id"], "patch": {private: "x"}},
        )

    refused = _run(server, flow)

    assert refused.is_error is True
    assert private not in str(refused.structured_content)
    assert private not in str(refused.content)


def test_commit_import_refuses_a_withdrawn_candidate_and_a_stale_digest(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "edits.db")

    async def flow(client: Client) -> tuple[Any, Any, Any]:
        batch_id, rows = await _staged(client)
        ids = [row["id"] for row in rows]
        stale = (await client.call_tool("review_import", {"batch_id": batch_id})).structured_content[
            "batch_digest"
        ]
        await client.call_tool("withdraw_candidates", {"batch_id": batch_id, "candidate_ids": [ids[1]]})
        named = await client.call_tool("commit_import", {"batch_id": batch_id, "accepted_ids": ids})
        outdated = await client.call_tool(
            "commit_import",
            {"batch_id": batch_id, "accepted_ids": [ids[0]], "expected_batch_digest": stale},
        )
        accepted = await client.call_tool("commit_import", {"batch_id": batch_id, "accepted_ids": [ids[0]]})
        return named, outdated, accepted

    named, outdated, accepted = _run(server, flow)

    assert named.structured_content["error"] == "candidate_withdrawn"
    assert outdated.structured_content["error"] == "batch_changed"
    assert len(accepted.structured_content["committed_ids"]) == 1


def test_an_ambiguous_row_offers_the_people_it_could_be(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "edits.db")

    async def flow(client: Client) -> Any:
        first = await client.call_tool("remember_person", {"name": "Priya Sharma"})
        # `remember_person` updates the person a name already matches, so a second one is made by
        # giving a differently named person the colliding value as an alias.
        second = await client.call_tool("remember_person", {"name": "P. Sharma"})
        await client.call_tool(
            "add_alias", {"person_id": second.structured_content["person"]["id"], "value": "Priya Sharma"}
        )
        assert first.structured_content["person"]["id"] != second.structured_content["person"]["id"]
        batch = await client.call_tool(
            "stage_candidates",
            {
                "source": "review",
                "candidates": [
                    {"type": "person", "ref": "p1", "name": "Priya Sharma", "aliases": []},
                    # An M17 type is what opts the request into the ambiguity-preserving matcher.
                    {"type": "observation", "person_ref": "p1", "text": "spoke at the all-hands"},
                ],
                "source_kind": "notes",
            },
        )
        return await client.call_tool(
            "review_import", {"batch_id": batch.structured_content["batch_id"]}
        )

    review = _run(server, flow)

    row = review.structured_content["candidates"][0]
    assert row["candidate"]["match_disposition"] == "ambiguous"
    assert len(row["match_candidates"]) == 2
    assert row["match_candidates_truncated"] is False
    assert review.structured_content["candidates"][1]["match_candidates"] is None
