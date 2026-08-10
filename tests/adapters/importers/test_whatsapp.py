"""WhatsApp chat-export extraction, self exclusion, staging, and commit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from people_context.adapters.importers.email import ImportExtractionError
from people_context.adapters.importers.router import ImportExtractorRouter
from people_context.adapters.importers.whatsapp import WhatsAppImportExtractor
from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteContextReader,
    SqliteImportStagingStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.app.imports import CommitImport, ImportContent, ImportPipelineError, ReviewImport
from people_context.app.people import RememberPerson, RememberPersonInput
from people_context.app.people.remember import AliasInput
from people_context.app.records import RecordFact, RecordInteraction, SetAffiliation
from people_context.domain.person import AliasKind
from people_context.ports.imports import ExtractedImport

_BODY_SENTINEL = "WHATSAPP-BODY-MUST-NOT-LEAK-6c41"
_NOW = datetime(2026, 8, 10, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _RecordingExtractor:
    """Capture the self-resolution inputs the application forwards to an extractor."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def extract(
        self,
        source_type: str,
        *,
        content: str | None,
        path: str | None,
        self_addresses: set[str],
        self_names: set[str] | None = None,
        self_sender: str | None = None,
    ) -> ExtractedImport:
        self.calls.append(
            {
                "source_type": source_type,
                "self_addresses": self_addresses,
                "self_names": self_names,
                "self_sender": self_sender,
            }
        )
        return ExtractedImport(
            people=[],
            interactions=[],
            candidates=[
                {
                    "type": "person",
                    "ref": "whatsapp-person-1",
                    "name": "Alice Example",
                    "aliases": [],
                    "message_id": None,
                    "date": None,
                }
            ],
        )


def _use_cases(conn, extractor: Any | None = None):
    people = SqlitePeopleRepository(conn)
    records = SqliteRecordStore(conn)
    audit = SqliteAuditLog(conn)
    staging = SqliteImportStagingStore(conn)
    return (
        ImportContent(people, extractor or ImportExtractorRouter(), staging, _Clock()),
        ReviewImport(staging),
        CommitImport(
            people,
            staging,
            RememberPerson(people, people, audit, _Clock()),
            RecordInteraction(people, records, audit, _Clock()),
            SetAffiliation(people, SqliteOrganizationStore(conn), records, audit, _Clock()),
            RecordFact(people, records, audit, _Clock()),
        ),
    )


def _extract(content: str, **kwargs: Any) -> ExtractedImport:
    defaults: dict[str, Any] = {"self_addresses": set()}
    defaults.update(kwargs)
    return WhatsAppImportExtractor().extract("whatsapp", content=content, path=None, **defaults)


def _people(extracted: ExtractedImport) -> list[dict[str, Any]]:
    return [candidate for candidate in extracted.candidates if candidate["type"] == "person"]


def _interactions(extracted: ExtractedImport) -> list[dict[str, Any]]:
    return [candidate for candidate in extracted.candidates if candidate["type"] == "interaction"]


def test_whatsapp_bracketed_day_first_export_keeps_only_senders_and_days() -> None:
    content = "\n".join(
        [
            "\u200e[13/02/2025, 10:12:45] Alice Example: " + _BODY_SENTINEL,
            "a wrapped body line carrying " + _BODY_SENTINEL,
            "[13/02/2025, 10:13:00] Bob Builder: " + _BODY_SENTINEL,
            "[14/02/2025, 09:00:00] Alice Example: " + _BODY_SENTINEL,
        ]
    )

    extracted = _extract(content)

    assert [person["name"] for person in _people(extracted)] == ["Alice Example", "Bob Builder"]
    assert [person["aliases"] for person in _people(extracted)] == [[], []]
    assert [
        (interaction["date"], interaction["participant_refs"], interaction["summary"], interaction["channel"])
        for interaction in _interactions(extracted)
    ] == [
        (
            datetime(2025, 2, 13, tzinfo=UTC),
            ["whatsapp-person-1", "whatsapp-person-2"],
            "WhatsApp chat",
            "whatsapp",
        ),
        (datetime(2025, 2, 14, tzinfo=UTC), ["whatsapp-person-1"], "WhatsApp chat", "whatsapp"),
    ]
    assert extracted.skipped_cards == []
    assert _BODY_SENTINEL not in repr(extracted)


