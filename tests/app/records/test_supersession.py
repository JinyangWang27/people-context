"""Application policy for atomic fact supersession (M19.2), against in-memory ports.

Supersession is the one M19 mutation, and what it must never do is as important as what it does: it
must not overwrite a value that was historically correct, must not widen a bounded assertion into an
open-ended one, and must not describe one indivisible transition as two unrelated changes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from people_context.app.records import (
    REASON_AFTER_VALID_TO,
    REASON_NO_PRIOR_DAY,
    REASON_NOT_AFTER_VALID_FROM,
    SUPERSEDE_OP,
    InvalidSupersessionError,
    PersonNotFoundError,
    RecordFact,
    RecordFactInput,
    RecordNotFoundError,
    SupersedeFact,
    SupersedeFactInput,
)
from people_context.domain.fact import Fact
from people_context.domain.person import Person
from people_context.domain.shared import Sensitivity
from tests.app.fakes import FakeAuditLog, FakeClock, FakePeopleRepository, FakeRecordStore

_NOW = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
_LATER = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


class _Store:
    """A person with one recorded fact, and the two use cases under test."""

    def __init__(self) -> None:
        self.people = FakePeopleRepository()
        self.records = FakeRecordStore()
        self.audit = FakeAuditLog()
        self.clock = FakeClock(_NOW)
        self.alice = Person(id="P1", canonical_name="Alice")
        self.people.save_person(self.alice)
        self.record_fact = RecordFact(self.people, self.records, self.audit, self.clock)
        self.supersede = SupersedeFact(self.records, self.records, self.audit, self.clock, people=self.people)

    def fact(
        self,
        *,
        value: str = "Acme",
        predicate: str = "employer",
        valid_from: date | None = None,
        valid_to: date | None = None,
        confidence: float | None = None,
        sensitivity: Sensitivity = Sensitivity.PERSONAL,
    ) -> Fact:
        return self.record_fact.execute(
            RecordFactInput(
                person_id=self.alice.id,
                predicate=predicate,
                value=value,
                valid_from=valid_from,
                valid_to=valid_to,
                confidence=confidence,
                sensitivity=sensitivity,
                source="agent",
            )
        )

    def stored(self, fact_id: str) -> Fact:
        record = self.records.get_record("fact", fact_id)
        assert isinstance(record, Fact)
        return record


class TestBoundaryMath:
    """Endpoints are inclusive, so the old row closes on the day before the transition."""

    def test_an_open_ended_fact_closes_the_day_before_and_stays_open_ended(self) -> None:
        store = _Store()
        original = store.fact(value="Acme", valid_from=date(2024, 1, 1))

        result = store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
        )

        assert result.superseded.period.valid_from == date(2024, 1, 1)
        assert result.superseded.period.valid_to == date(2026, 6, 30)
        assert result.replacement.period.valid_from == date(2026, 7, 1)
        assert result.replacement.period.valid_to is None

    def test_a_bounded_fact_hands_its_original_endpoint_to_the_replacement(self) -> None:
        """`[2026-01-01, 2026-12-31]` superseded on 1 July must not become open-ended."""
        store = _Store()
        original = store.fact(valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31))

        result = store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
        )

        assert (result.superseded.period.valid_from, result.superseded.period.valid_to) == (
            date(2026, 1, 1),
            date(2026, 6, 30),
        )
        assert (result.replacement.period.valid_from, result.replacement.period.valid_to) == (
            date(2026, 7, 1),
            date(2026, 12, 31),
        )

    def test_superseding_on_the_last_day_leaves_a_one_day_replacement(self) -> None:
        store = _Store()
        original = store.fact(valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31))

        result = store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 12, 31))
        )

        assert result.superseded.period.valid_to == date(2026, 12, 30)
        assert (result.replacement.period.valid_from, result.replacement.period.valid_to) == (
            date(2026, 12, 31),
            date(2026, 12, 31),
        )

    def test_a_fact_with_no_start_date_may_still_be_superseded(self) -> None:
        store = _Store()
        original = store.fact()

        result = store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
        )

        assert result.superseded.period.valid_from is None
        assert result.superseded.period.valid_to == date(2026, 6, 30)
        assert result.replacement.period.valid_from == date(2026, 7, 1)

    def test_a_leap_day_transition_closes_on_the_previous_calendar_day(self) -> None:
        store = _Store()
        original = store.fact(valid_from=date(2024, 1, 1))

        result = store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2024, 3, 1))
        )

        assert result.superseded.period.valid_to == date(2024, 2, 29)


class TestPreservation:
    """The old assertion keeps everything but its end date."""

    def test_the_old_row_keeps_its_person_predicate_value_provenance_and_recorded_time(self) -> None:
        store = _Store()
        original = store.fact(value="Acme", confidence=0.8, sensitivity=Sensitivity.SENSITIVE)
        store.clock.set(_LATER)

        result = store.supersede.execute(
            SupersedeFactInput(
                fact_id=original.id,
                new_value="Globex",
                effective_from=date(2026, 7, 1),
                source="operator",
                session="s-1",
            )
        )

        closed = result.superseded
        assert closed.id == original.id
        assert (closed.person_id, closed.predicate, closed.value) == (original.person_id, "employer", "Acme")
        assert closed.provenance == original.provenance
        assert closed.recorded_at == original.recorded_at == _NOW
        assert closed.confidence == 0.8
        assert closed.sensitivity == Sensitivity.SENSITIVE

    def test_the_replacement_keeps_the_person_and_predicate_and_takes_the_new_value(self) -> None:
        store = _Store()
        original = store.fact(predicate="employer", value="Acme")

        result = store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
        )

        assert result.replacement.id != original.id
        assert (result.replacement.person_id, result.replacement.predicate) == (store.alice.id, "employer")
        assert result.replacement.value == "Globex"

    def test_the_replacement_carries_provenance_from_the_approved_call(self) -> None:
        store = _Store()
        original = store.fact()
        store.clock.set(_LATER)

        result = store.supersede.execute(
            SupersedeFactInput(
                fact_id=original.id,
                new_value="Globex",
                effective_from=date(2026, 7, 1),
                source="operator",
                session="s-9",
                stated_by="alice",
            )
        )

        assert result.replacement.provenance.source == "operator"
        assert result.replacement.provenance.session == "s-9"
        assert result.replacement.provenance.stated_by == "alice"
        # The replacement is a new assertion, so it is recorded now rather than backdated.
        assert result.replacement.recorded_at == _LATER

    def test_confidence_and_sensitivity_are_inherited_unless_supplied(self) -> None:
        store = _Store()
        original = store.fact(confidence=0.4, sensitivity=Sensitivity.SENSITIVE)

        inherited = store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
        ).replacement

        assert inherited.confidence == 0.4
        assert inherited.sensitivity == Sensitivity.SENSITIVE

    def test_explicit_confidence_and_sensitivity_override_the_inherited_values(self) -> None:
        store = _Store()
        original = store.fact(confidence=0.4, sensitivity=Sensitivity.SENSITIVE)

        replacement = store.supersede.execute(
            SupersedeFactInput(
                fact_id=original.id,
                new_value="Globex",
                effective_from=date(2026, 7, 1),
                confidence=0.9,
                sensitivity=Sensitivity.PERSONAL,
            )
        ).replacement

        assert replacement.confidence == 0.9
        assert replacement.sensitivity == Sensitivity.PERSONAL

    def test_both_rows_are_durable_after_the_call(self) -> None:
        store = _Store()
        original = store.fact()

        result = store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
        )

        assert store.stored(original.id).value == "Acme"
        assert store.stored(result.replacement.id).value == "Globex"


class TestRefusals:
    """A date that cannot describe a transition is refused, never repaired."""

    def test_an_effective_date_on_the_start_day_is_refused(self) -> None:
        store = _Store()
        original = store.fact(valid_from=date(2026, 1, 1))

        with pytest.raises(InvalidSupersessionError) as raised:
            store.supersede.execute(
                SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 1, 1))
            )

        assert raised.value.reason == REASON_NOT_AFTER_VALID_FROM
        assert raised.value.fact_id == original.id

    def test_an_effective_date_before_the_start_day_is_refused(self) -> None:
        store = _Store()
        original = store.fact(valid_from=date(2026, 1, 1))

        with pytest.raises(InvalidSupersessionError) as raised:
            store.supersede.execute(
                SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2025, 6, 1))
            )

        assert raised.value.reason == REASON_NOT_AFTER_VALID_FROM

    def test_a_single_day_fact_cannot_be_superseded(self) -> None:
        """There is no day inside `[d, d]` that is both after the start and not after the end."""
        store = _Store()
        original = store.fact(valid_from=date(2026, 5, 5), valid_to=date(2026, 5, 5))

        with pytest.raises(InvalidSupersessionError) as raised:
            store.supersede.execute(
                SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 5, 5))
            )

        assert raised.value.reason == REASON_NOT_AFTER_VALID_FROM

    def test_an_already_ended_period_is_refused_rather_than_reopened(self) -> None:
        store = _Store()
        original = store.fact(valid_from=date(2020, 1, 1), valid_to=date(2020, 12, 31))

        with pytest.raises(InvalidSupersessionError) as raised:
            store.supersede.execute(
                SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
            )

        assert raised.value.reason == REASON_AFTER_VALID_TO

    def test_the_first_representable_date_has_no_day_to_close_on(self) -> None:
        store = _Store()
        original = store.fact()

        with pytest.raises(InvalidSupersessionError) as raised:
            store.supersede.execute(
                SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date.min)
            )

        assert raised.value.reason == REASON_NO_PRIOR_DAY

    def test_an_unknown_fact_is_reported_as_a_missing_record(self) -> None:
        store = _Store()

        with pytest.raises(RecordNotFoundError) as raised:
            store.supersede.execute(
                SupersedeFactInput(fact_id="missing", new_value="Globex", effective_from=date(2026, 7, 1))
            )

        assert (raised.value.entity_type, raised.value.entity_id) == ("fact", "missing")

    def test_a_row_that_vanishes_before_the_closure_is_reported_as_a_missing_record(self) -> None:
        """The write follows the read, so the row can be gone by the time the update lands.

        The guard exists so that case is reported as the missing record it is, rather than
        continuing into a replacement built from a row nothing closed.
        """
        store = _Store()
        original = store.fact()
        store.records.update_record_fields = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

        with pytest.raises(RecordNotFoundError) as raised:
            store.supersede.execute(
                SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
            )

        assert (raised.value.entity_type, raised.value.entity_id) == ("fact", original.id)
        # Nothing was recorded for a closure that never happened.
        assert [entry.op for entry in store.audit.entries] == ["create"]

    def test_a_stored_record_that_is_not_a_fact_is_refused(self) -> None:
        """Only a fact may be superseded; anything else is reported missing rather than coerced."""
        store = _Store()
        store.records.records[("fact", "odd")] = object()

        with pytest.raises(RecordNotFoundError):
            store.supersede.execute(
                SupersedeFactInput(fact_id="odd", new_value="Globex", effective_from=date(2026, 7, 1))
            )

    def test_a_fact_of_a_removed_person_is_refused(self) -> None:
        store = _Store()
        original = store.fact()
        store.people.save_person(store.alice.model_copy(update={"deleted_at": _LATER}))

        with pytest.raises(PersonNotFoundError):
            store.supersede.execute(
                SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
            )

    def test_a_refusal_leaves_the_fact_and_the_audit_untouched(self) -> None:
        store = _Store()
        original = store.fact(valid_from=date(2026, 1, 1))
        audit_before = len(store.audit.entries)

        with pytest.raises(InvalidSupersessionError):
            store.supersede.execute(
                SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 1, 1))
            )

        assert store.stored(original.id).period.valid_to is None
        assert len(store.audit.entries) == audit_before
        assert [key for key in store.records.records if key[0] == "fact"] == [("fact", original.id)]


class TestAudit:
    """Two row effects, one transition, and a closure that never looks like a correction."""

    def test_the_closure_and_the_creation_are_audited_as_distinct_operations(self) -> None:
        store = _Store()
        original = store.fact()

        result = store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
        )

        supersession = [entry for entry in store.audit.entries if entry.op != "create"]
        creations = [entry for entry in store.audit.entries if entry.op == "create"]
        assert [entry.op for entry in supersession] == [SUPERSEDE_OP]
        assert supersession[0].entity_id == original.id
        assert [entry.entity_id for entry in creations] == [original.id, result.replacement.id]

    def test_the_closure_is_not_recorded_as_a_value_correction(self) -> None:
        """`correct_record` says the stored value was wrong; supersession says it was right."""
        store = _Store()
        original = store.fact(value="Acme")

        store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
        )

        closure = next(entry for entry in store.audit.entries if entry.op == SUPERSEDE_OP)
        assert closure.op != "correct"
        assert closure.payload["fields"] == ["valid_to"]
        assert closure.payload["before"]["value"] == closure.payload["after"]["value"] == "Acme"
        assert closure.payload["before"]["period"]["valid_to"] is None
        assert closure.payload["after"]["period"]["valid_to"] == "2026-06-30"

    def test_the_result_reports_the_shared_transaction_id(self) -> None:
        store = _Store()
        original = store.fact()

        result = store.supersede.execute(
            SupersedeFactInput(fact_id=original.id, new_value="Globex", effective_from=date(2026, 7, 1))
        )

        assert result.transaction_id
