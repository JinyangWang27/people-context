"""Explicit import extractor routing tests."""

from __future__ import annotations

import mailbox
from email.message import EmailMessage
from pathlib import Path

import pytest

from people_context.adapters.email_import import ImportExtractionError
from people_context.adapters.import_router import ImportExtractorRouter


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


def test_unknown_source_type_reports_supported_values() -> None:
    with pytest.raises(ImportExtractionError) as error:
        ImportExtractorRouter().extract("unknown", content="value", path=None, self_addresses=set())

    assert error.value.code == "invalid_source_type"
    assert str(error.value) == "source_type must be 'email', 'mbox', or 'vcard'"