def test_whatsapp_supports_dash_form_twelve_hour_month_first_exports() -> None:
    content = "\n".join(
        [
            "2/13/25, 10:12 AM - Alice Example: " + _BODY_SENTINEL,
            "2/14/25, 10:12:45 pm - Bob Builder: " + _BODY_SENTINEL,
        ]
    )

    extracted = _extract(content)

    assert [person["name"] for person in _people(extracted)] == ["Alice Example", "Bob Builder"]
    assert [interaction["date"] for interaction in _interactions(extracted)] == [
        datetime(2025, 2, 13, tzinfo=UTC),
        datetime(2025, 2, 14, tzinfo=UTC),
    ]
    assert extracted.skipped_cards == []
    assert _BODY_SENTINEL not in repr(extracted)


def test_whatsapp_supports_dotted_day_first_and_iso_dated_exports() -> None:
    dotted = "\n".join(
        [
            "15.02.2025, 11:30 - Alice Example: " + _BODY_SENTINEL,
            "16.02.2025, 11:31 - Alice Example: " + _BODY_SENTINEL,
        ]
    )
    iso = "\n".join(
        [
            "[2025-02-15, 11:30:00] Alice Example: " + _BODY_SENTINEL,
            "2025-02-16, 11:31 - Alice Example: " + _BODY_SENTINEL,
        ]
    )

    for content in (dotted, iso):
        extracted = _extract(content)

        assert [person["name"] for person in _people(extracted)] == ["Alice Example"]
        assert [interaction["date"] for interaction in _interactions(extracted)] == [
            datetime(2025, 2, 15, tzinfo=UTC),
            datetime(2025, 2, 16, tzinfo=UTC),
        ]
        assert extracted.skipped_cards == []
        assert _BODY_SENTINEL not in repr(extracted)


def test_whatsapp_skips_numeric_dates_whose_component_order_is_unresolved() -> None:
    undetermined = "\n".join(
        [
            "[02/03/2025, 10:12:45] Alice Example: " + _BODY_SENTINEL,
            "[04/05/2025, 10:13:00] Alice Example: " + _BODY_SENTINEL,
        ]
    )
    conflicting = "\n".join(
        [
            "[13/02/2025, 10:12:45] Alice Example: " + _BODY_SENTINEL,
            "[02/13/2025, 10:13:00] Alice Example: " + _BODY_SENTINEL,
        ]
    )

    for content in (undetermined, conflicting):
        extracted = _extract(content)

        assert _interactions(extracted) == []
        assert extracted.skipped_cards == [
            {"index": 1, "reason": "ambiguous_date_order"},
            {"index": 2, "reason": "ambiguous_date_order"},
        ]
        assert _BODY_SENTINEL not in repr(extracted)


def test_whatsapp_reports_impossible_timestamps_and_unusable_sender_labels() -> None:
    content = "\n".join(
        [
            "[31/02/2025, 10:12:45] Alice Example: " + _BODY_SENTINEL,
            "[13/13/2025, 10:12:45] Alice Example: " + _BODY_SENTINEL,
            "[13/02/2025, 10:13:00] Messages and calls are end-to-end encrypted.",
            f"[13/02/2025, 10:14:00] {'N' * 81}: " + _BODY_SENTINEL,
            "[13/02/2025, 10:15:00] Alice Example: " + _BODY_SENTINEL,
        ]
    )

    extracted = _extract(content)

    assert extracted.skipped_cards == [
        {"index": 1, "reason": "invalid_timestamp"},
        {"index": 2, "reason": "invalid_timestamp"},
        {"index": 3, "reason": "no_sender"},
        {"index": 4, "reason": "invalid_sender"},
    ]
    assert [person["name"] for person in _people(extracted)] == ["Alice Example"]
    assert [interaction["participant_refs"] for interaction in _interactions(extracted)] == [
        ["whatsapp-person-1"]
    ]


