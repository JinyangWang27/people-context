"""Deterministic one-way vCard export of active people.

This is a read-only projection: it records nothing, mints no audit or changelog rows, and
adds no model-callable tool. The use case decides what may leave the store and hands the
serializer an already-filtered projection, so the adapter can neither widen disclosure nor
reach back into the database.

The mapping is deliberately lossless and non-heuristic. Everything emitted must survive a
round trip through the unchanged `VCardImportExtractor`, which is what bounds it:

- the importer consumes only the first `ORG`/`TITLE` pair, so exactly one affiliation is
  emitted and the remaining active ones are counted;
- the importer stores `BDAY` text verbatim, so only full ISO `YYYY-MM-DD` values are
  emitted. The project's recurring `--MM-DD` spelling is not what a conforming vCard 4
  partial date looks like and has no vCard 3 form at all, so those values are counted
  rather than written in a spelling no consumer agrees on;
- `N` carries the whole canonical name in the family-name component, because splitting a
  name on whitespace guesses a structure the store never recorded.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import TypeVar

from pydantic import BaseModel

from people_context.app.insights.upcoming import BIRTHDAY_PREDICATE, ORDINARY_SENSITIVITIES
from people_context.domain.fact import Fact
from people_context.domain.organization import Affiliation, Organization
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import as_utc, normalize_name
from people_context.ports.clock import Clock
from people_context.ports.export import ExportReader
from people_context.ports.vcard import (
    SUPPORTED_VCARD_VERSIONS,
    VCARD_4_0,
    VCardAffiliation,
    VCardContact,
    VCardProjection,
    VCardWriter,
)

# 4.0 is the current standard (RFC 6350) and the importer accepts it, so a fresh export
# defaults to it; `--version 3.0` stays available for consumers that never adopted it.
DEFAULT_VCARD_VERSION = VCARD_4_0

# Only a complete, real calendar date is portable; `--MM-DD` is the project's own recurring
# spelling and is counted separately rather than emitted.
_FULL_BIRTHDAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_PARTIAL_BIRTHDAY = re.compile(r"^--(\d{2})-(\d{2})$")
# Any leap-year calendar keeps `--02-29` valid while still rejecting `--02-30`.
_MONTH_DAY_CALENDAR_YEAR = 2000

# Mirrors the importer's address shape: an address that does not parse is not exported as
# an `EMAIL`, because a consumer would otherwise reimport a handle as a mail address.
_EMAIL_RE = re.compile(
    r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


class VCardExportError(ValueError):
    """Raised when a vCard export parameter falls outside its documented range."""


class VCardExportResult(BaseModel):
    """One rendered vCard document plus the non-sensitive counts explaining what it omits."""

    version: str
    document: str
    exported: int = 0
    omitted_affiliations: int = 0
    omitted_birthdays: int = 0
    skipped_partial_birthdays: int = 0
    skipped_unparseable_birthdays: int = 0


class ExportVCard:
    """Project active people onto the importer-compatible vCard subset.

    Every time-dependent decision uses the injected clock, so an export is deterministic
    under a fake clock: affiliations are evaluated as of `clock.now().date()` and nothing
    consults wall-clock time again.

    Sensitive and restricted facts are invisible by default. They contribute neither a
    birthday nor a skip count, so the counts never signal that an elevated record exists.
    """

    def __init__(self, reader: ExportReader, writer: VCardWriter, clock: Clock) -> None:
        self._reader = reader
        self._writer = writer
        self._clock = clock

    def execute(
        self,
        *,
        version: str = DEFAULT_VCARD_VERSION,
        include_sensitive: bool = False,
    ) -> VCardExportResult:
        """Return the serialized document and the counts of everything it left out."""
        if version not in SUPPORTED_VCARD_VERSIONS:
            raise VCardExportError(f"version must be one of {', '.join(SUPPORTED_VCARD_VERSIONS)}")

        snapshot = self._reader.read_export()
        as_of = self._clock.now().date()
        organizations = {
            organization.id: organization.name
            for organization in (Organization.model_validate(row) for row in snapshot.organizations)
        }
        affiliations = _group_by_person(Affiliation.model_validate(row) for row in snapshot.affiliations)
        facts = _group_by_person(Fact.model_validate(row) for row in snapshot.facts)

        contacts: list[VCardContact] = []
        omitted_affiliations = 0
        omitted_birthdays = 0
        skipped_partial_birthdays = 0
        skipped_unparseable_birthdays = 0

        for person in _export_order(Person.model_validate(row) for row in snapshot.people):
            full_name = person.canonical_name.strip()
            if not full_name:
                # A card without a usable `FN` is refused by the importer, so exporting one
                # would produce a card that cannot come back.
                continue
            affiliation, omitted = _select_affiliation(affiliations.get(person.id, []), organizations, as_of)
            birthday, counts = _select_birthday(facts.get(person.id, []), include_sensitive=include_sensitive)
            omitted_affiliations += omitted
            omitted_birthdays += counts.omitted
            skipped_partial_birthdays += counts.partial
            skipped_unparseable_birthdays += counts.unparseable
            contacts.append(
                VCardContact(
                    person_id=person.id,
                    full_name=full_name,
                    nicknames=_nicknames(person),
                    emails=_emails(person),
                    affiliation=affiliation,
                    birthday=birthday,
                )
            )

        projection = VCardProjection(version=version, contacts=tuple(contacts))
        return VCardExportResult(
            version=version,
            document=self._writer.write_vcards(projection),
            exported=len(contacts),
            omitted_affiliations=omitted_affiliations,
            omitted_birthdays=omitted_birthdays,
            skipped_partial_birthdays=skipped_partial_birthdays,
            skipped_unparseable_birthdays=skipped_unparseable_birthdays,
        )


@dataclass
class _BirthdayCounts:
    """How many birthday rows one person contributed to each reported outcome."""

    omitted: int = 0
    partial: int = 0
    unparseable: int = 0


_PersonScoped = TypeVar("_PersonScoped", Affiliation, Fact)


def _group_by_person(records: Iterable[_PersonScoped]) -> dict[str, list[_PersonScoped]]:
    """Bucket records by `person_id` without depending on the reader's row order."""
    grouped: dict[str, list[_PersonScoped]] = {}
    for record in records:
        grouped.setdefault(record.person_id, []).append(record)
    return grouped


