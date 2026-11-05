"""An agent resolves a possible duplicate from tool output alone (#222)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
from mcp.client import Client

from people_context.adapters.mcp.server import build_server


def test_review_suggests_an_existing_person_and_amend_reuses_it(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "people.db")

    async def flow(client: Client) -> Any:
        await client.call_tool("remember_person", {"name": "Alex"})
        batch = await client.call_tool(
            "stage_candidates",
            {
                "source": "notes",
                "candidates": [{"type": "person", "ref": "p1", "name": "Alex Morgan", "aliases": []}],
            },
        )
        batch_id = batch.structured_content["batch_id"]
        review = await client.call_tool("review_import", {"batch_id": batch_id})
        (row,) = review.structured_content["candidates"]
        (suggestion,) = row["possible_matches"]
        amended = await client.call_tool(
            "amend_candidate",
            {
                "batch_id": batch_id,
                "candidate_id": row["id"],
                "patch": {"matched_person_id": suggestion["id"]},
                "expected_batch_digest": review.structured_content["batch_digest"],
            },
        )
        await client.call_tool(
            "commit_import",
            {
                "batch_id": batch_id,
                "accepted_ids": [row["id"]],
                "expected_batch_digest": amended.structured_content["batch_digest"],
            },
        )
        resolved = await client.call_tool("resolve_person", {"query": "Alex Morgan"})
        return row, suggestion, resolved.structured_content

    async def main() -> Any:
        async with Client(server) as client:
            return await flow(client)

    row, suggestion, resolved = anyio.run(main)

    assert row["candidate"]["matched_person_id"] is None
    assert suggestion["canonical_name"] == "Alex"
    (candidate,) = resolved["candidates"]
    assert candidate["person_id"] == suggestion["id"]
    assert candidate["match_reason"] == "exact"
