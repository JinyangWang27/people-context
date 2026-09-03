"""Tests for the MCP stdio adapter via the SDK's in-memory client/server."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client import Client

from people_context import __version__ as package_version
from people_context.adapters import runtime as runtime_module
from people_context.adapters.mcp.server import build_server
from people_context.adapters.model2vec_embeddings import MODEL_ID
from people_context.adapters.sqlite import SqliteAuditLog, SqlitePeopleRepository, open_db
from people_context.adapters.sqlite.semantic import create_sqlite_vector_index
from people_context.domain.person import Person
from people_context.ports.semantic import SemanticDocument, SemanticIndexMetadata

EXPECTED_SERVER_INSTRUCTIONS = (
    "people-context stores durable, local-first knowledge about the people in the user's life: "
    "their names and aliases, how they relate to the user, their organisations and roles, and "
    "relevant past interactions. "
    "When the user mentions someone by name, nickname, or partial reference, call `resolve_person` "
    "first to find who they mean — prefer resolving before asking the user who someone is. "
    "After resolving an identity, use `get_person_context` for a bounded, sensitivity-aware context bundle. "
    "After resolving a person, use `get_communication_guidance` when communication help is requested. "
    "Use `search_people` for broader browsing and `remember_person` to record a new or updated person. "
    "When the user states one durable thing about someone directly, call `remember` once: it resolves the "
    "name, creates the person only if nobody matches, and records the note, affiliation, or relationship in "
    "one audited transaction — never guessing between similar names. "
    "Use `stage_candidates` only for concise structured proposals — not raw source text — that are left for "
    "later user review and never committed automatically. "
    "Ordinary read tools that take a `person_id` also accept `person` (a name) in its place; "
    "`find_connection` and the operator-elevated tools take ids only. "
    "The full usage guide is the `people-context://guide` resource; `people-context://self` is the user's own record. "
    "Read-only tools are safe to call freely; write and destructive tools are annotated so the client "
    "can gate them behind its normal approval flow."
)

EXPECTED_TOOLS = {
    # real
    "resolve_person",
    "search_people",
    "semantic_search",
    "remember_person",
    "remember",
    # read-only stubs
    "get_person_context",
    "get_communication_guidance",
    "list_reminders",
    # write stubs
    "add_alias",
    "set_relationship",
    "set_affiliation",
    "record_fact",
    "record_observation",
    "record_trait",
    "record_interaction",
    "correct_record",
    "set_reminder",
    "complete_reminder",
    "set_communication_philosophy",
    "import_content",
    "stage_candidates",
    "review_import",
    "commit_import",
    # destructive stubs
    "merge_people",
    "forget",
}


def _run(server: Any, coro_factory: Any) -> Any:
    async def main() -> Any:
        async with Client(server) as client:
            return await coro_factory(client)

    return anyio.run(main)


def test_initialize_advertises_the_released_package_version(tmp_path: Path) -> None:
    """The handshake must name a version, and the same one the package ships under.

    `MCPServer` defaults `version` to the empty string, so forgetting to pass it is
    silent: clients and directory listings simply show a nameless build. Binding the
    assertion to `people_context.__version__` — which release automation bumps beside
    `pyproject.toml`, `server.json`, and the MCPB manifest — keeps this surface in step
    with the other released version values instead of adding one more to hand-maintain.
    """
    server = build_server(db_path=tmp_path / "version.db")

    async def initialize() -> Any:
        async with Client(server) as client:
            return client.server_info

    server_info = anyio.run(initialize)

    assert server_info is not None
    assert server_info.name == "people-context"
    assert server_info.version == package_version
    assert server_info.version != ""


def test_initialize_returns_safe_tool_composition_instructions(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "instructions.db")

    async def initialize() -> str | None:
        async with Client(server) as client:
            return client.instructions

    instructions = anyio.run(initialize)

    assert instructions == EXPECTED_SERVER_INSTRUCTIONS
    required_tool_order = (
        "`resolve_person`",
        "`get_person_context`",
        "`get_communication_guidance`",
        "`stage_candidates`",
    )
    assert instructions is not None
    assert [instructions.index(tool_name) for tool_name in required_tool_order] == sorted(
        instructions.index(tool_name) for tool_name in required_tool_order
    )
    assert "only for concise structured proposals" in instructions
    assert "not raw source text" in instructions
    assert "later user review and never committed automatically" in instructions
    assert "get_sensitive_person_context" not in instructions
    assert "export_data" not in instructions


def test_tools_list_surface_and_annotations(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "t.db")

    async def collect(client: Client) -> Any:
        return await client.list_tools()

    result = _run(server, collect)
    by_name = {tool.name: tool for tool in result.tools}

    assert set(by_name) >= EXPECTED_TOOLS
    assert by_name["resolve_person"].annotations.read_only_hint is True
    assert by_name["semantic_search"].annotations.read_only_hint is True
    assert by_name["stage_candidates"].annotations.read_only_hint is False
    assert by_name["stage_candidates"].annotations.destructive_hint is False
    assert by_name["get_person_context"].annotations.read_only_hint is True
    assert by_name["get_communication_guidance"].annotations.read_only_hint is True
    assert by_name["list_reminders"].annotations.read_only_hint is True
    assert "hints" in by_name["resolve_person"].input_schema["properties"]
    assert by_name["get_person_context"].input_schema["properties"]["max_items"]["default"] == 10
    assert "include_sensitive" not in by_name["get_person_context"].input_schema["properties"]
    assert "get_sensitive_person_context" not in by_name
    assert "export_data" not in by_name
    assert by_name["merge_people"].annotations.destructive_hint is True
    assert by_name["remember_person"].annotations.read_only_hint is False
    assert by_name["record_fact"].annotations.read_only_hint is False
    assert by_name["record_fact"].annotations.destructive_hint is False


def test_high_disclosure_tools_require_process_elevation(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE", "1")
    monkeypatch.setenv("PEOPLE_CONTEXT_MCP_ENABLE_EXPORT", "true")
    server = build_server(db_path=tmp_path / "elevated.db")

    async def collect(client: Client) -> Any:
        return await client.list_tools()

    result = _run(server, collect)
    by_name = {tool.name: tool for tool in result.tools}

    assert "get_sensitive_person_context" in by_name
    assert "include_sensitive" not in by_name["get_sensitive_person_context"].input_schema["properties"]
    assert by_name["get_sensitive_person_context"].annotations.read_only_hint is True
    assert "export_data" in by_name
    assert by_name["export_data"].annotations.read_only_hint is True


def test_remember_then_resolve_and_audit_row(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    server = build_server(db_path=db_path)

    async def flow(client: Client) -> tuple[dict[str, Any], dict[str, Any]]:
        remembered = await client.call_tool(
            "remember_person",
            {"name": "Jinyang Wang", "aliases": [{"value": "JW", "kind": "nickname"}], "summary": "me"},
        )
        resolved = await client.call_tool("resolve_person", {"query": "Jinyang"})
        return remembered.structured_content, resolved.structured_content

    remember_payload, resolve_payload = _run(server, flow)

    assert remember_payload["created"] is True
    person_id = remember_payload["person"]["id"]

    candidates = resolve_payload["candidates"]
    assert any(candidate["person_id"] == person_id for candidate in candidates)

    # An audit row must have been persisted to the DB file.
    conn = open_db(db_path)
    try:
        entries = SqliteAuditLog(conn).list_entries()
    finally:
        conn.close()
    assert any(entry.entity_id == person_id and entry.op == "create" for entry in entries)


def test_resolve_person_reports_additive_match_detail_for_both_scripts(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "detail.db")

    async def flow(client: Client) -> tuple[dict[str, Any], dict[str, Any]]:
        await client.call_tool(
            "remember_person",
            {"name": "Wang Xiaoming", "aliases": [{"value": "王小明", "kind": "native_script"}]},
        )
        by_canonical = await client.call_tool("resolve_person", {"query": "wang xiaoming"})
        by_alias = await client.call_tool("resolve_person", {"query": "王小明"})
        return by_canonical.structured_content, by_alias.structured_content

    canonical_payload, alias_payload = _run(server, flow)

    canonical_candidate = canonical_payload["candidates"][0]
    alias_candidate = alias_payload["candidates"][0]
    assert canonical_candidate["match_detail"] == "canonical_name"
    assert alias_candidate["match_detail"] == "alias:native_script"
    # Additive only: the pre-M15 fields keep their exact shape and values.
    assert canonical_candidate["match_reason"] == alias_candidate["match_reason"] == "exact"
    assert canonical_candidate["score"] == alias_candidate["score"] == 1.0
    assert canonical_candidate["person_id"] == alias_candidate["person_id"]
    assert canonical_payload["ambiguous"] is False


def test_import_content_returns_structured_error_for_empty_email(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "t.db")

    async def call(client: Client) -> dict[str, Any]:
        result = await client.call_tool("import_content", {"source_type": "email", "content": "x"})
        return result.structured_content

    payload = _run(server, call)
    assert payload["error"] == "no_candidates"


def test_import_content_reports_skipped_dateless_interaction(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "t.db")
    content = "\n".join(
        [
            "From: Alice <alice@example.com>",
            "To: Bob <bob@example.com>",
            "Date: invalid",
            "Message-ID: <dateless@example.com>",
            "",
        ]
    )

    async def call(client: Client) -> dict[str, Any]:
        result = await client.call_tool("import_content", {"source_type": "email", "content": content})
        assert result.structured_content is not None
        return result.structured_content

    payload = _run(server, call)
    assert payload["candidate_count"] == 2
    assert payload["skipped_message_ids"] == ["<dateless@example.com>"]


def test_import_content_reports_dateless_interaction_without_message_id(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "t.db")
    content = "From: Alice <alice@example.com>\nTo: Bob <bob@example.com>\n\n"

    async def flow(client: Client) -> Any:
        return await client.call_tool("import_content", {"source_type": "email", "content": content})

    payload = _run(server, flow).structured_content

    assert payload["candidate_count"] == 2
    assert payload["skipped_message_ids"] == []
    assert payload["skipped_without_id"] == 1
    assert payload["skipped_cards"] == []


def test_semantic_search_before_reindex_is_not_available_without_network(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    network_calls: list[bool] = []

    def reject_network(*args: Any, **kwargs: Any) -> None:
        network_calls.append(True)
        raise AssertionError("search must not download")

    monkeypatch.setattr("huggingface_hub.snapshot_download", reject_network)
    server = build_server(db_path=tmp_path / "semantic.db")

    async def flow(client: Client) -> Any:
        return await client.call_tool("semantic_search", {"query": "SQL engineer"})

    payload = _run(server, flow).structured_content

    assert payload == {
        "status": "not_available",
        "reason": "semantic index metadata is missing; run semantic reindex",
        "install": "uv sync --extra semantic",
        "retry": "uv run pctx reindex --semantic",
    }
    assert network_calls == []


def test_semantic_search_refuses_model_mismatch(tmp_path: Path) -> None:
    pytest.importorskip("sqlite_vec")
    db_path = tmp_path / "semantic-mismatch.db"
    conn = open_db(db_path)
    try:
        create_sqlite_vector_index(conn).replace_all(
            [],
            [],
            SemanticIndexMetadata(model_id="old/model", dimension=256),
        )
    finally:
        conn.close()
    server = build_server(db_path=db_path)

    async def flow(client: Client) -> Any:
        return await client.call_tool("semantic_search", {"query": "SQL engineer"})

    payload = _run(server, flow).structured_content

    assert payload["status"] == "model_mismatch"
    assert payload["stored_model_id"] == "old/model"
    assert payload["current_model_id"] == MODEL_ID


def test_semantic_search_hydrates_active_person_and_exposes_cosine_similarity(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    pytest.importorskip("sqlite_vec")

    class FakeProvider:
        model_id = MODEL_ID
        dimension = 256

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, *([0.0] * 255)] for _ in texts]

    db_path = tmp_path / "semantic-success.db"
    conn = open_db(db_path)
    try:
        person = Person(canonical_name="Alice", summary="SQL engineer")
        SqlitePeopleRepository(conn).save_person(person)
        create_sqlite_vector_index(conn).replace_all(
            [SemanticDocument(kind="person", entity_id=person.id, text="Alice\nSQL engineer")],
            [[1.0, *([0.0] * 255)]],
            SemanticIndexMetadata(model_id=MODEL_ID, dimension=256),
        )
    finally:
        conn.close()
    monkeypatch.setattr(runtime_module, "create_local_embedding_provider", FakeProvider)
    server = build_server(db_path=db_path)

    async def flow(client: Client) -> Any:
        return await client.call_tool("semantic_search", {"query": "SQL engineer", "kinds": ["person"]})

    payload = _run(server, flow).structured_content

    assert payload["status"] == "ok"
    assert payload["model_id"] == MODEL_ID
    assert payload["hits"] == [
        {
            "kind": "person",
            "entity_id": person.id,
            "score": 1.0,
            "title": "Alice",
            "summary": "SQL engineer",
        }
    ]


def test_vcard_import_reports_per_card_skips_and_stages_valid_neighbors(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "vcard.db")
    content = "\n".join(
        [
            "BEGIN:VCARD",
            "VERSION:4.0",
            "FN:Alice",
            "END:VCARD",
            "BEGIN:VCARD",
            "VERSION:2.1",
            "FN:Old",
            "END:VCARD",
        ]
    )

    async def flow(client: Client) -> Any:
        return await client.call_tool(
            "import_content",
            {"source_type": "vcard", "content": content},
        )

    payload = _run(server, flow).structured_content

    assert payload["candidate_count"] == 1
    assert payload["skipped_cards"] == [{"index": 2, "reason": "unsupported_version"}]


def test_all_invalid_vcards_return_no_candidates_with_skip_details(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "invalid-vcard.db")
    content = "BEGIN:VCARD\nVERSION:4.0\nEMAIL:nobody@example.com\nEND:VCARD\n"

    async def flow(client: Client) -> Any:
        return await client.call_tool(
            "import_content",
            {"source_type": "vcard", "content": content},
        )

    payload = _run(server, flow).structured_content

    assert payload["error"] == "no_candidates"
    assert payload["skipped_cards"] == [{"index": 1, "reason": "missing_fn"}]


def test_ics_import_stages_attendees_and_omits_free_text(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "ics.db")
    summary_sentinel = "MCP-ICS-SUMMARY-MUST-NOT-LEAK-71bd"
    content = "\n".join(
        [
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT",
            "UID:mcp-event@example.com",
            f"SUMMARY:{summary_sentinel}",
            "DTSTART:20260304T090600Z",
            "ATTENDEE;CN=Alice Example:mailto:alice@example.com",
            "ATTENDEE:mailto:bob@example.com",
            "END:VEVENT",
            "BEGIN:VEVENT",
            "DTSTART:20260305T090600",
            "ATTENDEE:mailto:carol@example.com",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )

    async def flow(client: Client) -> Any:
        imported = await client.call_tool("import_content", {"source_type": "ics", "content": content})
        reviewed = await client.call_tool("review_import", {"batch_id": imported.structured_content["batch_id"]})
        return imported.structured_content, reviewed.structured_content

    imported, reviewed = _run(server, flow)

    assert imported["candidate_count"] == 3
    assert imported["skipped_cards"] == [{"index": 2, "reason": "floating_dtstart_unsupported"}]
    summaries = [
        row["candidate"]["summary"] for row in reviewed["candidates"] if row["candidate"]["type"] == "interaction"
    ]
    assert summaries == ["Calendar event"]
    assert summary_sentinel not in str(reviewed)


def test_linkedin_import_stages_safe_rows_and_reports_invalid_neighbors(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "linkedin.db")
    url_sentinel = "MCP-LINKEDIN-URL-MUST-NOT-LEAK-71bd"
    note_sentinel = "MCP-LINKEDIN-NOTE-MUST-NOT-LEAK-92ac"
    content = "\n".join(
        [
            "First Name,Last Name,URL,Email Address,Company,Position,Connected On,Notes",
            f"Alice,Example,{url_sentinel},alice@example.com,Acme,Engineer,04 Mar 2026,{note_sentinel}",
            "Bad,Date,url,bad@example.com,Acme,Engineer,not-a-date,note",
        ]
    )

    async def flow(client: Client) -> Any:
        imported = await client.call_tool("import_content", {"source_type": "linkedin", "content": content})
        reviewed = await client.call_tool("review_import", {"batch_id": imported.structured_content["batch_id"]})
        return imported.structured_content, reviewed.structured_content

    imported, reviewed = _run(server, flow)

    assert imported["candidate_count"] == 3
    assert imported["skipped_cards"] == [{"index": 2, "reason": "invalid_connected_on"}]
    assert {row["source"] for row in reviewed["candidates"]} == {"import/linkedin"}
    assert url_sentinel not in str((imported, reviewed))
    assert note_sentinel not in str((imported, reviewed))


def test_outlook_import_stages_contacts_without_notes_or_web_pages(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "outlook.db")
    note_sentinel = "MCP-OUTLOOK-NOTE-MUST-NOT-LEAK-4b71"
    url_sentinel = "MCP-OUTLOOK-URL-MUST-NOT-LEAK-2ce8"
    content = "\n".join(
        [
            "First Name,Middle Name,Last Name,E-mail Address,Company,Job Title,Web Page,Birthday,Notes",
            f"Alice,,Example,alice@example.com,Acme,Engineer,{url_sentinel},1985-01-23,{note_sentinel}",
            f"Bad,,Email,not-an-address,Acme,Engineer,{url_sentinel},,{note_sentinel}",
        ]
    )

    async def flow(client: Client) -> Any:
        imported = await client.call_tool("import_content", {"source_type": "outlook", "content": content})
        reviewed = await client.call_tool("review_import", {"batch_id": imported.structured_content["batch_id"]})
        return imported.structured_content, reviewed.structured_content

    imported, reviewed = _run(server, flow)

    assert imported["candidate_count"] == 3
    assert imported["skipped_cards"] == [{"index": 2, "reason": "invalid_email"}]
    assert {row["source"] for row in reviewed["candidates"]} == {"import/outlook"}
    assert note_sentinel not in str((imported, reviewed))
    assert url_sentinel not in str((imported, reviewed))


def test_whatsapp_import_commits_without_message_bodies_reaching_person_context(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "whatsapp.db")
    body_sentinel = "MCP-WHATSAPP-BODY-MUST-NOT-LEAK-9d34"
    content = "\n".join(
        [
            f"[13/02/2025, 10:12:45] Alice Example: {body_sentinel}",
            f"a wrapped body line carrying {body_sentinel}",
            f"[13/02/2025, 10:13:00] You: {body_sentinel}",
            f"[14/02/2025, 09:00:00] Alice Example: {body_sentinel}",
        ]
    )

    async def flow(client: Client) -> Any:
        imported = await client.call_tool(
            "import_content",
            {"source_type": "whatsapp", "content": content, "self_sender": "You"},
        )
        batch_id = imported.structured_content["batch_id"]
        reviewed = await client.call_tool("review_import", {"batch_id": batch_id})
        accepted_ids = [row["id"] for row in reviewed.structured_content["candidates"]]
        await client.call_tool("commit_import", {"batch_id": batch_id, "accepted_ids": accepted_ids})
        resolved = await client.call_tool("resolve_person", {"query": "Alice Example"})
        person_id = resolved.structured_content["candidates"][0]["person_id"]
        context = await client.call_tool("get_person_context", {"person_id": person_id})
        return imported.structured_content, reviewed.structured_content, context.structured_content

    imported, reviewed, context = _run(server, flow)

    assert imported["candidate_count"] == 3
    assert imported["skipped_cards"] == []
    assert {row["source"] for row in reviewed["candidates"]} == {"import/whatsapp"}
    assert context["identity"]["canonical_name"] == "Alice Example"
    assert [interaction["summary"] for interaction in context["interactions"]] == [
        "WhatsApp chat",
        "WhatsApp chat",
    ]
    assert body_sentinel not in str((imported, reviewed, context))


def test_stage_candidates_returns_strict_validation_details(tmp_path: Path) -> None:
    server = build_server(db_path=tmp_path / "agent-stage.db")

    async def flow(client: Client) -> Any:
        return await client.call_tool(
            "stage_candidates",
            {
                "source": "notes",
                "candidates": [
                    {
                        "type": "person",
                        "ref": "alice",
                        "name": "Alice",
                        "aliases": [],
                        "unexpected": "forbidden",
                    }
                ],
            },
        )

    payload = _run(server, flow).structured_content

    assert payload["error"] == "invalid_candidates"
    assert payload["details"][0]["type"] == "extra_forbidden"
    assert payload["allowed_types"] == [
        "person",
        "interaction",
        "affiliation",
        "fact",
        "observation",
        "trait",
        "relationship",
    ]
    assert payload["valid_fields"]["person"] == [
        "type",
        "ref",
        "name",
        "aliases",
        "summary",
        "message_id",
        "date",
    ]


def test_stage_candidates_accepts_extraction_types_and_commits_through_the_review_gate(tmp_path: Path) -> None:
    """The M17 vocabulary is reachable end to end over MCP without a second lifecycle."""
    db_path = tmp_path / "extraction.db"
    server = build_server(db_path=db_path)

    async def flow(client: Client) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        staged = await client.call_tool(
            "stage_candidates",
            {
                "source": "planning-meeting",
                "candidates": [
                    {"type": "person", "ref": "alice", "name": "Alice Rivera", "aliases": []},
                    {"type": "person", "ref": "bob", "name": "Bob Chen", "aliases": []},
                    {"type": "observation", "person_ref": "alice", "text": "Asked for metrics first"},
                    {
                        "type": "trait",
                        "person_ref": "alice",
                        "category": "communication_style",
                        "value": "Responds to quantitative proposals",
                        "evidence_note": "Asked for measurable evidence twice in one meeting.",
                        "confidence": 0.6,
                    },
                    {
                        "type": "relationship",
                        "from_ref": "alice",
                        "to_ref": "bob",
                        "relationship_type": "colleague",
                    },
                ],
            },
        )
        batch_id = staged.structured_content["batch_id"]
        reviewed = await client.call_tool("review_import", {"batch_id": batch_id})
        accepted = [row["id"] for row in reviewed.structured_content["candidates"]]
        committed = await client.call_tool("commit_import", {"batch_id": batch_id, "accepted_ids": accepted})
        return staged.structured_content, reviewed.structured_content, committed.structured_content

    staged, reviewed, committed = _run(server, flow)

    assert staged["candidate_count"] == 5
    assert {row["candidate"]["type"] for row in reviewed["candidates"]} == {
        "person",
        "observation",
        "trait",
        "relationship",
    }
    people = [row for row in reviewed["candidates"] if row["candidate"]["type"] == "person"]
    assert all(row["candidate"]["match_disposition"] == "unmatched" for row in people)
    assert committed["unresolved_ids"] == []
    assert len(committed["committed_ids"]) == 5

    conn = open_db(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM traits").fetchone()[0] == 1
        assert conn.execute("SELECT type FROM relationships").fetchone()["type"] == "colleague_of"
    finally:
        conn.close()


def test_merge_people_tool_is_real_and_returns_structured_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "merge.db"
    conn = open_db(db_path)
    repo = SqlitePeopleRepository(conn)
    primary = Person(canonical_name="Alice")
    duplicate = Person(canonical_name="Alice Smith")
    repo.save_person(primary)
    repo.save_person(duplicate)
    conn.close()
    server = build_server(db_path=db_path)

    async def flow(client: Client) -> tuple[dict[str, Any], dict[str, Any]]:
        invalid = await client.call_tool("merge_people", {"primary_id": primary.id, "duplicate_id": primary.id})
        merged = await client.call_tool("merge_people", {"primary_id": primary.id, "duplicate_id": duplicate.id})
        return invalid.structured_content, merged.structured_content

    invalid, merged = _run(server, flow)

    assert invalid["error"] == "same_person"
    assert merged["person"]["id"] == primary.id
    assert merged["self_loops_removed"] == 0


def test_record_write_read_curation_and_guidance_flow(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE", "1")
    db_path = tmp_path / "m2.db"
    server = build_server(db_path=db_path)

    async def flow(client: Client) -> dict[str, Any]:
        me = (await client.call_tool("remember_person", {"name": "Me", "is_self": True})).structured_content["person"]
        alice = (await client.call_tool("remember_person", {"name": "Alice"})).structured_content["person"]
        person_id = alice["id"]
        await client.call_tool(
            "set_relationship",
            {"subject_id": me["id"], "object_id": person_id, "type": "friend_of"},
        )
        await client.call_tool(
            "set_affiliation",
            {"person_id": person_id, "org": "Acme", "role": "Engineer"},
        )
        fact = (
            await client.call_tool(
                "record_fact",
                {"person_id": person_id, "predicate": "location", "value": "Dubia", "sensitivity": "public"},
            )
        ).structured_content
        sensitive_fact = (
            await client.call_tool(
                "record_fact",
                {"person_id": person_id, "predicate": "private", "value": "secret", "sensitivity": "sensitive"},
            )
        ).structured_content
        observation = (
            await client.call_tool("record_observation", {"person_id": person_id, "text": "Never disclose"})
        ).structured_content
        await client.call_tool(
            "record_trait",
            {"person_id": person_id, "category": "communication_style", "value": "Prefers concise writing"},
        )
        await client.call_tool(
            "record_trait",
            {
                "person_id": person_id,
                "category": "topics_to_avoid",
                "value": "Restricted topic",
                "sensitivity": "restricted",
            },
        )
        await client.call_tool(
            "record_interaction",
            {"summary": "Discussed launch friction", "participant_ids": [me["id"], person_id]},
        )
        philosophy = "道可道，非常道"
        await client.call_tool("set_communication_philosophy", {"text": philosophy})
        follow_up = (
            await client.call_tool(
                "set_reminder",
                {
                    "person_id": person_id,
                    "text": "Follow up",
                    "kind": "follow_up",
                    "due_at": "2025-07-01T00:00:00+00:00",
                },
            )
        ).structured_content
        note = (
            await client.call_tool(
                "set_reminder",
                {"person_id": person_id, "text": "Use email", "kind": "communication_note"},
            )
        ).structured_content
        listed = (
            await client.call_tool(
                "list_reminders",
                {"person_id": person_id, "due_before": "2025-07-02T00:00:00+00:00"},
            )
        ).structured_content
        completed = (await client.call_tool("complete_reminder", {"reminder_id": follow_up["id"]})).structured_content
        completed_twice = (
            await client.call_tool("complete_reminder", {"reminder_id": follow_up["id"]})
        ).structured_content
        corrected = (
            await client.call_tool(
                "correct_record", {"entity_type": "fact", "entity_id": fact["id"], "fields": {"value": "Dubai"}}
            )
        ).structured_content
        rejected_identity = (
            await client.call_tool(
                "correct_record",
                {"entity_type": "fact", "entity_id": fact["id"], "fields": {"person_id": me["id"]}},
            )
        ).structured_content
        rejected_provenance = (
            await client.call_tool(
                "correct_record",
                {"entity_type": "fact", "entity_id": fact["id"], "fields": {"provenance": {"source": "x"}}},
            )
        ).structured_content
        context_default = (
            await client.call_tool("get_person_context", {"person_id": person_id, "max_items": 10})
        ).structured_content
        context_sensitive = (
            await client.call_tool("get_sensitive_person_context", {"person_id": person_id, "max_items": 10})
        ).structured_content
        guidance = (
            await client.call_tool(
                "get_communication_guidance", {"person_id": person_id, "situation": "Discuss launch"}
            )
        ).structured_content
        unknown = (
            await client.call_tool("record_fact", {"person_id": "missing", "predicate": "p", "value": "v"})
        ).structured_content
        return {
            "person_id": person_id,
            "fact": fact,
            "sensitive_fact": sensitive_fact,
            "observation": observation,
            "note": note,
            "listed": listed,
            "completed": completed,
            "completed_twice": completed_twice,
            "corrected": corrected,
            "rejected_identity": rejected_identity,
            "rejected_provenance": rejected_provenance,
            "context_default": context_default,
            "context_sensitive": context_sensitive,
            "guidance": guidance,
            "unknown": unknown,
            "philosophy": philosophy,
        }

    payload = _run(server, flow)

    assert payload["unknown"]["error"] == "person_not_found"
    assert payload["corrected"]["id"] == payload["fact"]["id"]
    assert payload["corrected"]["value"] == "Dubai"
    assert payload["rejected_identity"]["error"] == "invalid_correction"
    assert payload["rejected_provenance"]["error"] == "invalid_correction"
    assert [reminder["id"] for reminder in payload["listed"]["reminders"]] == [
        payload["completed"]["id"],
        payload["note"]["id"],
    ]
    assert payload["completed"]["status"] == "completed"
    assert payload["completed_twice"]["error"] == "reminder_not_active"
    context_default = payload["context_default"]
    assert context_default["facts"][0]["value"] == "Dubai"
    assert payload["sensitive_fact"]["id"] not in {fact["id"] for fact in context_default["facts"]}
    assert payload["sensitive_fact"]["id"] in {fact["id"] for fact in payload["context_sensitive"]["facts"]}
    assert context_default["observations"] == []
    assert context_default["relationships"][0]["relationship"]["type"] == "friend_of"
    assert context_default["affiliations"][0]["organization_name"] == "Acme"
    assert context_default["reminders"][0]["id"] == payload["note"]["id"]
    guidance = payload["guidance"]
    assert guidance["traits"]["communication_style"][0]["value"] == "Prefers concise writing"
    assert "topics_to_avoid" not in guidance["traits"]
    assert guidance["friction_notes"] == ["Discussed launch friction"]
    assert guidance["communication_philosophy"] == payload["philosophy"]
    assert guidance["philosophy_set"] is True
    assert guidance["situation"] == "Discuss launch"
    assert payload["observation"]["text"] not in str(guidance)

    conn = open_db(db_path)
    try:
        entries = SqliteAuditLog(conn).list_entries(limit=100)
    finally:
        conn.close()
    correction = next(entry for entry in entries if entry.op == "correct" and entry.entity_id == payload["fact"]["id"])
    assert correction.payload["before"]["value"] == "Dubia"
    assert correction.payload["after"]["value"] == "Dubai"
    philosophy_entry = next(entry for entry in entries if entry.entity_type == "preference")
    assert payload["philosophy"] not in str(philosophy_entry.payload)


def test_resolve_hints_are_validated_and_real_context_payload_is_returned(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    conn = open_db(db_path)
    repo = SqlitePeopleRepository(conn)
    person = Person(canonical_name="Alice", summary="Colleague")
    repo.save_person(person)
    with conn:
        conn.execute(
            """
            INSERT INTO facts (
                id, person_id, predicate, value, recorded_at, confidence, sensitivity, provenance_source
            ) VALUES ('fact-1', ?, 'location', 'Dubai', '2025-01-01T00:00:00+00:00', 1.0, 'public', 'test')
            """,
            (person.id,),
        )
    conn.close()
    server = build_server(db_path=db_path)

    async def flow(client: Client) -> tuple[Any, dict[str, Any]]:
        invalid = await client.call_tool("resolve_person", {"query": "Alice", "hints": {"unexpected": "x"}})
        context = await client.call_tool("get_person_context", {"person_id": person.id})
        return invalid, context.structured_content

    invalid_result, payload = _run(server, flow)

    assert invalid_result.is_error is True
    assert payload["found"] is True
    assert payload["identity"] == {
        "id": person.id,
        "canonical_name": "Alice",
        "aliases": [],
        "summary": "Colleague",
        "is_self": False,
    }
    assert [fact["id"] for fact in payload["facts"]] == ["fact-1"]
    assert payload["observations"] == []


def test_build_server_logs_to_stderr_not_stdout(tmp_path: Path, capsys: Any) -> None:
    db_path = tmp_path / "t.db"
    build_server(db_path=db_path)
    captured = capsys.readouterr()

    # Nothing must go to stdout — stdio transport carries the protocol there.
    assert captured.out == ""
    # The resolved DB path is logged to stderr at startup.
    assert str(db_path) in captured.err
