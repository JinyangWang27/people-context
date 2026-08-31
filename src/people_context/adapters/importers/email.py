"""Header-only stdlib email and mbox candidate extraction."""

from __future__ import annotations

import mailbox
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from typing import IO, Any

from people_context.adapters.importers.bounded_source import (
    CandidateBudget,
    MeteredSourceFile,
    ParserWorkBudget,
    SourceReadBudget,
    read_source_bytes,
    refuse_oversized_file,
)
from people_context.adapters.importers.errors import ImportExtractionError
from people_context.domain.shared import normalize_name
from people_context.ports.imports import (
    ExtractedImport,
    ImportInteractionCandidate,
    ImportPersonCandidate,
)

#: `ImportExtractionError` moved to `errors` so the bounded loader can raise it without a
#: cycle; it stays importable from here because every extractor and test already names it.
__all__ = ["EmailImportExtractor", "ImportExtractionError"]

_ADDRESS_HEADERS = ("From", "To", "Cc", "Reply-To")


class _BoundedMbox(mailbox.mbox):
    """An mbox whose table-of-contents scan and message reads are both metered.

    `mailbox` exposes no seam for supplying the file it reads, so the one it opened is
    replaced with a metered view of itself. That is the only way the budget can cover the
    scan, which happens inside `_generate_toc` before any message reaches the factory — and
    the scan, not the headers parsed afterwards, is where a growing mailbox would otherwise
    read without limit.
    """

    def __init__(self, path: str, factory: Any, budget: SourceReadBudget) -> None:
        super().__init__(path, factory=factory, create=False)
        # `mailbox` leaves the file it opened unannotated, so the swap is spelled out here
        # instead of inferred; the metered view delegates every other attribute to it, which
        # is what keeps `close`, `tell`, and `seek` behaving exactly as the base class expects.
        opened: IO[bytes] = self._file  # type: ignore[has-type]
        self._file = MeteredSourceFile(opened, budget)


