"""A bounded chronology of what happened around one person.

The timeline answers "what changed around this person, and when?" — not "what does every audit
row say". Audit and changelog remain the lower-level operational history; this is a projection
over the durable records themselves, assembled per call and stored nowhere.

Three rules decide everything below.

**A page is bounded, and the bound is the whole contract.** The reader returns one row past the
page so the result can say `truncated` without counting a table, and a person with a hundred
thousand imported interactions costs exactly what a person with three costs.

**Ordering is deterministic, and its ties are stable.** Entries are newest first by effective
instant, then by entry type, then by id. The instant is compared as UTC — an aware timestamp is
converted and a naive one is read as UTC, never in the host timezone — so the order of one
database never depends on the machine that read it.

**Nothing is invented and nothing is dropped.** A record that carries a validity period is placed
by its `valid_from`; one without is placed by the time it was recorded, and every entry names
which stored field it was placed by. A date-only `valid_from` is placed at `00:00:00Z`, the same
deterministic convention M9.2 fixed for all-day calendar values, and the entry still carries the
date itself so a caller can see the granularity it came from.

Disclosure is the ordinary rule: `public`/`personal` unless the caller explicitly asks for more,
which only the local human CLI does. Affiliations and relationships carry no stored level and are
ordinary by construction. A trait's evidence is filtered by the *evidence's* own level rather than
the trait's, because naming a restricted observation beside a visible trait would disclose that
the record exists — exactly the disclosure M18.3 requires the visible trait not to make.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Final

from pydantic import BaseModel, Field

from people_context.app.exports._document import render_json_document
from people_context.domain.shared import Sensitivity, as_utc
from people_context.domain.trait_evidence import MAX_TRAIT_EVIDENCE_LINKS
from people_context.ports.repository import PersonReader
from people_context.ports.timeline import (
    ENTRY_TRAIT,
    PersonTimelineReader,
    TimelineRow,
)

#: Entries one page carries when the caller says nothing.
DEFAULT_TIMELINE_LIMIT: Final = 50

#: The narrowest and widest page a caller may ask for. The ceiling bounds one response; a longer
#: history is read by asking for a wider page, not by an unbounded scan.
MIN_TIMELINE_LIMIT: Final = 1
MAX_TIMELINE_LIMIT: Final = 200

#: Evidence citations one trait entry reports. This is the same ceiling M18.3 places on a staged
#: trait candidate, so a trait recorded through the supported path is never truncated here; a
#: restored trait carrying more says so through `evidence_truncated` rather than silently losing
#: links.
MAX_TIMELINE_EVIDENCE_LINKS: Final = MAX_TRAIT_EVIDENCE_LINKS

PERSON_TIMELINE_FORMAT: Final = "people-context-person-timeline"
PERSON_TIMELINE_VERSION: Final = 1

#: Levels an ordinary read may disclose, in the shared order used by every other read path.
ORDINARY_SENSITIVITIES: Final[tuple[Sensitivity, ...]] = (Sensitivity.PUBLIC, Sensitivity.PERSONAL)

#: Every level, for the explicit local opt-in.
ALL_SENSITIVITIES: Final[tuple[Sensitivity, ...]] = (
    Sensitivity.PUBLIC,
    Sensitivity.PERSONAL,
    Sensitivity.SENSITIVE,
    Sensitivity.RESTRICTED,
)


class PersonTimelineError(ValueError):
    """Raised when a timeline parameter falls outside its documented range."""


class TimelineEvidenceLink(BaseModel):
    """One durable record a trait entry rests on.

    The type is part of the citation rather than decoration: ids are opaque and unique only within
    their own table, so a restored store may hold an observation and an interaction sharing one id
    and a trait may cite both. Reporting the bare id would render two distinct records as one
    string, and leave a consumer unable to resolve either.
    """

    evidence_type: str
    evidence_id: str


class TimelineEntry(BaseModel):
    """One durable record placed on the chronology.

    `effective_at` is the instant the entry is ordered by and `basis` names the stored field it
    came from, so "this happened then" is never confused with "this was written down then".
    `sensitivity` is `null` for affiliations and relationships, whose durable contract carries no
    disclosure level and which are therefore always ordinary.
    """

    entry_type: str
    entry_id: str
    person_id: str
    effective_at: datetime
    basis: str
    summary: str
    detail: str | None = None
    sensitivity: Sensitivity | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    source_session_id: str | None = None
    evidence: list[TimelineEvidenceLink] = Field(default_factory=list)
    evidence_truncated: bool = False


class PersonTimelineResult(BaseModel):
    """One bounded newest-first page of a person's chronology.

    `found` is false for an unknown or soft-deleted person, exactly as person context reports it,
    and carries no entries rather than an error.
    """

    found: bool
    person_id: str
    limit: int
    include_sensitive: bool = False
    entries: list[TimelineEntry] = Field(default_factory=list)
    truncated: bool = False


class PersonTimelineDocument(BaseModel):
    """The versioned timeline document; a declared machine interface under the M12 promise."""

    format: str = PERSON_TIMELINE_FORMAT
    version: int = PERSON_TIMELINE_VERSION
    found: bool
    person_id: str
    limit: int
    include_sensitive: bool = False
    entries: list[TimelineEntry] = Field(default_factory=list)
    truncated: bool = False


class GetPersonTimeline:
    """Assemble one bounded, deterministically ordered page of a person's history."""

    def __init__(self, people: PersonReader, timeline: PersonTimelineReader) -> None:
        self._people = people
        self._timeline = timeline

    def execute(
        self,
        person_id: str,
        *,
        limit: int = DEFAULT_TIMELINE_LIMIT,
        include_sensitive: bool = False,
    ) -> PersonTimelineResult:
        """Return the newest `limit` entries for one active person, newest first."""
        page_limit = _checked_limit(limit)
        person = self._people.get(person_id)
        if person is None or person.deleted_at is not None:
            return PersonTimelineResult(
                found=False,
                person_id=person_id,
                limit=page_limit,
                include_sensitive=include_sensitive,
            )

        sensitivities = ALL_SENSITIVITIES if include_sensitive else ORDINARY_SENSITIVITIES
        rows = self._timeline.list_timeline_rows(
            person_id,
            limit=page_limit,
            sensitivities=sensitivities,
        )
        ordered = _ordered(rows)
        page = ordered[:page_limit]
        return PersonTimelineResult(
            found=True,
            person_id=person_id,
            limit=page_limit,
            include_sensitive=include_sensitive,
            entries=[self._entry(person_id, row, sensitivities) for row in page],
            truncated=len(ordered) > len(page),
        )

    def _entry(
        self,
        person_id: str,
        row: TimelineRow,
        sensitivities: tuple[Sensitivity, ...],
    ) -> TimelineEntry:
        evidence: list[TimelineEvidenceLink] = []
        truncated = False
        if row.entry_type == ENTRY_TRAIT:
            evidence, truncated = self._evidence(row.entry_id, sensitivities)
        return TimelineEntry(
            entry_type=row.entry_type,
            entry_id=row.entry_id,
            person_id=person_id,
            effective_at=row.effective_at,
            basis=row.basis,
            summary=row.summary,
            detail=row.detail,
            sensitivity=row.sensitivity,
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            source_session_id=row.source_session_id,
            evidence=evidence,
            evidence_truncated=truncated,
        )

    def _evidence(
        self,
        trait_id: str,
        sensitivities: tuple[Sensitivity, ...],
    ) -> tuple[list[TimelineEvidenceLink], bool]:
        """Return one trait's disclosable citations, and whether more of them exist.

        The lookup is per trait rather than one query over the whole page on purpose: a shared
        budget would let a single trait with an unusual number of links consume it and leave the
        other traits on the page looking as though they rested on nothing.

        Both the page and the truncation flag are computed over the links this caller may actually
        read, because the reader filtered them. Counting the withheld ones first would have made
        the flag a disclosure in its own right: a visible trait answering with no citations and
        `evidence_truncated` set would prove that elevated evidence exists, which is exactly what
        the level on that evidence is there to prevent.
        """
        rows = self._timeline.list_trait_evidence(
            trait_id,
            limit=MAX_TIMELINE_EVIDENCE_LINKS,
            sensitivities=sensitivities,
        )
        return (
            [
                TimelineEvidenceLink(evidence_type=row.evidence_type, evidence_id=row.evidence_id)
                for row in rows[:MAX_TIMELINE_EVIDENCE_LINKS]
            ],
            len(rows) > MAX_TIMELINE_EVIDENCE_LINKS,
        )


