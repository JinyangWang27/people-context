"""LinkedIn Connections CSV extraction into narrow staged candidates."""

from __future__ import annotations

import csv
import itertools
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date

from people_context.adapters.importers.bounded_source import (
    CandidateBudget,
    ParserWorkBudget,
    open_source_stream,
)
from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.importers.normalization import clean_text, normalize_email
from people_context.domain.person import AliasKind
from people_context.domain.shared import normalize_name
from people_context.ports.imports import ExtractedImport

_EXPECTED_HEADERS = frozenset(
    {
        "First Name",
        "Last Name",
        "URL",
        "Email Address",
        "Company",
        "Position",
        "Connected On",
    }
)
_ENGLISH_MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
_ENGLISH_DATE_RE = re.compile(r"^(\d{2}) ([A-Z][a-z]{2}) (\d{4})$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class _PersonAccumulator:
    """One batch-local person, optionally coalesced by normalized email."""

    ref: str
    name: str
    email: str | None
    alternate_names: list[str] = field(default_factory=list)


class LinkedInImportExtractor:
    """Parse canonical LinkedIn Connections CSV rows without retaining profile or free text."""

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
        """Extract connection rows; ``self_names`` and ``self_sender`` are unused by this source.

        The export's preamble is skipped by scanning one line at a time rather than by splitting
        the whole file, and `csv` then streams rows off the same reader, so nothing larger than
        the current line and the current row is held while the source is parsed.
        """
        if source_type != "linkedin":
            raise ImportExtractionError("invalid_source_type", "source_type must be 'linkedin'")
        work = ParserWorkBudget(max_retained_parse_records)
        with open_source_stream(
            content=content,
            content_bytes=content_bytes,
            path=path,
            encoding="utf-8-sig",
            max_bytes=max_source_bytes,
            source_label="linkedin",
            strip_content_bom=True,
            universal_newlines=False,
        ) as lines:
            return self._extract_rows(
                csv.DictReader(_from_canonical_header(lines, work), strict=True),
                self_addresses=self_addresses,
                max_candidates=max_candidates,
                work=work,
            )

    @staticmethod
    def _extract_rows(
        reader: csv.DictReader[str],
        *,
        self_addresses: set[str],
        max_candidates: int | None,
        work: ParserWorkBudget,
    ) -> ExtractedImport:
        """Turn the streamed connection rows into candidates, holding one row at a time."""
        headers = reader.fieldnames
        if headers is None or not _EXPECTED_HEADERS.issubset(headers):
            raise ImportExtractionError("invalid_headers", "linkedin CSV is missing required canonical headers")

        # Self handles are compared as addresses, not as names: name normalization strips combining
        # marks, which would fold a self handle onto a genuinely distinct ASCII contact address.
        normalized_self = {normalized for address in self_addresses if (normalized := normalize_email(address))}
        people: list[_PersonAccumulator] = []
        people_by_email: dict[str, _PersonAccumulator] = {}
        affiliations: list[dict[str, object]] = []
        facts: list[dict[str, object]] = []
        seen_affiliations: set[tuple[str, str, str]] = set()
        seen_facts: set[tuple[str, str]] = set()
        skipped: list[dict[str, int | str]] = []
        budget = CandidateBudget(max_candidates)

        try:
            for row_index, row in enumerate(reader, start=1):
                work.account(1)
                name = _combined_name(row)
                if not name:
                    skipped.append({"index": row_index, "reason": "missing_name"})
                    continue
                raw_email = clean_text(row.get("Email Address"))
                email = normalize_email(raw_email)
                if raw_email and email is None:
                    skipped.append({"index": row_index, "reason": "invalid_email"})
                    continue
                if email in normalized_self:
                    continue
                connected_on, invalid_date = _parse_connected_on(clean_text(row.get("Connected On")))
                if invalid_date:
                    skipped.append({"index": row_index, "reason": "invalid_connected_on"})
                    continue

                person = people_by_email.get(email) if email is not None else None
                if person is None:
                    person = _PersonAccumulator(
                        ref=f"linkedin-person-{len(people) + 1}",
                        name=name,
                        email=email,
                    )
                    people.append(person)
                    if email is not None:
                        people_by_email[email] = person
                else:
                    _add_alternate_name(person, name)

                company = clean_text(row.get("Company"))
                position = clean_text(row.get("Position"))
                if company and position:
                    key = (person.ref, normalize_name(company), normalize_name(position))
                    if key not in seen_affiliations:
                        seen_affiliations.add(key)
                        affiliations.append(
                            {
                                "type": "affiliation",
                                "person_ref": person.ref,
                                "org": company,
                                "role": position,
                            }
                        )
                if connected_on is not None:
                    fact_key = (person.ref, connected_on.isoformat())
                    if fact_key not in seen_facts:
                        seen_facts.add(fact_key)
                        facts.append(
                            {
                                "type": "fact",
                                "person_ref": person.ref,
                                "predicate": "linkedin_connected_on",
                                "value": connected_on.isoformat(),
                            }
                        )
                budget.account(len(people) + len(affiliations) + len(facts))
        except csv.Error as exc:
            raise ImportExtractionError("invalid_csv", "linkedin CSV is malformed") from exc

        candidates = [_person_candidate(person) for person in people]
        return ExtractedImport(
            people=[],
            interactions=[],
            candidates=[*candidates, *affiliations, *facts],
            skipped_cards=skipped,
        )


def _from_canonical_header(lines: Iterable[str], work: ParserWorkBudget) -> Iterator[str]:
    """Yield the export from its canonical header row onward, scanning one line at a time.

    A LinkedIn export puts a notice above the header, and the header is found by trying to read
    each candidate line as a CSV record. The old scan split the whole file to do that; this one
    holds a single line, and splits only that line further so the search still breaks at every
    boundary `str.splitlines` recognizes rather than at newlines alone.

    What follows the matched piece is handed straight through, so `csv` sees exactly the record
    stream it saw when the tail was rejoined into one string first.
    """
    iterator = iter(lines)
    for line in iterator:
        work.account(1)
        pieces = line.splitlines(keepends=True)
        for index, piece in enumerate(pieces):
            try:
                columns = next(csv.reader([piece], strict=True))
            except csv.Error:
                continue
            if _EXPECTED_HEADERS.issubset(columns):
                yield from itertools.chain(["".join(pieces[index:])], iterator)
                return
    raise ImportExtractionError("invalid_headers", "linkedin CSV is missing required canonical headers")


def _combined_name(row: dict[str | None, str | list[str] | None]) -> str:
    return " ".join(value for value in (clean_text(row.get("First Name")), clean_text(row.get("Last Name"))) if value)


def _parse_connected_on(value: str) -> tuple[date | None, bool]:
    if not value:
        return None, False
    try:
        if _ISO_DATE_RE.fullmatch(value):
            return date.fromisoformat(value), False
        match = _ENGLISH_DATE_RE.fullmatch(value)
        if match is None:
            return None, True
        day, month_name, year = match.groups()
        month = _ENGLISH_MONTHS.get(month_name)
        if month is None:
            return None, True
        return date(int(year), month, int(day)), False
    except ValueError:
        return None, True


def _add_alternate_name(person: _PersonAccumulator, name: str) -> None:
    normalized = normalize_name(name)
    known = {normalize_name(person.name), *(normalize_name(value) for value in person.alternate_names)}
    if normalized not in known:
        person.alternate_names.append(name)


def _person_candidate(person: _PersonAccumulator) -> dict[str, object]:
    aliases: list[dict[str, str]] = []
    if person.email is not None:
        aliases.append({"value": person.email, "kind": AliasKind.HANDLE.value})
    aliases.extend({"value": name, "kind": AliasKind.OTHER.value} for name in person.alternate_names)
    return {
        "type": "person",
        "ref": person.ref,
        "name": person.name,
        "aliases": aliases,
        "message_id": None,
        "date": None,
    }
