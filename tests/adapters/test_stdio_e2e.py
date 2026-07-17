"""End-to-end proof through a real MCP stdio subprocess and the CLI."""

from __future__ import annotations

import mailbox
import shutil
import subprocess
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from people_context.adapters.sqlite import SqliteAuditLog, SqliteContextReader, open_db


def test_real_stdio_remember_resolve_with_hints_context_then_cli_show(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    project_root = Path(__file__).parents[2]
    db_path = tmp_path / "people.db"
    parameters = StdioServerParameters(
        command=uv,
        args=["run", "people-context-mcp", "--db", str(db_path)],
        cwd=project_root,
    )

    async def flow() -> tuple[str, dict[str, Any], dict[str, Any]]:
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as client,
        ):
            await client.initialize()
            tools = await client.list_tools()
            assert {"remember_person", "resolve_person", "get_person_context"} <= {tool.name for tool in tools.tools}

            remembered = await client.call_tool(
                "remember_person",
                {"name": "Alice Example", "aliases": [{"value": "Ally"}], "summary": "Colleague"},
            )
            person_id = remembered.structuredContent["person"]["id"]
            _seed_context(db_path, person_id)

            resolved = await client.call_tool(
                "resolve_person",
                {"query": "Alice Example", "hints": {"org": "Acme"}},
            )
            context = await client.call_tool(
                "get_person_context",
                {"person_id": person_id, "max_items": 1},
            )
            return person_id, resolved.structuredContent, context.structuredContent

    person_id, resolved, context = anyio.run(flow)

    assert resolved["candidates"][0]["person_id"] == person_id
    assert resolved["candidates"][0]["match_reason"] == "exact+hint:org"
    assert context["affiliations"][0]["organization_name"] == "Acme Corp"
    assert context["facts"][0]["value"] == "Dubai"
    assert context["reminders"][0]["text"] == "Prefer written updates"

    shown = subprocess.run(
        [uv, "run", "people-context", "--db", str(db_path), "show", person_id],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert shown.returncode == 0, shown.stderr
    assert "Alice Example" in shown.stdout
    assert "Engineer at Acme Corp" in shown.stdout
    assert "location: Dubai" in shown.stdout
    assert "Prefer written updates" in shown.stdout


def test_real_stdio_m2_full_write_read_guidance_round_trip(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    project_root = Path(__file__).parents[2]
    db_path = tmp_path / "m2-stdio.db"
    parameters = StdioServerParameters(
        command=uv,
        args=["run", "people-context-mcp", "--db", str(db_path)],
        cwd=project_root,
    )
    philosophy = "道可道，非常道；上善若水。"

    async def flow() -> tuple[str, dict[str, Any], dict[str, Any], str]:
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as client,
        ):
            await client.initialize()
            me = (
                await client.call_tool("remember_person", {"name": "Me", "is_self": True})
            ).structuredContent["person"]
            alice = (
                await client.call_tool("remember_person", {"name": "Alice Example"})
            ).structuredContent["person"]
            person_id = alice["id"]
            await client.call_tool(
                "set_relationship",
                {"subject_id": me["id"], "object_id": person_id, "type": "friend_of"},
            )
            await client.call_tool(
                "set_affiliation",
                {"person_id": person_id, "org": "Acme Corp", "role": "Engineer"},
            )
            await client.call_tool(
                "record_fact",
                {"person_id": person_id, "predicate": "location", "value": "Dubai", "sensitivity": "public"},
            )
            observation = (
                await client.call_tool(
                    "record_observation", {"person_id": person_id, "text": "Subjective and private"}
                )
            ).structuredContent
            await client.call_tool(
                "record_trait",
                {
                    "person_id": person_id,
                    "category": "communication_style",
                    "value": "Prefers written summaries",
                },
            )
            await client.call_tool(
                "record_interaction",
                {"summary": "Needed a clearer scope", "participant_ids": [me["id"], person_id]},
            )
            await client.call_tool("set_communication_philosophy", {"text": philosophy})
            context = (
                await client.call_tool("get_person_context", {"person_id": person_id, "max_items": 10})
            ).structuredContent
            guidance = (
                await client.call_tool(
                    "get_communication_guidance", {"person_id": person_id, "situation": "Plan launch"}
                )
            ).structuredContent
            return person_id, context, guidance, observation["id"]

    person_id, context, guidance, observation_id = anyio.run(flow)

    assert context["facts"][0]["value"] == "Dubai"
    assert context["relationships"][0]["relationship"]["type"] == "friend_of"
    assert context["affiliations"][0]["organization_name"] == "Acme Corp"
    assert context["observations"] == []
    assert guidance["traits"]["communication_style"][0]["value"] == "Prefers written summaries"
    assert guidance["friction_notes"] == ["Needed a clearer scope"]
    assert guidance["communication_philosophy"] == philosophy
    assert guidance["situation"] == "Plan launch"
    assert "Subjective and private" not in str(guidance)

    conn = open_db(db_path)
    try:
        observations = SqliteContextReader(conn).list_observations(person_id)
        entries = SqliteAuditLog(conn).list_entries(limit=100)
    finally:
        conn.close()
    assert [observation.id for observation in observations] == [observation_id]
    assert len(entries) == 10
    philosophy_entry = next(entry for entry in entries if entry.entity_type == "preference")
    assert philosophy not in str(philosophy_entry.payload)


def test_real_stdio_mbox_import_commit_and_resolve(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    project_root = Path(__file__).parents[2]
    db_path = tmp_path / "m3-stdio.db"
    mbox_path = tmp_path / "mailbox.mbox"
    box = mailbox.mbox(mbox_path)
    try:
        message = EmailMessage()
        message["From"] = "Alice Example <alice@example.com>"
        message["To"] = "Bob Example <bob@example.com>"
        message["Date"] = "Wed, 04 Mar 2026 09:06:00 +0400"
        message["Subject"] = "Project update"
        message["Message-ID"] = "<stdio-message@example.com>"
        message.set_content("body is intentionally discarded")
        box.add(message)
        box.flush()
    finally:
        box.close()
    parameters = StdioServerParameters(
        command=uv,
        args=["run", "people-context-mcp", "--db", str(db_path)],
        cwd=project_root,
    )

    async def flow() -> tuple[dict[str, Any], dict[str, Any]]:
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as client,
        ):
            await client.initialize()
            imported = await client.call_tool("import_content", {"source_type": "mbox", "path": str(mbox_path)})
            batch_id = imported.structuredContent["batch_id"]
            reviewed = await client.call_tool("review_import", {"batch_id": batch_id})
            accepted_ids = [row["id"] for row in reviewed.structuredContent["candidates"]]
            committed = await client.call_tool(
                "commit_import",
                {"batch_id": batch_id, "accepted_ids": accepted_ids},
            )
            resolved = await client.call_tool("resolve_person", {"query": "alice@example.com"})
            return committed.structuredContent, resolved.structuredContent

    committed, resolved = anyio.run(flow)

    assert len(committed["committed_ids"]) == 3
    assert committed["unresolved_ids"] == []
    assert resolved["candidates"][0]["canonical_name"] == "Alice Example"


def _seed_context(db_path: Path, person_id: str) -> None:
    conn = open_db(db_path)
    try:
        with conn:
            conn.execute("INSERT INTO organizations (id, name, kind) VALUES ('org-1', 'Acme Corp', 'company')")
            conn.execute(
                """
                INSERT INTO affiliations (
                    id, person_id, org_id, role, confidence, provenance_source, created_at
                ) VALUES ('aff-1', ?, 'org-1', 'Engineer', 1.0, 'test', '2025-01-01T00:00:00+00:00')
                """,
                (person_id,),
            )
            conn.execute(
                """
                INSERT INTO facts (
                    id, person_id, predicate, value, recorded_at, confidence, sensitivity, provenance_source
                ) VALUES (
                    'fact-1', ?, 'location', 'Dubai', '2025-01-02T00:00:00+00:00', 1.0, 'public', 'test'
                )
                """,
                (person_id,),
            )
            conn.execute(
                """
                INSERT INTO reminders (
                    id, person_id, text, kind, status, created_at
                ) VALUES (
                    'reminder-1', ?, 'Prefer written updates', 'communication_note', 'active',
                    '2025-01-03T00:00:00+00:00'
                )
                """,
                (person_id,),
            )
    finally:
        conn.close()