class EmailImportExtractor:
    """Extract correspondents and dated interaction summaries without bodies."""

    def extract(
        self,
        source_type: str,
        *,
        content: str | None,
        path: str | None,
        self_addresses: set[str],
        self_names: set[str] | None = None,
        self_sender: str | None = None,
        content_bytes: bytes | None = None,
        max_source_bytes: int | None = None,
        max_candidates: int | None = None,
        max_retained_parse_records: int | None = None,
    ) -> ExtractedImport:
        """Extract correspondents; ``self_names`` and ``self_sender`` are unused by this source.

        Messages are consumed one at a time straight out of the mailbox, so the parsed records
        this source holds live are the message being read and the addresses its own headers
        expand into — not one `Message` per message in the file. A mailbox whose messages name no
        external correspondent therefore costs a constant whether it holds three messages or a
        million, and ``max_retained_parse_records`` is what makes that assertable rather than
        implied.
        """
        people: dict[str, ImportPersonCandidate] = {}
        alternate_names: dict[str, list[str]] = {}
        interactions: list[ImportInteractionCandidate] = []
        skipped_message_ids: list[str] = []
        skipped_without_id = 0
        normalized_self = {normalize_name(address) for address in self_addresses}
        budget = CandidateBudget(max_candidates)
        work = ParserWorkBudget(max_retained_parse_records)
        with self._open_messages(source_type, content, content_bytes, path, max_source_bytes) as messages:
            for message in messages:
                # Exactly one parsed message is live per turn of this loop; the previous one is
                # unreachable as soon as the name is rebound. Accounting it explicitly is what
                # puts the streamed shape on the budget seam, so a regression back to a
                # materialized mailbox fails a test rather than passing review unnoticed.
                work.account(1)
                correspondents = self._correspondents(message, normalized_self, work)
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
                        # One message's recipient headers can name as many correspondents as the
                        # source budget allows, so the ceiling applies inside this fan-out too
                        # rather than only once the message is finished.
                        budget.account(len(people))
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
                budget.account(len(people) + len(interactions))
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

    @contextmanager
    def _open_messages(
        self,
        source_type: str,
        content: str | None,
        content_bytes: bytes | None,
        path: str | None,
        max_source_bytes: int | None,
    ) -> Iterator[Iterator[Message]]:
        """Yield this source's messages lazily, for exactly as long as reading them is legal.

        A mailbox cannot be drained into a list and then read: `mailbox` hands out messages by
        seeking around the file it owns, so a lazily consumed mailbox is one that must stay open
        for the whole extraction loop. Ownership of the handle therefore belongs here, where a
        `finally` closes it exactly once on every path — a completed loop, a candidate ceiling
        reached halfway through, a decode failure, or any other refusal raised while the caller
        is mid-iteration.
        """
        parser = BytesParser(policy=policy.default)
        if source_type == "email":
            supplied = [value is not None for value in (content, content_bytes, path)]
            if sum(supplied) != 1:
                raise ImportExtractionError(
                    "invalid_source",
                    "email import requires exactly one of content, content_bytes, or path",
                )
            if content is not None:
                raw = content.encode("utf-8")
            elif content_bytes is not None:
                raw = content_bytes
            else:
                raw = read_source_bytes(path or "", max_bytes=max_source_bytes)
            yield iter([parser.parsebytes(_header_bytes(raw), headersonly=True)])
            return
        if source_type == "mbox":
            # `mbox` is the one path-only contract: `mailbox.mbox` opens the path itself, so
            # there is no in-memory snapshot to hand it. Stable-snapshot verification for this
            # source is the caller's pre/post rehash, not a byte snapshot taken here.
            if path is None or content is not None or content_bytes is not None:
                raise ImportExtractionError("invalid_source", "mbox import requires path and does not accept content")

            # `mailbox.mbox` opens the path itself and scans the whole file to build its
            # table of contents before yielding a message, so the budget is applied to the
            # file object it reads through. A reported size only saves the setup cost.
            refuse_oversized_file(path, max_bytes=max_source_bytes)

            # `mailbox.mbox` types this parameter with a private typeshed alias, so the
            # annotation stays deliberately loose rather than importing a non-public name.
            def header_factory(file_obj: Any) -> mailbox.mboxMessage:
                lines: list[bytes] = []
                while True:
                    line = file_obj.readline()
                    if not line:
                        break
                    lines.append(line)
                    if line in (b"\n", b"\r\n"):
                        break
                return mailbox.mboxMessage(parser.parsebytes(b"".join(lines), headersonly=True))

            mbox = _BoundedMbox(path, header_factory, SourceReadBudget(max_source_bytes))
            try:
                # `mailbox`'s own iteration parses one message per step, and its whole-file scan
                # still runs first — under the metered file, so an oversized mailbox is refused
                # for its size before a single message is parsed, exactly as before.
                yield iter(mbox)
            finally:
                mbox.close()
            return
        raise ImportExtractionError("invalid_source_type", "source_type must be 'email' or 'mbox'")

    @staticmethod
    def _correspondents(
        message: Message,
        self_addresses: set[str],
        work: ParserWorkBudget,
    ) -> list[tuple[str, str]]:
        """Return one message's external correspondents, metering each header before parsing it.

        `getaddresses` has no incremental form: it returns one header's complete address list,
        so accounting only the correspondents that survive filtering would meter the expansion
        after the allocation worth bounding had already happened. It does publish an upper bound
        on what it can return, though — in its strict default mode the result is either one
        tuple per comma-delimited element or the single empty tuple it substitutes for a
        malformed header — and that bound is a comma count, one pass over a string the parsed
        message already holds. Charging it before the call is what puts the allocation itself
        under the budget rather than the list built from it.

        What is deliberately not done is parsing a header's values separately. Joining them is
        what decides how a quoted display name folded across two header lines is read, and how
        many addresses the strict count then expects, so splitting them would change what this
        source extracts; extraction output is frozen for this milestone.
        """
        correspondents: list[tuple[str, str]] = []
        for header in _ADDRESS_HEADERS:
            values = message.get_all(header, [])
            if not values:
                continue
            # One header's addresses are live at a time, alongside the message they came from
            # and the correspondents kept so far. The previous header's list is already
            # unreachable by the time this one is charged.
            work.account(1 + len(correspondents) + _address_upper_bound(values))
            for display_name, address in getaddresses(values):
                normalized_address = normalize_name(address.strip())
                if not normalized_address or "@" not in normalized_address or normalized_address in self_addresses:
                    continue
                name = _normalize_text(display_name) or normalized_address.split("@", maxsplit=1)[0]
                correspondents.append((name, normalized_address))
                work.account(len(correspondents) + 1)
        return correspondents


def _address_upper_bound(values: list[Any]) -> int:
    """Return the most addresses `getaddresses` can return for one header's values.

    This mirrors the count `getaddresses` computes to validate its own result: one address per
    value plus one per comma inside it, with a malformed header collapsing to a single empty
    tuple rather than exceeding it. Commas inside quoted display names are not discounted here,
    which can only raise the number, so it stays an upper bound — and on a `getaddresses` that
    predates that validation the per-append accounting in `_correspondents` is still the
    backstop. Every unit counted costs at least one byte of the source that carried it, so a
    source inside the caller's byte budget cannot reach a parser-work budget derived from it.
    """
    return sum(1 + (value if isinstance(value, str) else str(value)).count(",") for value in values)


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
