"""Explicit import extractor routing tests."""

from __future__ import annotations

import mailbox
from email.message import EmailMessage
from pathlib import Path

import pytest

from people_context.adapters.importers.email import ImportExtractionError
from people_context.adapters.importers.router import ImportExtractorRouter


def test_routes_email_content_to_email_extractor() -> None:
    content = "\n".join(
        [
            "From: Alice Example <alice@example.com>",
            "Date: Wed, 04 Mar 2026 09:06:00 +0400",
            "",
        ]
    )

    extracted = ImportExtractorRouter().extract("email", content=content, path=None, self_addresses=set())

    assert [person.email for person in extracted.people] == ["alice@example.com"]
    assert len(extracted.interactions) == 1


def test_routes_path_based_mbox_to_email_extractor(tmp_path: Path) -> None:
    mbox_path = tmp_path / "mailbox.mbox"
    box = mailbox.mbox(mbox_path)
    try:
        message = EmailMessage()
        message["From"] = "Alice Example <alice@example.com>"
        message["Date"] = "Wed, 04 Mar 2026 09:06:00 +0400"
        box.add(message)
        box.flush()
    finally:
        box.close()

    extracted = ImportExtractorRouter().extract("mbox", content=None, path=str(mbox_path), self_addresses=set())

    assert [person.email for person in extracted.people] == ["alice@example.com"]
    assert len(extracted.interactions) == 1


def test_mbox_rejects_content() -> None:
    with pytest.raises(ImportExtractionError) as error:
        ImportExtractorRouter().extract("mbox", content="mail", path=None, self_addresses=set())

    assert error.value.code == "invalid_source"
    assert str(error.value) == "mbox import requires path and does not accept content"


def test_routes_vcard_content_to_vcard_extractor() -> None:
    content = "\n".join(["BEGIN:VCARD", "VERSION:4.0", "FN:Alice Example", "END:VCARD"])

    extracted = ImportExtractorRouter().extract("vcard", content=content, path=None, self_addresses=set())

    assert extracted.candidates == [
        {
            "type": "person",
            "ref": "card-1",
            "name": "Alice Example",
            "aliases": [],
            "message_id": None,
            "date": None,
        }
    ]


def test_routes_ics_content_to_ics_extractor() -> None:
    content = "\n".join(
        [
            "BEGIN:VCALENDAR",
            "BEGIN:VEVENT",
            "DTSTART:20260304T090600Z",
            "ATTENDEE;CN=Alice Example:mailto:alice@example.com",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )

    extracted = ImportExtractorRouter().extract("ics", content=content, path=None, self_addresses=set())

    assert [candidate["ref"] for candidate in extracted.candidates if candidate["type"] == "person"] == [
        "alice@example.com"
    ]
    interactions = [candidate for candidate in extracted.candidates if candidate["type"] == "interaction"]
    assert len(interactions) == 1
    assert interactions[0]["summary"] == "Calendar event"


def test_routes_linkedin_content_to_linkedin_extractor() -> None:
    content = "\n".join(
        [
            "First Name,Last Name,URL,Email Address,Company,Position,Connected On",
            "Alice,Example,url,alice@example.com,,,,",
        ]
    )

    extracted = ImportExtractorRouter().extract("linkedin", content=content, path=None, self_addresses=set())

    assert [candidate["name"] for candidate in extracted.candidates if candidate["type"] == "person"] == [
        "Alice Example"
    ]


def test_routes_outlook_content_to_outlook_extractor() -> None:
    content = "\n".join(
        [
            "First Name,Middle Name,Last Name,E-mail Address,Company,Job Title,Birthday",
            "Alice,,Example,alice@example.com,,,",
        ]
    )

    extracted = ImportExtractorRouter().extract("outlook", content=content, path=None, self_addresses=set())

    assert [candidate["name"] for candidate in extracted.candidates if candidate["type"] == "person"] == [
        "Alice Example"
    ]


def test_routes_whatsapp_content_to_whatsapp_extractor() -> None:
    content = "\n".join(
        [
            "[13/02/2025, 10:12:45] Alice Example: hello",
            "[14/02/2025, 10:12:45] Alice Example: hello again",
        ]
    )

    extracted = ImportExtractorRouter().extract("whatsapp", content=content, path=None, self_addresses=set())

    assert [candidate["name"] for candidate in extracted.candidates if candidate["type"] == "person"] == [
        "Alice Example"
    ]
    interactions = [candidate for candidate in extracted.candidates if candidate["type"] == "interaction"]
    assert [interaction["summary"] for interaction in interactions] == ["WhatsApp chat", "WhatsApp chat"]


def test_router_forwards_self_names_and_sender_hint_to_the_selected_extractor() -> None:
    content = "\n".join(
        [
            "[13/02/2025, 10:12:45] Sam Self: hello",
            "[13/02/2025, 10:13:00] You: hello",
            "[13/02/2025, 10:14:00] Alice Example: hello",
        ]
    )

    extracted = ImportExtractorRouter().extract(
        "whatsapp",
        content=content,
        path=None,
        self_addresses=set(),
        self_names={"sam self"},
        self_sender="You",
    )

    assert [candidate["name"] for candidate in extracted.candidates if candidate["type"] == "person"] == [
        "Alice Example"
    ]


def test_existing_sources_accept_and_ignore_self_name_parameters() -> None:
    content = "\n".join(["BEGIN:VCARD", "VERSION:4.0", "FN:Alice Example", "END:VCARD"])

    extracted = ImportExtractorRouter().extract(
        "vcard",
        content=content,
        path=None,
        self_addresses=set(),
        self_names={"alice example"},
        self_sender="Alice Example",
    )

    assert [candidate["name"] for candidate in extracted.candidates if candidate["type"] == "person"] == [
        "Alice Example"
    ]


def test_unknown_source_type_reports_supported_values() -> None:
    with pytest.raises(ImportExtractionError) as error:
        ImportExtractorRouter().extract("unknown", content="value", path=None, self_addresses=set())

    assert error.value.code == "invalid_source_type"
    assert str(error.value) == (
        "source_type must be 'email', 'mbox', 'vcard', 'ics', 'linkedin', 'outlook', or 'whatsapp'"
    )
