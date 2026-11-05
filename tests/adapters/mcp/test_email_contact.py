"""The #225 correction end to end over SQLite: an address stored as a name becomes contact information.

A Linear import once stored `contact@example.com` as a person's canonical name. Through the published
tools only, a new person can no longer be named by an address, the stored one is renamed without losing
the address, and afterwards the address still resolves to the person and is reported as an email.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
from mcp.client import Client

from people_context.adapters.mcp.server import build_server
from people_context.adapters.sqlite import SqlitePeopleRepository, open_db
from people_context.domain.person import Person


def _run(server: Any, coro_factory: Any) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await coro_factory(client)

    return anyio.run(main)


def test_an_address_stored_as_a_name_is_corrected_into_contact_information(tmp_path: Path) -> None:
    db_path = tmp_path / "people.db"
    conn = open_db(str(db_path))
    legacy = Person(canonical_name="contact@example.com")
    SqlitePeopleRepository(conn).save_person(legacy)
    conn.commit()
    conn.close()
    server = build_server(db_path=db_path)

    async def flow(client: Client) -> dict[str, Any]:
        return {
            "new_by_email": await client.call_tool("remember_person", {"name": "sam@example.com"}),
            "note_by_email": await client.call_tool("remember", {"person": "sam@example.com", "note": "Met at QBR"}),
            "staged_by_email": await client.call_tool(
                "stage_candidates",
                {"source": "linear", "candidates": [{"type": "person", "ref": "p1", "name": "sam@example.com"}]},
            ),
            "rename_to_email": await client.call_tool(
                "rename_person", {"person_id": legacy.id, "name": "other@example.com"}
            ),
            "renamed": await client.call_tool("rename_person", {"person_id": legacy.id, "name": "Jordan Lee"}),
            "resolved": await client.call_tool("resolve_person", {"query": "contact@example.com"}),
            "context": await client.call_tool("get_person_context", {"person_id": legacy.id}),
        }

    out = _run(server, flow)

    assert out["new_by_email"].is_error is True
    assert out["new_by_email"].structured_content["error"] == "name_is_email"
    assert out["note_by_email"].structured_content["status"] == "invalid_request"
    assert out["staged_by_email"].is_error is True
    assert "name_is_email" in str(out["staged_by_email"].structured_content)
    assert out["rename_to_email"].is_error is True
    assert out["rename_to_email"].structured_content["details"][0]["type"] == "name_is_email"

    renamed = out["renamed"].structured_content
    assert renamed["canonical_name"] == "Jordan Lee"
    assert [(alias["value"], alias["kind"]) for alias in renamed["aliases"]] == [("contact@example.com", "handle")]
    resolved = out["resolved"].structured_content["candidates"]
    assert [candidate["person_id"] for candidate in resolved] == [legacy.id]
    identity = out["context"].structured_content["identity"]
    assert identity["canonical_name"] == "Jordan Lee"
    assert identity["emails"] == ["contact@example.com"]

    conn = open_db(str(db_path))
    try:
        names = [row[0] for row in conn.execute("SELECT canonical_name FROM persons ORDER BY canonical_name")]
    finally:
        conn.close()
    assert names == ["Jordan Lee"]
