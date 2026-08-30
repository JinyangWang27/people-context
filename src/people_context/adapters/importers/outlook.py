"""Outlook contacts CSV extraction into narrow staged candidates."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date

from people_context.adapters.importers.bounded_source import (
    CandidateBudget,
    ParserWorkBudget,
    drain_source,
    open_source_stream,
)
from people_context.adapters.importers.errors import ImportExtractionError
from people_context.adapters.importers.normalization import clean_text, normalize_email
from people_context.domain.person import AliasKind
from people_context.domain.shared import normalize_name
from people_context.ports.imports import ExtractedImport

# The canonical Outlook contacts export columns this extractor consumes. Additional exported
# columns (phones, addresses, notes, web pages, and the many locale-specific extras) are
# tolerated and never read, so free text and profile URLs cannot reach a staged candidate.
_EXPECTED_HEADERS = frozenset(
    {
        "First Name",
        "Middle Name",
        "Last Name",
        "E-mail Address",
        "Company",
        "Job Title",
        "Birthday",
    }
)
_NAME_COLUMNS = ("First Name", "Middle Name", "Last Name")
# Only year-first birthdays are accepted: a slash-separated Outlook birthday is locale ordered
# and cannot be resolved to a day and month without guessing.
_YEAR_FIRST_DATE_RE = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$")


@dataclass
class _PersonAccumulator:
    """One batch-local person, coalesced by normalized email."""

    ref: str
    name: str
    email: str | None
    alternate_names: list[str] = field(default_factory=list)


class OutlookImportExtractor:
    """Parse Outlook contacts CSV rows into identity, affiliation, and birthday candidates."""

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
        """Extract contact rows; ``self_names`` and ``self_sender`` are unused by this source.

        `csv` already yields one row at a time; what this source used to retain was the decoded
        text behind the reader, which the streaming reader removes. One parsed row is live at a
        time, so the only structures that grow with the file are the candidates themselves.
        """
        if source_type != "outlook":
            raise ImportExtractionError("invalid_source_type", "source_type must be 'outlook'")
        with open_source_stream(
            content=content,
            content_bytes=content_bytes,
            path=path,
            encoding="utf-8-sig",
            max_bytes=max_source_bytes,
            source_label="outlook",
            strip_content_bom=True,
            universal_newlines=False,
        ) as lines:
            try:
                return self._extract_rows(
                    csv.DictReader(lines, strict=True),
                    self_addresses=self_addresses,
                    max_candidates=max_candidates,
                    max_retained_parse_records=max_retained_parse_records,
                )
            except ImportExtractionError:
                # A parse refusal must not outrank one the rest of the source would have
                # produced: the whole-file read decoded everything before parsing anything.
                drain_source(lines)
                raise

    @staticmethod
    def _extract_rows(
        reader: csv.DictReader[str],
        *,
        self_addresses: set[str],
        max_candidates: int | None,
        max_retained_parse_records: int | None,
    ) -> ExtractedImport:
        """Turn the streamed contact rows into candidates, holding one row at a time."""
        try:
            # Reading the header row parses CSV too, so it belongs inside the error boundary.
            headers = reader.fieldnames
        except csv.Error as exc:
            raise ImportExtractionError("invalid_csv", "outlook CSV is malformed") from exc
        if headers is None or not _EXPECTED_HEADERS.issubset(headers):
            raise ImportExtractionError("invalid_headers", "outlook CSV is missing required canonical headers")

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
        work = ParserWorkBudget(max_retained_parse_records)

        try:
            for row_index, row in enumerate(reader, start=1):
                work.account(1)
                name = _combined_name(row)
                if not name:
                    skipped.append({"index": row_index, "reason": "missing_name"})
                    continue
                raw_email = clean_text(row.get("E-mail Address"))
                email = normalize_email(raw_email)
                if raw_email and email is None:
                    skipped.append({"index": row_index, "reason": "invalid_email"})
                    continue
                if email is not None and email in normalized_self:
                    continue

                person = people_by_email.get(email) if email is not None else None
                if person is None:
                    person = _PersonAccumulator(
                        ref=f"outlook-person-{len(people) + 1}",
                        name=name,
                        email=email,
                    )
                    people.append(person)
                    if email is not None:
                        people_by_email[email] = person
                else:
                    _add_alternate_name(person, name)

                company = clean_text(row.get("Company"))
                job_title = clean_text(row.get("Job Title"))
                if company and job_title:
                    key = (person.ref, normalize_name(company), normalize_name(job_title))
                    if key not in seen_affiliations:
                        seen_affiliations.add(key)
                        affiliations.append(
                            {
                                "type": "affiliation",
                                "person_ref": person.ref,
                                "org": company,
                                "role": job_title,
                            }
                        )

                raw_birthday = clean_text(row.get("Birthday"))
                birthday = _parse_birthday(raw_birthday)
                if raw_birthday and birthday is None:
                    # The row's identity is still trustworthy, so only the birthday is dropped.
                    skipped.append({"index": row_index, "reason": "invalid_birthday"})
                elif birthday is not None:
                    fact_key = (person.ref, birthday.isoformat())
                    if fact_key not in seen_facts:
                        seen_facts.add(fact_key)
                        facts.append(
                            {
                                "type": "fact",
                                "person_ref": person.ref,
                                "predicate": "birthday",
                                "value": birthday.isoformat(),
                            }
                        )
                budget.account(len(people) + len(affiliations) + len(facts))
        except csv.Error as exc:
            raise ImportExtractionError("invalid_csv", "outlook CSV is malformed") from exc

        candidates = [_person_candidate(person) for person in people]
        return ExtractedImport(
            people=[],
            interactions=[],
            candidates=[*candidates, *affiliations, *facts],
            skipped_cards=skipped,
        )


def _combined_name(row: dict[str | None, str | list[str] | None]) -> str:
    return " ".join(value for value in (clean_text(row.get(column)) for column in _NAME_COLUMNS) if value)


def _parse_birthday(value: str) -> date | None:
    match = _YEAR_FIRST_DATE_RE.fullmatch(value)
    if match is None:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


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
