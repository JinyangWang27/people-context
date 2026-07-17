"""Portable export integration tests."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteContextReader,
    SqliteExportReader,
    SqliteLifecycleStore,
    SqlitePeopleRepository,
    SqlitePreferencesStore,
    SqliteRecordStore,
    open_db,
)
from people_context.app import ExportData
from people_context.cli import CliContext, _cmd_export
from people_context.domain.interaction import Interaction
from people_context.domain.person import Alias, Person
from people_context.domain.shared import Provenance
from people_context.ports.audit_log import AuditEntry

_NOW = datetime(2026, 2, 3, 4, 5, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def test_empty_export_is_json_round_trippable_and_excludes_internal_tables() -> None:
    conn = open_db(":memory:")

    payload = ExportData(SqliteExportReader(conn), _Clock()).execute().model_dump(mode="json")

    assert json.loads(json.dumps(payload)) == payload
    assert payload["format"] == "people-context-export"
    assert payload["version"] == 1
    assert payload["people"] == []
    assert "person_search" not in payload
    assert "import_staging" not in payload


def test_export_contains_soft_deleted_people_preferences_interactions_and_decoded_audit() -> None:
    conn = open_db(":memory:")
    people = SqlitePeopleRepository(conn)
    person = Person(canonical_name="小明", aliases=[Alias(value="Ming")], deleted_at=_NOW)
    participant = Person(canonical_name="Alice")
    people.save_person(person)
    people.save_person(participant)
    interaction = Interaction(
        summary="Met",
        occurred_at=_NOW,
        participant_ids=[participant.id],
        provenance=Provenance(source="test"),
    )
    SqliteRecordStore(conn).save_interaction(interaction)
    SqlitePreferencesStore(conn).set("communication_philosophy", "上善若水")
    SqliteAuditLog(conn).append(
        AuditEntry(
            ts=_NOW,
            op="forget",
            entity_type="person",
            entity_id="forgotten",
            payload={"redacted": True},
            source="agent",
        )
    )

    payload = ExportData(SqliteExportReader(conn), _Clock()).execute().model_dump(mode="json")

    assert next(item for item in payload["people"] if item["id"] == person.id)["deleted_at"] == "2026-02-03T04:05:00Z"
    assert payload["interactions"][0]["participant_ids"] == [participant.id]
    assert payload["user_preferences"][0]["value"] == "上善若水"
    assert payload["audit_log"][0]["payload"] == {"redacted": True}


def test_cli_and_use_case_emit_byte_equivalent_payload_with_fixed_clock(capsys) -> None:
    conn = open_db(":memory:")
    export_reader = SqliteExportReader(conn)
    ctx = CliContext(
        conn=conn,
        repo=SqlitePeopleRepository(conn),
        context_reader=SqliteContextReader(conn),
        clock=_Clock(),
        export_reader=export_reader,
        audit=SqliteAuditLog(conn),
        lifecycle=SqliteLifecycleStore(conn),
        preferences=SqlitePreferencesStore(conn),
    )
    expected = json.dumps(
        ExportData(export_reader, _Clock()).execute().model_dump(mode="json"),
        indent=2,
        ensure_ascii=False,
    ) + "\n"

    assert _cmd_export(ctx, argparse.Namespace(output=None)) == 0

    assert capsys.readouterr().out == expected