def test_whatsapp_impossible_components_do_not_bias_the_file_wide_date_order() -> None:
    content = "\n".join(
        [
            "[40/02/2025, 10:12:45] Alice Example: " + _BODY_SENTINEL,
            "[02/13/2025, 10:13:00] Alice Example: " + _BODY_SENTINEL,
            "[03/14/2025, 10:14:00] Bob Builder: " + _BODY_SENTINEL,
        ]
    )

    extracted = _extract(content)

    assert extracted.skipped_cards == [{"index": 1, "reason": "invalid_timestamp"}]
    assert [interaction["date"] for interaction in _interactions(extracted)] == [
        datetime(2025, 2, 13, tzinfo=UTC),
        datetime(2025, 3, 14, tzinfo=UTC),
    ]


def test_whatsapp_deduplicates_phone_senders_and_stages_a_compact_handle() -> None:
    content = "\n".join(
        [
            "[13/02/2025, 10:12:45] +1 555 123 4567: " + _BODY_SENTINEL,
            "[13/02/2025, 10:13:00] +1 (555) 123-4567: " + _BODY_SENTINEL,
            "[13/02/2025, 10:14:00] +15551234567: " + _BODY_SENTINEL,
        ]
    )

    extracted = _extract(content)

    assert _people(extracted) == [
        {
            "type": "person",
            "ref": "whatsapp-person-1",
            "name": "+1 555 123 4567",
            "aliases": [
                {"value": "+15551234567", "kind": AliasKind.HANDLE.value},
                {"value": "+1 (555) 123-4567", "kind": AliasKind.OTHER.value},
            ],
            "message_id": None,
            "date": None,
        }
    ]
    assert [interaction["participant_refs"] for interaction in _interactions(extracted)] == [
        ["whatsapp-person-1"]
    ]


def test_whatsapp_omits_self_by_alias_name_and_by_explicit_sender_hint() -> None:
    content = "\n".join(
        [
            "[13/02/2025, 10:12:45] Sam Self: " + _BODY_SENTINEL,
            "[13/02/2025, 10:13:00] You: " + _BODY_SENTINEL,
            "[13/02/2025, 10:14:00] Alice Example: " + _BODY_SENTINEL,
            "[14/02/2025, 09:00:00] You: " + _BODY_SENTINEL,
            "[14/02/2025, 09:01:00] Sam Self: " + _BODY_SENTINEL,
        ]
    )

    extracted = _extract(content, self_names={"sam self"}, self_sender="You")

    assert [person["name"] for person in _people(extracted)] == ["Alice Example"]
    assert [
        (interaction["date"], interaction["participant_refs"]) for interaction in _interactions(extracted)
    ] == [(datetime(2025, 2, 13, tzinfo=UTC), ["whatsapp-person-1"])]
    assert extracted.skipped_cards == []


def test_whatsapp_matches_a_self_phone_hint_written_in_another_format() -> None:
    content = "\n".join(
        [
            "[13/02/2025, 10:12:45] +1 555 123 4567: " + _BODY_SENTINEL,
            "[13/02/2025, 10:13:00] Alice Example: " + _BODY_SENTINEL,
        ]
    )

    extracted = _extract(content, self_sender="+1 (555) 123-4567")

    assert [person["name"] for person in _people(extracted)] == ["Alice Example"]


