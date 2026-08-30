"""Close a fact that was true and open its replacement, atomically.

A **correction** and a **state transition** are different domain events, and conflating them loses
history that cannot be recovered. `CorrectRecord` says "the stored value was wrong"; it overwrites
the row and keeps the old value only in the audit trail. Superseding says "the stored value was
right, and then the world changed" — the old assertion must keep its value, its provenance, and the
stretch of time it held for, while a new assertion takes over from a known date.

Four rules make that safe.

**The replacement inherits the old assertion's original end date.** An assertion bounded to
`[2026-01-01, 2026-12-31]` superseded on `2026-07-01` leaves the old row valid through `2026-06-30`
and a replacement valid `[2026-07-01, 2026-12-31]`. Widening a bounded claim into an open-ended one
would assert something nobody said. An originally open-ended fact stays open-ended, and M19 adds no
second `valid_to` argument whose extension semantics would need a whole temporal-edit policy first.

**The effective date must fall inside the stretch the old fact still held.** It has to be after any
`valid_from` — a transition before the assertion began is not a transition — and, when the fact is
bounded, no later than its `valid_to`: a period that already ended is not superseded, it is history,
and guessing which was meant would silently invent one. Endpoints are inclusive, so the old row
closes on the day before.

**Both rows carry one logical `transaction_id`.** SQLite atomicity is not grouping metadata: the
sync contract defines `transaction_id` as the key that ties every row-level effect of one logical
transaction together, and `audit_mutation` mints a fresh one whenever it is not given one. Calling
it twice without passing the first id along would make replay and inspection describe one
indivisible supersession as two unrelated transactions that merely happened to commit together.

**Nothing else about the fact may move.** Person, predicate, old value, and the old row's own
provenance and `recorded_at` are preserved; only its `valid_to` changes. A caller fixing a typo, a
misidentified predicate, a wrong historical value, or a wrong endpoint is describing erroneous data
and still wants `correct_record`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Final

from pydantic import BaseModel

from people_context.app._mutation import (
    RecordNotFoundError,
    audit_mutation,
    provenance,
    require_active_person,
    snapshot,
    transactional,
    unit_of_work_for,
)
from people_context.domain.fact import Fact
from people_context.domain.shared import Confidence, Sensitivity, ValidityPeriod
from people_context.ports.audit_log import AuditLog
from people_context.ports.clock import Clock
from people_context.ports.records import RecordReader, RecordWriter
from people_context.ports.repository import PersonReader

#: The entity type this operation is defined on. M19 supersedes facts only; affiliation and
#: relationship transitions can be designed later if real usage shows they need one.
FACT_ENTITY_TYPE: Final = "fact"

#: The audit/changelog op the old row's closure is recorded under. It is deliberately not `correct`:
#: replay and inspection must be able to tell a temporal transition from an in-place repair.
SUPERSEDE_OP: Final = "supersede"

#: The one field the old row changes.
SUPERSEDED_FIELD: Final = "valid_to"

#: `effective_from` is not strictly after the old fact's `valid_from`.
REASON_NOT_AFTER_VALID_FROM: Final = "effective_from_not_after_valid_from"

#: `effective_from` falls after the old fact's `valid_to`; that period has already ended.
REASON_AFTER_VALID_TO: Final = "effective_from_after_valid_to"

#: `effective_from` is the first representable date, so the old row has no inclusive day to close on.
REASON_NO_PRIOR_DAY: Final = "effective_from_has_no_prior_day"


class InvalidSupersessionError(Exception):
    """Raised when an effective date cannot describe a transition of the named fact."""

    def __init__(self, fact_id: str, reason: str) -> None:
        self.fact_id = fact_id
        self.reason = reason
        super().__init__(f"invalid fact supersession: {reason}")


class SupersedeFactInput(BaseModel):
    """Input for one approved temporal fact transition.

    `confidence` and `sensitivity` are the replacement's own; omitting either inherits the old
    fact's value, because a transition in the world says nothing about how sure the store is or how
    disclosable the claim has become.
    """

    fact_id: str
    new_value: str
    effective_from: date
    confidence: Confidence | None = None
    sensitivity: Sensitivity | None = None
    source: str = "agent"
    session: str | None = None
    stated_by: str | None = None


class SupersedeFactResult(BaseModel):
    """Both durable rows of one supersession, and the id that groups their replay effects."""

    superseded: Fact
    replacement: Fact
    transaction_id: str


class SupersedeFact:
    """Close one still-effective fact and open its replacement in a single unit of work."""

    def __init__(
        self,
        records: RecordReader,
        writer: RecordWriter,
        audit: AuditLog,
        clock: Clock,
        *,
        people: PersonReader,
    ) -> None:
        self._records = records
        self._writer = writer
        self._audit = audit
        self._clock = clock
        self._uow = unit_of_work_for(audit)
        self._people = people

    @transactional
    def execute(self, data: SupersedeFactInput) -> SupersedeFactResult:
        """Supersede one fact, emitting both row effects under one transaction id."""
        current = self._records.get_record(FACT_ENTITY_TYPE, data.fact_id)
        if not isinstance(current, Fact):
            raise RecordNotFoundError(FACT_ENTITY_TYPE, data.fact_id)
        require_active_person(self._people, current.person_id)
        _check_transition(current, data.effective_from)

        original_valid_to = current.period.valid_to
        closed = self._writer.update_record_fields(
            FACT_ENTITY_TYPE,
            data.fact_id,
            {SUPERSEDED_FIELD: data.effective_from - timedelta(days=1)},
        )
        if not isinstance(closed, Fact):
            raise RecordNotFoundError(FACT_ENTITY_TYPE, data.fact_id)
        transaction_id = audit_mutation(
            self._audit,
            self._clock,
            op=SUPERSEDE_OP,
            entity_type=FACT_ENTITY_TYPE,
            entity_id=closed.id,
            payload={
                "before": snapshot(current),
                "after": snapshot(closed),
                "fields": [SUPERSEDED_FIELD],
            },
            replay_payload=snapshot(closed),
            changed_fields=[SUPERSEDED_FIELD],
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
        )

        replacement = Fact(
            person_id=current.person_id,
            predicate=current.predicate,
            value=data.new_value,
            period=ValidityPeriod(valid_from=data.effective_from, valid_to=original_valid_to),
            recorded_at=self._clock.now(),
            confidence=data.confidence if data.confidence is not None else current.confidence,
            sensitivity=data.sensitivity if data.sensitivity is not None else current.sensitivity,
            provenance=provenance(data.source, data.session, data.stated_by),
        )
        self._writer.save_fact(replacement)
        audit_mutation(
            self._audit,
            self._clock,
            op="create",
            entity_type=FACT_ENTITY_TYPE,
            entity_id=replacement.id,
            payload=snapshot(replacement),
            source=data.source,
            session=data.session,
            stated_by=data.stated_by,
            transaction_id=transaction_id,
        )
        return SupersedeFactResult(
            superseded=closed,
            replacement=replacement,
            transaction_id=transaction_id,
        )


def _check_transition(fact: Fact, effective_from: date) -> None:
    """Refuse an effective date that does not describe a transition of this fact.

    Each refusal is stated rather than repaired. Clamping a date into the period, or treating an
    ended period as open, would write an assertion the caller never made about a person's history.
    """
    if fact.period.valid_from is not None and effective_from <= fact.period.valid_from:
        raise InvalidSupersessionError(fact.id, REASON_NOT_AFTER_VALID_FROM)
    if fact.period.valid_to is not None and effective_from > fact.period.valid_to:
        raise InvalidSupersessionError(fact.id, REASON_AFTER_VALID_TO)
    if effective_from == date.min:
        # An unbounded-start fact admits any later date, including the first representable one,
        # which has no inclusive day before it to close the old row on.
        raise InvalidSupersessionError(fact.id, REASON_NO_PRIOR_DAY)