def person_timeline_document(result: PersonTimelineResult) -> PersonTimelineDocument:
    """Project one timeline result into its versioned document, changing nothing."""
    return PersonTimelineDocument(
        found=result.found,
        person_id=result.person_id,
        limit=result.limit,
        include_sensitive=result.include_sensitive,
        entries=list(result.entries),
        truncated=result.truncated,
    )


def render_timeline_json(document: PersonTimelineDocument) -> str:
    """Render the timeline document as canonical JSON text ending in a newline."""
    return render_json_document(document)


def _checked_limit(limit: int) -> int:
    if limit < MIN_TIMELINE_LIMIT or limit > MAX_TIMELINE_LIMIT:
        raise PersonTimelineError(f"limit must be between {MIN_TIMELINE_LIMIT} and {MAX_TIMELINE_LIMIT}")
    return limit


def _ordered(rows: list[TimelineRow]) -> list[TimelineRow]:
    """Order rows newest first, breaking ties by entry type then id.

    The two passes are what make the tie-break stable in both directions: the first sorts ties
    ascending by `(entry_type, entry_id)`, and Python's stable sort preserves that order inside
    each instant while the second pass puts the instants newest first. Sorting once with
    `reverse=True` would have reversed the tie-break too.

    Comparison is on `datetime` values rather than POSIX floats, so two timestamps microseconds
    apart order by what they are rather than by what a float can represent.
    """
    by_identity = sorted(rows, key=lambda row: (row.entry_type, row.entry_id))
    return sorted(by_identity, key=lambda row: as_utc(row.effective_at), reverse=True)