def test_whatsapp_rejects_wrong_source_type_and_ambiguous_source() -> None:
    with pytest.raises(ImportExtractionError) as wrong_source:
        WhatsAppImportExtractor().extract("linkedin", content="x", path=None, self_addresses=set())
    assert wrong_source.value.code == "invalid_source_type"

    with pytest.raises(ImportExtractionError) as ambiguous_source:
        WhatsAppImportExtractor().extract("whatsapp", content="x", path="chat.txt", self_addresses=set())
    assert ambiguous_source.value.code == "invalid_source"


def test_whatsapp_reads_a_file_path_through_the_router(tmp_path) -> None:
    chat_path = tmp_path / "chat.txt"
    chat_path.write_text(
        "[13/02/2025, 10:12:45] Alice Example: " + _BODY_SENTINEL + "\n",
        encoding="utf-8",
    )

    extracted = ImportExtractorRouter().extract(
        "whatsapp",
        content=None,
        path=str(chat_path),
        self_addresses=set(),
    )

    assert [candidate["name"] for candidate in _people(extracted)] == ["Alice Example"]
    assert _BODY_SENTINEL not in repr(extracted)


def test_import_content_forwards_normalized_self_names_and_the_sender_hint() -> None:
    with open_db(":memory:") as conn:
        people = SqlitePeopleRepository(conn)
        audit = SqliteAuditLog(conn)
        RememberPerson(people, people, audit, _Clock()).execute(
            RememberPersonInput(
                name="Sam Self",
                aliases=[
                    AliasInput(value="sam@example.com", kind=AliasKind.HANDLE),
                    AliasInput(value="Sammy", kind=AliasKind.NICKNAME),
                ],
                is_self=True,
            )
        )
        extractor = _RecordingExtractor()
        import_content, _, _ = _use_cases(conn, extractor)

        import_content.execute("whatsapp", content="chat", self_sender="You")

        assert extractor.calls == [
            {
                "source_type": "whatsapp",
                "self_addresses": {"sam@example.com"},
                "self_names": {"sam self", "sam@example.com", "sammy"},
                "self_sender": "You",
            }
        ]


def test_whatsapp_self_only_export_reports_no_candidates() -> None:
    with open_db(":memory:") as conn:
        import_content, _, _ = _use_cases(conn)
        content = "[13/02/2025, 10:12:45] You: " + _BODY_SENTINEL

        with pytest.raises(ImportPipelineError) as error:
            import_content.execute("whatsapp", content=content, self_sender="You")

        assert error.value.code == "no_candidates"
        assert error.value.details["skipped_cards"] == []


def test_whatsapp_import_stages_reviews_and_commits_without_body_text() -> None:
    with open_db(":memory:") as conn:
        import_content, review_import, commit_import = _use_cases(conn)
        content = "\n".join(
            [
                "[13/02/2025, 10:12:45] Alice Example: " + _BODY_SENTINEL,
                "[13/02/2025, 10:13:00] You: " + _BODY_SENTINEL,
                "[14/02/2025, 09:00:00] Alice Example: " + _BODY_SENTINEL,
            ]
        )

        batch = import_content.execute("whatsapp", content=content, self_sender="You")
        reviewed = review_import.execute(batch.batch_id)
        committed = commit_import.execute(batch.batch_id, [row.id for row in reviewed.candidates])

        assert batch.candidate_count == 3
        assert {row.source for row in reviewed.candidates} == {"import/whatsapp"}
        assert len(committed.committed_ids) == 3
        assert committed.unresolved_ids == []
        assert _BODY_SENTINEL not in repr(reviewed)

        matches = SqlitePeopleRepository(conn).find_by_normalized_name("alice example")
        assert [person.canonical_name for person in matches] == ["Alice Example"]
        interactions = SqliteContextReader(conn).list_interactions(matches[0].id)
        assert [interaction.summary for interaction in interactions] == ["WhatsApp chat", "WhatsApp chat"]
        assert _BODY_SENTINEL not in repr(interactions)
