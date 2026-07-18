"""Header-only stdlib email and mbox candidate extraction."""

from __future__ import annotations

import mailbox
from collections.abc import Iterable
from datetime import UTC, datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from people_context.domain.shared import normalize_name
from people_context.ports.imports import (
    ExtractedImport,
    ImportInteractionCandidate,
    ImportPersonCandidate,
)

_ADDRESS_HEADERS = ("From", "To", "Cc", "Reply-To")


class ImportExtractionError(Exception):
    """Raised when import source parameters or headers are invalid."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class EmailImportExtractor:
    """Extract correspondents and dated interaction summaries without bodies."""

    def extract(
        self,
        source_type: str,
        *,
        content: str | None,
        path: str | None,
        self_addresses: set[str],
    ) -> ExtractedImport:
        messages = self._messages(source_type, content, path)
        people: dict[str, ImportPersonCandidate] = {}
        alternate_names: dict[str, list[str]] = {}
        interactions: list[ImportInteractionCandidate] = []
        skipped_message_ids: list[str] = []
        skipped_without_id = 0
        normalized_self = {normalize_name(address) for address in self_addresses}
        for message in messages:
            correspondents = self._correspondents(message, normalized_self)
            occurred_at = _message_date(message)
            message_id = _clean_header(message.get("Message-ID"))
            for name, address in correspondents:
                if address not in people:
                    people[address] = ImportPersonCandidate(
                        name=name,
                        email=address,
                        message_id=message_id,
                        date=occurred_at,
                    )
                    alternate_names[address] = []
                elif normalize_name(name) != normalize_name(people[address].name):
                    known = {normalize_name(value) for value in alternate_names[address]}
                    if normalize_name(name) not in known:
                        alternate_names[address].append(name)
            if occurred_at is not None and correspondents:
                interactions.append(
                    ImportInteractionCandidate(
                        participant_emails=list(dict.fromkeys(address for _, address in correspondents)),
                        occurred_at=occurred_at,
                        message_id=message_id,
                    )
                )
            elif occurred_at is None and correspondents and message_id is not None:
                skipped_message_ids.append(message_id)
            elif occurred_at is None and correspondents:
                skipped_without_id += 1
        candidates = [
            ImportPersonCandidate(
                name=candidate.name,
                email=candidate.email,
                alternate_names=alternate_names[address],
                message_id=candidate.message_id,
                date=candidate.date,
            )
            for address, candidate in people.items()
        ]
        return ExtractedImport(
            people=candidates,
            interactions=interactions,
            skipped_message_ids=skipped_message_ids,
            skipped_without_id=skipped_without_id,
        )

    def _messages(self, source_type: str, content: str | None, path: str | None) -> Iterable[Message]:
        parser = BytesParser(policy=policy.default)
        if source_type == "email":
            if (content is None) == (path is None):
                raise ImportExtractionError("invalid_source", "email import requires exactly one of content or path")
            raw = content.encode("utf-8") if content is not None else Path(path or "").read_bytes()
            return [parser.parsebytes(_header_bytes(raw), headersonly=True)]
        if source_type == "mbox":
            if path is None or content is not None:
                raise ImportExtractionError("invalid_source", "mbox import requires path and does not accept content")

            def header_factory(file_obj) -> mailbox.mboxMessage:
                lines: list[bytes] = []
                while True:
                    line = file_obj.readline()
                    if not line:
                        break
                    lines.append(line)
                    if line in (b"\n", b"\r\n"):
                        break
                return mailbox.mboxMessage(parser.parsebytes(b"".join(lines), headersonly=True))

            mbox = mailbox.mbox(path, factory=header_factory, create=False)
            try:
                return list(mbox)
            finally:
                mbox.close()
        raise ImportExtractionError("invalid_source_type", "source_type must be 'email' or 'mbox'")

    @staticmethod
    def _correspondents(message: Message, self_addresses: set[str]) -> list[tuple[str, str]]:
        correspondents: list[tuple[str, str]] = []
        for header in _ADDRESS_HEADERS:
            for display_name, address in getaddresses(message.get_all(header, [])):
                normalized_address = normalize_name(address.strip())
                if not normalized_address or "@" not in normalized_address or normalized_address in self_addresses:
                    continue
                name = _normalize_text(display_name) or normalized_address.split("@", maxsplit=1)[0]
                correspondents.append((name, normalized_address))
        return correspondents


def _message_date(message: Message) -> datetime | None:
    value = message.get("Date")
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _clean_header(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = _normalize_text(str(value))
    return normalized or None


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def _header_bytes(raw: bytes) -> bytes:
    """Return only the RFC header block, including its terminating blank line."""
    for separator in (b"\r\n\r\n", b"\n\n"):
        header, found, _ = raw.partition(separator)
        if found:
            return header + separator
    return raw
