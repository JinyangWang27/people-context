"""Multi-device bootstrap sign-off through the shipped entry points.

The adapter, application, and CLI suites already cover restore semantics in process. This
module proves the complete hand-off works the way an owner performs it: writes arrive through
a real ``people-context-mcp`` stdio subprocess, and vocabulary curation, ``sync push``,
``sync pull``, and inspection all run through the installed ``pctx`` CLI.

Device A seeds and pushes. Device B restores that bundle and then writes locally. Device C
restores B's bundle, which is where the historical-device chain is proven: A's retirement must
be carried forward verbatim rather than re-minted at the second hop.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from people_context.adapters.sqlite import open_db

_PROJECT_ROOT = Path(__file__).parents[2]

_DEVICES_SQL = """SELECT id, display_name, created_at, retired_at, hlc_physical_ms, hlc_logical
                  FROM devices ORDER BY created_at, id"""
# Ordered by the replication comparison key so a snapshot can be compared position by position.
_CHANGELOG_SQL = """SELECT op_id, device_id, hlc_physical_ms, hlc_logical, entity_type, entity_id, op_kind
                    FROM changelog ORDER BY hlc_physical_ms, hlc_logical, device_id, op_id"""
_TYPES_SQL = "SELECT type, inverse, symmetric, category, canonical FROM relationship_types ORDER BY type"
_SYNONYMS_SQL = "SELECT synonym, type FROM relationship_type_synonyms ORDER BY synonym"

_CUSTOM_TYPE = "climbing_partner_of"
_CUSTOM_SYNONYM = "belay partner"


@dataclass(frozen=True)
class _Chain:
    """Everything the A -> B -> C walk observed, captured at the moment it mattered."""

    device_a: Path
    device_b: Path
    device_c: Path
    self_id: str
    alice_id: str
    bob_id: str
    export_a: dict[str, Any]
    export_b_restored: dict[str, Any]
    export_b_final: dict[str, Any]
    export_c: dict[str, Any]
    changelog_a: list[dict[str, Any]]
    changelog_b_restored: list[dict[str, Any]]
    changelog_b_final: list[dict[str, Any]]
    changelog_c: list[dict[str, Any]]
    devices_a: list[dict[str, Any]]
    devices_b: list[dict[str, Any]]
    devices_c: list[dict[str, Any]]
    vocabulary_a: tuple[list[dict[str, Any]], list[dict[str, Any]]]
    vocabulary_b: tuple[list[dict[str, Any]], list[dict[str, Any]]]
    vocabulary_c: tuple[list[dict[str, Any]], list[dict[str, Any]]]
    pull_b_output: str
    pull_c_output: str


@pytest.fixture(scope="module")
def chain(tmp_path_factory: pytest.TempPathFactory) -> _Chain:
    """Walk A -> B -> C once through real subprocesses and record each observation."""
    root = tmp_path_factory.mktemp("m11-multi-device")
    device_a = root / "device-a.db"
    device_b = root / "device-b.db"
    device_c = root / "device-c.db"
    outbox_a = root / "outbox-a"
    outbox_b = root / "outbox-b"

    _cli(
        device_a,
        "relationship-types",
        "add",
        _CUSTOM_TYPE,
        "--category",
        "social",
        "--symmetric",
        "--synonym",
        _CUSTOM_SYNONYM,
    )
    seeded: dict[str, str] = anyio.run(_seed_device_a, device_a)
    _cli(device_a, "sync", "push", "--output", str(outbox_a))
    pull_b_output = _cli(device_b, "sync", "pull", "--input", str(outbox_a), "--yes")

    export_a = _export(device_a)
    export_b_restored = _export(device_b)
    changelog_a = _rows(device_a, _CHANGELOG_SQL)
    changelog_b_restored = _rows(device_b, _CHANGELOG_SQL)
    devices_a = _rows(device_a, _DEVICES_SQL)
    devices_b = _rows(device_b, _DEVICES_SQL)
    vocabulary_a = _vocabulary(device_a)
    vocabulary_b = _vocabulary(device_b)

    bob_id: str = anyio.run(_write_on_device_b, device_b)
    export_b_final = _export(device_b)
    changelog_b_final = _rows(device_b, _CHANGELOG_SQL)

    _cli(device_b, "sync", "push", "--output", str(outbox_b))
    pull_c_output = _cli(device_c, "sync", "pull", "--input", str(outbox_b), "--yes")

    return _Chain(
        device_a=device_a,
        device_b=device_b,
        device_c=device_c,
        self_id=seeded["self"],
        alice_id=seeded["alice"],
        bob_id=bob_id,
        export_a=export_a,
        export_b_restored=export_b_restored,
        export_b_final=export_b_final,
        export_c=_export(device_c),
        changelog_a=changelog_a,
        changelog_b_restored=changelog_b_restored,
        changelog_b_final=changelog_b_final,
        changelog_c=_rows(device_c, _CHANGELOG_SQL),
        devices_a=devices_a,
        devices_b=devices_b,
        devices_c=_rows(device_c, _DEVICES_SQL),
        vocabulary_a=vocabulary_a,
        vocabulary_b=vocabulary_b,
        vocabulary_c=_vocabulary(device_c),
        pull_b_output=pull_b_output,
        pull_c_output=pull_c_output,
    )


# -- A -> B ---------------------------------------------------------------


def test_device_b_restores_every_portable_row_device_a_exported(chain: _Chain) -> None:
    """The strongest content check: the two databases export the same portable document."""
    assert chain.export_b_restored == chain.export_a
    assert [row["canonical_name"] for row in chain.export_a["people"]] == ["Me", "Alice Example"]
    assert chain.export_a["facts"][0]["value"] == "Dubai"
    assert chain.export_a["interactions"][0]["summary"] == "Reviewed the launch plan"
    assert chain.export_a["reminders"][0]["text"] == "Send the belay schedule"
    assert chain.export_a["user_preferences"][0]["value"] == "Be direct and specific."


def test_device_b_restores_custom_relationship_vocabulary_and_its_canonical_edge(chain: _Chain) -> None:
    """A curated type and its synonym must travel, or the restored edge loses its meaning."""
    types_a, synonyms_a = chain.vocabulary_a
    assert {"type": _CUSTOM_TYPE, "inverse": None, "symmetric": 1, "category": "social", "canonical": 1} in types_a
    assert {"synonym": "belay_partner", "type": _CUSTOM_TYPE} in synonyms_a
    assert chain.vocabulary_b == chain.vocabulary_a
    # Device A resolved the synonym at write time, so both databases store the canonical type.
    assert [row["type"] for row in chain.export_b_restored["relationships"]] == [_CUSTOM_TYPE]


def test_restore_reinstates_history_without_minting_new_accountability_rows(chain: _Chain) -> None:
    """Restore is the documented `audit_mutation` exception; it must add no rows of its own."""
    assert chain.changelog_b_restored == chain.changelog_a
    assert chain.export_b_restored["audit_log"] == chain.export_a["audit_log"]
    assert {row["device_id"] for row in chain.changelog_b_restored} == {chain.devices_a[0]["id"]}


def test_device_b_keeps_its_own_active_identity_and_retires_device_a(chain: _Chain) -> None:
    assert [row["id"] for row in chain.devices_a] == [chain.devices_a[0]["id"]]
    assert chain.devices_a[0]["retired_at"] is None

    retired = [row for row in chain.devices_b if row["retired_at"] is not None]
    active = [row for row in chain.devices_b if row["retired_at"] is None]
    assert [row["id"] for row in retired] == [chain.devices_a[0]["id"]]
    assert len(active) == 1
    assert active[0]["id"] != chain.devices_a[0]["id"]


def test_a_later_write_on_device_b_uses_device_b_and_sorts_after_every_import(chain: _Chain) -> None:
    local_device_id = _active_device_id(chain.devices_b)
    appended = [row for row in chain.changelog_b_final if row not in chain.changelog_b_restored]

    assert [row["entity_id"] for row in appended] == [chain.bob_id]
    assert [row["device_id"] for row in appended] == [local_device_id]
    assert min(_key(row) for row in appended) > max(_key(row) for row in chain.changelog_b_restored)
    assert chain.changelog_b_final[-1] == appended[0]


# -- B -> C ---------------------------------------------------------------


def test_device_c_receives_the_content_device_b_held_including_its_local_write(chain: _Chain) -> None:
    assert chain.export_c == chain.export_b_final
    assert chain.changelog_c == chain.changelog_b_final
    assert chain.vocabulary_c == chain.vocabulary_a
    assert [row["canonical_name"] for row in chain.export_c["people"]] == ["Me", "Alice Example", "Bob Example"]


def test_device_c_carries_both_earlier_devices_forward_as_retired_history(chain: _Chain) -> None:
    device_a_id = chain.devices_a[0]["id"]
    device_b_id = _active_device_id(chain.devices_b)
    retired = {row["id"]: row for row in chain.devices_c if row["retired_at"] is not None}
    active = [row for row in chain.devices_c if row["retired_at"] is None]

    assert set(retired) == {device_a_id, device_b_id}
    assert len(active) == 1
    assert active[0]["id"] not in {device_a_id, device_b_id}
    # A was retired when B imported it; the second hop copies that instant instead of restamping it.
    retired_on_b = {row["id"]: row for row in chain.devices_b}[device_a_id]
    assert retired[device_a_id] == retired_on_b
    assert retired[device_b_id]["retired_at"] is not None


def test_every_hop_reports_imported_devices_as_retired_history(chain: _Chain) -> None:
    assert "Imported devices 1 (all retired)" in chain.pull_b_output
    assert "Imported devices 2 (all retired)" in chain.pull_c_output
    assert "this device keeps its own identity" in chain.pull_b_output
    assert "this device keeps its own identity" in chain.pull_c_output
    assert "Restored 2 people" in chain.pull_b_output
    assert "Restored 3 people" in chain.pull_c_output
    # Vocabulary is reconciled once per hop, never duplicated on top of the seeded rows.
    assert "new relationship types 1, new synonyms 1" in chain.pull_b_output
    assert "new relationship types 1, new synonyms 1" in chain.pull_c_output


def test_device_c_can_serve_the_restored_people_over_real_stdio(chain: _Chain) -> None:
    """The final proof that a bootstrapped device is a working device, not just matching rows."""
    resolved, context = anyio.run(_read_device_c, chain.device_c, chain.alice_id)

    assert resolved["candidates"][0]["person_id"] == chain.alice_id
    assert context["affiliations"][0]["organization_name"] == "Acme Corp"
    assert context["facts"][0]["value"] == "Dubai"
    assert context["relationships"][0]["relationship"]["type"] == _CUSTOM_TYPE


# -- helpers --------------------------------------------------------------


async def _seed_device_a(db_path: Path) -> dict[str, str]:
    """Write one broad, sensitivity-mixed dataset through the real stdio server."""
    async with (
        stdio_client(_stdio_parameters(db_path)) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as client,
    ):
        await client.initialize()
        me = (await client.call_tool("remember_person", {"name": "Me", "is_self": True})).structuredContent["person"]
        alice = (
            await client.call_tool(
                "remember_person",
                {
                    "name": "Alice Example",
                    "aliases": [{"value": "Ally", "kind": "nickname"}],
                    "summary": "Climbing partner and colleague",
                },
            )
        ).structuredContent["person"]
        # Asserted through the custom synonym, so the canonical type only survives with its vocabulary.
        await client.call_tool(
            "set_relationship",
            {"subject_id": me["id"], "object_id": alice["id"], "type": _CUSTOM_SYNONYM},
        )
        await client.call_tool("set_affiliation", {"person_id": alice["id"], "org": "Acme Corp", "role": "Engineer"})
        await client.call_tool(
            "record_fact",
            {"person_id": alice["id"], "predicate": "location", "value": "Dubai", "sensitivity": "public"},
        )
        await client.call_tool("record_observation", {"person_id": alice["id"], "text": "Subjective and private"})
        await client.call_tool(
            "record_trait",
            {"person_id": alice["id"], "category": "communication_style", "value": "Prefers written summaries"},
        )
        await client.call_tool(
            "record_interaction",
            {"summary": "Reviewed the launch plan", "participant_ids": [me["id"], alice["id"]]},
        )
        await client.call_tool(
            "set_reminder",
            {
                "person_id": alice["id"],
                "text": "Send the belay schedule",
                "kind": "follow_up",
                "due_at": "2026-09-01T09:00:00Z",
            },
        )
        await client.call_tool("set_communication_philosophy", {"text": "Be direct and specific."})
        return {"self": me["id"], "alice": alice["id"]}


async def _write_on_device_b(db_path: Path) -> str:
    """Make one ordinary local write on the restored device."""
    async with (
        stdio_client(_stdio_parameters(db_path)) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as client,
    ):
        await client.initialize()
        remembered = await client.call_tool("remember_person", {"name": "Bob Example"})
        return str(remembered.structuredContent["person"]["id"])


async def _read_device_c(db_path: Path, alice_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    async with (
        stdio_client(_stdio_parameters(db_path)) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as client,
    ):
        await client.initialize()
        resolved = await client.call_tool("resolve_person", {"query": "Ally"})
        context = await client.call_tool("get_person_context", {"person_id": alice_id, "max_items": 5})
        return resolved.structuredContent, context.structuredContent


def _stdio_parameters(db_path: Path) -> StdioServerParameters:
    uv = shutil.which("uv")
    assert uv is not None
    return StdioServerParameters(
        command=uv,
        args=["run", "people-context-mcp", "--db", str(db_path)],
        cwd=_PROJECT_ROOT,
    )


def _cli(db_path: Path, *args: str) -> str:
    uv = shutil.which("uv")
    assert uv is not None
    completed = subprocess.run(
        [uv, "run", "pctx", "--db", str(db_path), *args],
        cwd=_PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _export(db_path: Path) -> dict[str, Any]:
    """Return the portable export without its wall-clock export instant."""
    document: dict[str, Any] = json.loads(_cli(db_path, "export"))
    document.pop("exported_at")
    return document


def _rows(db_path: Path, sql: str) -> list[dict[str, Any]]:
    conn = open_db(db_path)
    try:
        return [dict(row) for row in conn.execute(sql)]
    finally:
        conn.close()


def _vocabulary(db_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return _rows(db_path, _TYPES_SQL), _rows(db_path, _SYNONYMS_SQL)


def _active_device_id(devices: list[dict[str, Any]]) -> str:
    active = [row["id"] for row in devices if row["retired_at"] is None]
    assert len(active) == 1
    return str(active[0])


def _key(row: dict[str, Any]) -> tuple[int, int, str, str]:
    """Mirror `ChangelogEntry.comparison_key` for rows read straight from SQLite."""
    return (row["hlc_physical_ms"], row["hlc_logical"], row["device_id"], row["op_id"])
