"""LinkedIn Connections CSV extraction into narrow staged candidates."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from people_context.adapters.email_import import ImportExtractionError
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
_EMAIL_RE = re.compile(
    r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
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
    ) -> ExtractedImport:
        if source_type != "linkedin":
            raise ImportExtractionError("invalid_source_type", "source_type must be 'linkedin'")
        if (content is None) == (path is None):
            raise ImportExtractionError(
                "invalid_source",
                "linkedin import requires exactly one of content or path",
            )
        text = content.lstrip("\ufeff") if content is not None else Path(path or "").read_text(encoding="utf-8-sig")
        reader = csv.DictReader(io.StringIO(_csv_from_canonical_header(text)), strict=True)
        headers = reader.fieldnames
        if headers is None or not _EXPECTED_HEADERS.issubset(headers):
            raise ImportExtractionError("invalid_headers", "linkedin CSV is missing required canonical headers")

        normalized_self = {normalize_name(address) for address in self_addresses if address.strip()}
        people: list[_PersonAccumulator] = []
        people_by_email: dict[str, _PersonAccumulator] = {}
        affiliations: list[dict[str, object]] = []
        facts: list[dict[str, object]] = []
        seen_affiliations: set[tuple[str, str, str]] = set()
        seen_facts: set[tuple[str, str]] = set()
        skipped: list[dict[str, int | str]] = []

        try:
            for row_index, row in enumerate(reader, start=1):
                name = _combined_name(row)
                if not name:
                    skipped.append({"index": row_index, "reason": "missing_name"})
                    continue
                raw_email = _clean(row.get("Email Address"))
                email = _normalize_email(raw_email)
                if raw_email and email is None:
                    skipped.append({"index": row_index, "reason": "invalid_email"})
                    continue
                if email in normalized_self:
                    continue
                connected_on, invalid_date = _parse_connected_on(_clean(row.get("Connected On")))
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

                company = _clean(row.get("Company"))
                position = _clean(row.get("Position"))
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
                    key = (person.ref, connected_on.isoformat())
                    if key not in seen_facts:
                        seen_facts.add(key)
                        facts.append(
                            {
                                "type": "fact",
                                "person_ref": person.ref,
                                "predicate": "linkedin_connected_on",
                                "value": connected_on.isoformat(),
                            }
                        )
        except csv.Error as exc:
            raise ImportExtractionError("invalid_csv", "linkedin CSV is malformed") from exc

        candidates = [_person_candidate(person) for person in people]
        return ExtractedImport(
            people=[],
            interactions=[],
            candidates=[*candidates, *affiliations, *facts],
            skipped_cards=skipped,
        )


def _csv_from_canonical_header(text: str) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        try:
            columns = next(csv.reader([line], strict=True))
        except csv.Error:
            continue
        if _EXPECTED_HEADERS.issubset(columns):
            return "".join(lines[index:])
    raise ImportExtractionError("invalid_headers", "linkedin CSV is missing required canonical headers")


def _combined_name(row: dict[str | None, str | list[str] | None]) -> str:
    return " ".join(value for value in (_clean(row.get("First Name")), _clean(row.get("Last Name"))) if value)


def _clean(value: str | list[str] | None) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _normalize_email(value: str) -> str | None:
    if not value:
        return None
    normalized = normalize_name(value)
    return normalized if _EMAIL_RE.fullmatch(normalized) else None


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
