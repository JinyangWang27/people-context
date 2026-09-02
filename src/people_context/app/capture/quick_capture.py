"""Record one thing the user said about one person, in one call.

Every existing write takes a ``person_id``, so the smallest useful memory — "Alice from Acme prefers
email" — costs an agent a resolve, a create, an affiliation, and a trait, each a separate approval.
This use case composes those same audited writes behind one input: it resolves the name, creates the
person only when nobody matches, records the statement under the right kind, and commits everything
in one transaction or nothing at all.

It adds no new record type and no new policy. What is stored is exactly what ``remember_person``,
``set_affiliation``, ``set_relationship``, ``record_fact``, ``record_trait``, and
``record_interaction`` would have stored; the audit and changelog rows are theirs. The only judgement
made here is *which* of those to call when the caller did not say, and that rule is a short, fixed
keyword table (``classify_note``) rather than a model — an agent that knows better passes ``kind``.

Identity is never guessed for a write. An ambiguous resolution, or a single match that is not an exact
name and not a strong search hit, returns the candidates and records nothing.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from people_context.app._mutation import unit_of_work_for
from people_context.app.people import (
    EXACT_MATCH_REASON,
    FUZZY_MATCH_REASON,
    RememberPerson,
    RememberPersonInput,
    ResolutionCandidate,
    ResolutionHints,
    ResolvePerson,
    base_match_reason,
)
from people_context.app.records import (
    RecordFact,
    RecordFactInput,
    RecordInteraction,
    RecordInteractionInput,
    RecordTrait,
    RecordTraitInput,
    SetAffiliation,
    SetAffiliationInput,
)
from people_context.app.relationships import SetRelationship, SetRelationshipInput
from people_context.domain.shared import Sensitivity, new_id
from people_context.domain.trait import TraitCategory
from people_context.ports.audit_log import AuditLog
from people_context.ports.repository import PersonReader

CaptureKind = Literal["auto", "fact", "trait", "interaction", "affiliation", "relationship"]
RecordedKind = Literal["fact", "trait", "interaction", "affiliation", "relationship"]

#: A non-exact top candidate must score at least this to receive a write. Search hits score
#: ``0.4 + 0.4 * relevance``, so this admits a strong lexical match. It is a floor, not the whole
#: test: an edit-distance candidate is refused by :func:`_is_confident` at any score.
CONFIDENT_WRITE_SCORE = 0.7

#: Role recorded for an affiliation when the caller named an organisation but no role.
DEFAULT_ROLE = "member"

#: Predicate under which an unstructured statement is stored as a fact.
DEFAULT_PREDICATE = "note"

#: Markers that place a statement on a day other than the one it is said on, whether relative
#: ("yesterday", "a month ago") or absolute ("2026-08-20", "on Tuesday", "12 Aug"). An interaction is
#: the store's recency signal — `get_stale_relationships` and the timeline both read its date — so
#: one of these without an explicit `occurred_at` is refused rather than dated now, the same rule
#: the staged import path states for its `interaction` candidates. The list errs towards refusing:
#: an unnecessary question costs a round-trip, while a missed one silently backdates the answer to
#: "when did I last speak to this person?". Bare "May" is left out because the month is far more
#: often the verb, and a bare year needs a preposition so "the 2026 budget" is not read as a date.
_PAST_CUE = re.compile(
    r"""
    \b(?:
        yesterday
      | last\ (?:night|week|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)
      | on\ (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)
      | (?:\d+|a|an|few|couple\ of)\ (?:day|week|month|year)s?\ ago
      | (?:in|back\ in|during)\ (?:19|20)\d{2}
      | \d{4}[-/]\d{1,2}[-/]\d{1,2}
      | \d{1,2}[/.]\d{1,2}[/.]\d{2,4}
      | (?:jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\ \d{1,2}
      | \d{1,2}(?:st|nd|rd|th)?\ (?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*
    )\b
    """,
    re.VERBOSE,
)

# Keyword tables for ``classify_note``. Lowercase substrings; order of the checks matters and is
# documented on the function. Kept small on purpose: the goal is a sensible default, not NLP.
_INTERACTION_CUES = (
    "met ",
    "met.",
    "talked",
    "spoke",
    "called ",
    "chatted",
    "had lunch",
    "had coffee",
    "had dinner",
    "had a call",
    "meeting with",
    "discussed",
    "caught up",
    "yesterday",
    "today",
    "this morning",
    "last week",
    "last night",
)
_COMMUNICATION_CUES = (
    "email",
    "e-mail",
    "call",
    "phone",
    "text",
    "message",
    "slack",
    "whatsapp",
    "reply",
    "respond",
    "tone",
    "formal",
    "blunt",
    "direct",
    "brief",
    "concise",
    "detail",
    "small talk",
)
_PREFERENCE_CUES = ("prefer", "like", "dislike", "hate", "love", "enjoy", "favorite", "favourite", "allerg")
_AVOID_CUES = ("avoid", "don't bring up", "do not bring up", "don't mention", "do not mention", "sore subject")


class QuickCaptureInput(BaseModel):
    """One statement about one person, named by the name the user used."""

    person: str = Field(min_length=1)
    note: str | None = None
    kind: CaptureKind = "auto"
    occurred_at: datetime | None = None
    org: str | None = None
    role: str | None = None
    relationship: str | None = None
    predicate: str | None = None
    trait_category: TraitCategory | None = None
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    source: str = "agent"
    session: str | None = None


class CapturedRecord(BaseModel):
    """One durable row this call created, named by the same kind vocabulary the tools use."""

    kind: RecordedKind
    id: str
    summary: str


class QuickCaptureResult(BaseModel):
    """What happened. ``recorded`` is empty unless ``status`` is ``recorded``."""

    status: Literal["recorded", "ambiguous", "unconfirmed", "no_self", "nothing_to_record", "invalid_request"]
    person_id: str | None = None
    canonical_name: str | None = None
    created: bool = False
    recorded: list[CapturedRecord] = Field(default_factory=list)
    candidates: list[ResolutionCandidate] = Field(default_factory=list)
    message: str | None = None


def classify_note(note: str) -> tuple[RecordedKind, TraitCategory | None]:
    """Pick a record kind for a statement whose caller did not say.

    Checked in this order, first hit wins:

    1. an interaction cue (``met``, ``talked``, ``had lunch``, ``yesterday`` …) → ``interaction``;
    2. a topic-to-avoid cue (``avoid``, ``don't bring up`` …) with no communication cue →
       ``trait`` / ``topics_to_avoid``;
    3. a communication cue (``email``, ``call``, ``reply``, ``tone``, ``concise`` …) together with a
       preference or avoid cue → ``trait`` / ``communication_style``;
    4. a preference cue alone (``prefer``, ``likes``, ``allergic`` …) → ``trait`` / ``preference``;
    5. otherwise → ``fact`` under the ``note`` predicate.
    """
    lowered = f" {note.casefold()} "
    if any(cue in lowered for cue in _INTERACTION_CUES):
        return "interaction", None
    communication = any(cue in lowered for cue in _COMMUNICATION_CUES)
    preference = any(cue in lowered for cue in _PREFERENCE_CUES)
    avoid = any(cue in lowered for cue in _AVOID_CUES)
    if avoid and not communication:
        return "trait", TraitCategory.TOPICS_TO_AVOID
    if communication and (preference or avoid):
        return "trait", TraitCategory.COMMUNICATION_STYLE
    if preference:
        return "trait", TraitCategory.PREFERENCE
    return "fact", None


def _is_confident(candidate: ResolutionCandidate) -> bool:
    """Whether one candidate identifies the person well enough to write to their record.

    An exact name or alias always does. Anything else needs a strong score — and a candidate the
    resolver reached only by edit distance never qualifies, however high its score climbed. That
    last clause is the one that has to be said out loud: matched hints add 0.15 each and rewrite
    the reason, so `remember(person="Alicja Stone", org="Acme", role="CTO")` presents a 0.45 guess
    at 0.75, and a score test alone would file the note on the stored Alicia Stone.
    """
    reason = base_match_reason(candidate.match_reason)
    if reason == EXACT_MATCH_REASON:
        return True
    return reason != FUZZY_MATCH_REASON and candidate.score >= CONFIDENT_WRITE_SCORE


def _validate(data: QuickCaptureInput, note: str | None) -> str | None:
    """Refuse, before anything is resolved or created, a request that could not record what it means.

    Four shapes are caught here. A structural ``kind`` without its payload would create the person
    and then record nothing, reporting success. A structural ``kind`` alongside a note is a
    contradiction — ``kind`` says how to record the note, and those two values do not describe a
    note — so the note would be dropped rather than guessed at. And an affiliation or relationship
    carries no sensitivity field, so an elevated ``sensitivity`` on one would be silently dropped
    and the row disclosed by every ordinary read — the outcome the level exists to prevent. And a
    note that says an interaction happened earlier — "met Alice yesterday" — would otherwise be
    stored with the current time, which is not a small inaccuracy: the date is the whole content of
    the recency signal, so the report meant to surface a lapsed relationship would show it as
    current.
    """
    if data.kind == "affiliation" and data.org is None:
        return "kind=affiliation needs `org`; nothing was recorded."
    if data.kind == "relationship" and data.relationship is None:
        return "kind=relationship needs `relationship`; nothing was recorded."
    if data.kind in ("affiliation", "relationship") and note is not None:
        return (
            f"kind={data.kind} describes the structural record, not the note, so `note` would not be "
            "recorded; nothing was recorded. Omit `kind` to record both, or drop `note`."
        )
    if data.kind in ("fact", "trait", "interaction") and note is None:
        return f"kind={data.kind} needs `note`; nothing was recorded."
    if note is not None and data.occurred_at is None and _PAST_CUE.search(note.casefold()):
        kind = data.kind if data.kind != "auto" else classify_note(note)[0]
        if kind == "interaction":
            return (
                "that note says the interaction happened earlier, and recording it as happening now would "
                "misreport when you last spoke; nothing was recorded. Pass `occurred_at` with the date it "
                "happened."
            )
    if data.sensitivity in (Sensitivity.SENSITIVE, Sensitivity.RESTRICTED) and (
        data.org is not None or data.relationship is not None
    ):
        return (
            "Affiliations and relationships have no sensitivity level and are disclosed by every ordinary "
            "read, so they cannot be recorded as sensitive or restricted; nothing was recorded. Record the "
            "private statement as a fact instead (kind=fact), and leave the affiliation or relationship out."
        )
    return None


class QuickCapture:
    """Resolve, create if needed, record once, commit once."""

    def __init__(
        self,
        people: PersonReader,
        resolve_person: ResolvePerson,
        remember_person: RememberPerson,
        set_affiliation: SetAffiliation,
        set_relationship: SetRelationship,
        record_fact: RecordFact,
        record_trait: RecordTrait,
        record_interaction: RecordInteraction,
        audit: AuditLog,
    ) -> None:
        self._people = people
        self._resolve = resolve_person
        self._remember = remember_person
        self._set_affiliation = set_affiliation
        self._set_relationship = set_relationship
        self._record_fact = record_fact
        self._record_trait = record_trait
        self._record_interaction = record_interaction
        self._uow = unit_of_work_for(audit)

    def execute(self, data: QuickCaptureInput) -> QuickCaptureResult:
        """Record the statement, or explain without writing why it could not be attributed."""
        note = (data.note or "").strip() or None
        if note is None and data.org is None and data.relationship is None:
            return QuickCaptureResult(
                status="nothing_to_record",
                message="Give a note, an org, or a relationship; a bare name records nothing.",
            )
        invalid = _validate(data, note)
        if invalid is not None:
            return QuickCaptureResult(status="invalid_request", message=invalid)
        if data.relationship is not None and self._people.get_self() is None:
            return QuickCaptureResult(
                status="no_self",
                message="A relationship is recorded from the user's own record, and none exists yet; "
                "run `pctx init` or `remember_person` with is_self=true first.",
            )

        with self._uow:
            transaction_id = new_id()
            identity = self._identify(data, transaction_id)
            if isinstance(identity, QuickCaptureResult):
                return identity
            person_id, canonical_name, created = identity
            recorded = self._record(person_id, note, data, transaction_id)
            return QuickCaptureResult(
                status="recorded",
                person_id=person_id,
                canonical_name=canonical_name,
                created=created,
                recorded=recorded,
            )

    def _identify(
        self, data: QuickCaptureInput, transaction_id: str
    ) -> tuple[str, str, bool] | QuickCaptureResult:
        hints = ResolutionHints(org=data.org, role=data.role, relationship=data.relationship)
        resolution = self._resolve.execute(data.person, limit=5, hints=hints)
        if resolution.ambiguous:
            return QuickCaptureResult(
                status="ambiguous",
                candidates=resolution.candidates,
                message=f"{data.person!r} matches several people; nothing was recorded. "
                "Ask which one, then call again with a unique name or alias.",
            )
        if resolution.candidates:
            top = resolution.candidates[0]
            if not _is_confident(top):
                return QuickCaptureResult(
                    status="unconfirmed",
                    candidates=resolution.candidates,
                    message=f"{data.person!r} only loosely matches {top.canonical_name!r}; nothing was recorded. "
                    "Confirm with the user, then call again with that exact name — or a new one to create.",
                )
            return top.person_id, top.canonical_name, False
        result = self._remember.execute(
            RememberPersonInput(name=data.person, source=data.source, session=data.session),
            transaction_id=transaction_id,
        )
        return result.person.id, result.person.canonical_name, True

    def _record(
        self, person_id: str, note: str | None, data: QuickCaptureInput, transaction_id: str
    ) -> list[CapturedRecord]:
        recorded: list[CapturedRecord] = []
        kind: RecordedKind | None = None if data.kind == "auto" else data.kind
        category = data.trait_category

        # `org` and `relationship` are payloads, not classifications: they record their own rows
        # whatever `kind` says about the note, so a combined statement never loses half of itself.
        if data.org is not None:
            role = data.role or DEFAULT_ROLE
            affiliation = self._set_affiliation.execute(
                SetAffiliationInput(
                    person_id=person_id, org=data.org, role=role, source=data.source, session=data.session
                ),
                transaction_id=transaction_id,
            )
            recorded.append(CapturedRecord(kind="affiliation", id=affiliation.id, summary=f"{role} at {data.org}"))
        if data.relationship is not None:
            self_person = self._people.get_self()
            assert self_person is not None  # checked in execute before the transaction opened
            relationship = self._set_relationship.execute(
                SetRelationshipInput(
                    subject_id=self_person.id,
                    object_id=person_id,
                    type=data.relationship,
                    source=data.source,
                ),
                transaction_id=transaction_id,
            )
            recorded.append(
                CapturedRecord(kind="relationship", id=relationship.id, summary=f"{relationship.type} (from you)")
            )
        # A structural `kind` never reaches here carrying a note: `_validate` refused that pairing.
        if note is None or kind in ("affiliation", "relationship"):
            return recorded

        if kind is None:
            kind, inferred = classify_note(note)
            category = category or inferred
        if kind == "fact":
            predicate = data.predicate or DEFAULT_PREDICATE
            fact = self._record_fact.execute(
                RecordFactInput(
                    person_id=person_id,
                    predicate=predicate,
                    value=note,
                    sensitivity=data.sensitivity,
                    source=data.source,
                    session=data.session,
                ),
                transaction_id=transaction_id,
            )
            recorded.append(CapturedRecord(kind="fact", id=fact.id, summary=f"{predicate}: {note}"))
        elif kind == "trait":
            trait_category = category or TraitCategory.OTHER
            trait = self._record_trait.execute(
                RecordTraitInput(
                    person_id=person_id,
                    category=trait_category,
                    value=note,
                    sensitivity=data.sensitivity,
                    source=data.source,
                    session=data.session,
                ),
                transaction_id=transaction_id,
            )
            recorded.append(CapturedRecord(kind="trait", id=trait.id, summary=f"{trait_category.value}: {note}"))
        elif kind == "interaction":
            interaction = self._record_interaction.execute(
                RecordInteractionInput(
                    summary=note,
                    participant_ids=[person_id],
                    occurred_at=data.occurred_at,
                    sensitivity=data.sensitivity,
                    source=data.source,
                    session=data.session,
                ),
                transaction_id=transaction_id,
            )
            recorded.append(CapturedRecord(kind="interaction", id=interaction.id, summary=note))
        return recorded