def _export_order(people: Iterable[Person]) -> list[Person]:
    """Order active people by normalized name and then id, which is a total order."""
    active = [person for person in people if person.deleted_at is None]
    return sorted(active, key=lambda person: (normalize_name(person.canonical_name), person.id))


def _nicknames(person: Person) -> tuple[str, ...]:
    """Return deduplicated nickname aliases, excluding one that only repeats the name."""
    canonical = normalize_name(person.canonical_name)
    values = [
        alias
        for alias in _alias_order(person, AliasKind.NICKNAME)
        if normalize_name(alias.value) != canonical
    ]
    return tuple(_dedupe(alias.value.strip() for alias in values))


def _emails(person: Person) -> tuple[str, ...]:
    """Return deduplicated handle aliases that actually parse as mail addresses."""
    values = [
        alias
        for alias in _alias_order(person, AliasKind.HANDLE)
        if _EMAIL_RE.fullmatch(normalize_name(alias.value))
    ]
    return tuple(_dedupe(alias.value.strip() for alias in values))


def _alias_order(person: Person, kind: AliasKind) -> list[Alias]:
    """Order one kind of non-empty alias by normalized value and then alias id."""
    matching = [alias for alias in person.aliases if alias.kind == kind and alias.value.strip()]
    return sorted(matching, key=lambda alias: (normalize_name(alias.value), alias.id))


def _dedupe(values: Iterable[str]) -> list[str]:
    """Drop values that normalize to one already kept, preserving the given order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = normalize_name(value)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _select_affiliation(
    affiliations: list[Affiliation],
    organizations: dict[str, str],
    as_of: date,
) -> tuple[VCardAffiliation | None, int]:
    """Pick one affiliation active on `as_of` and count the additional active ones.

    Only an affiliation that resolves to a named organization and a non-empty role can be
    written at all, because the importer creates an affiliation candidate only when both
    `ORG` and `TITLE` carry a value. Selection is by normalized organization name, then
    normalized role, then affiliation id, so the same store always exports the same pair.
    """
    candidates = [
        (organizations[affiliation.org_id].strip(), affiliation.role.strip(), affiliation.id)
        for affiliation in affiliations
        if affiliation.period.contains(as_of) and affiliation.org_id in organizations
    ]
    usable = [row for row in candidates if row[0] and row[1]]
    if not usable:
        return None, 0
    usable.sort(key=lambda row: (normalize_name(row[0]), normalize_name(row[1]), row[2]))
    organization, role, _id = usable[0]
    return VCardAffiliation(organization=organization, role=role), len(usable) - 1


def _select_birthday(facts: list[Fact], *, include_sensitive: bool) -> tuple[date | None, _BirthdayCounts]:
    """Pick one full-date birthday and report what the remaining rows were.

    Selection is by highest confidence, then newest `recorded_at`, then fact id. Timestamps
    are compared as UTC instants through `as_utc`, so a stored naive value never makes the
    choice depend on the host timezone.
    """
    counts = _BirthdayCounts()
    candidates: list[tuple[float, float, str, date]] = []
    for fact in facts:
        if fact.predicate != BIRTHDAY_PREDICATE:
            continue
        if not include_sensitive and fact.sensitivity not in ORDINARY_SENSITIVITIES:
            continue
        birthday = _parse_full_birthday(fact.value)
        if birthday is not None:
            candidates.append((-fact.confidence, -as_utc(fact.recorded_at).timestamp(), fact.id, birthday))
        elif _is_partial_birthday(fact.value):
            counts.partial += 1
        else:
            counts.unparseable += 1
    if not candidates:
        return None, counts
    candidates.sort(key=lambda row: row[:3])
    counts.omitted = len(candidates) - 1
    return candidates[0][3], counts


def _parse_full_birthday(value: str) -> date | None:
    """Return the real calendar date a full ISO birthday value names, if it is one."""
    match = _FULL_BIRTHDAY.fullmatch(value.strip())
    if match is None:
        return None
    year, month, day = (int(group) for group in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _is_partial_birthday(value: str) -> bool:
    """Return whether a value is the project's recurring `--MM-DD` form for a real day."""
    match = _PARTIAL_BIRTHDAY.fullmatch(value.strip())
    if match is None:
        return False
    month, day = int(match.group(1)), int(match.group(2))
    try:
        date(_MONTH_DAY_CALENDAR_YEAR, month, day)
    except ValueError:
        return False
    return True
