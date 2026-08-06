"""Application policy for the staleness report, against a fake reader and clock."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from people_context.app.insights import (
    GetStaleRelationships,
    StaleRelationshipsError,
)
from people_context.ports.insights import RecencySignal
from tests.app.fakes import FakeClock, FakeRecencyReader

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)


def _signal(
    person_id: str,
    name: str,
    *,
    last: datetime | None = None,
    categories: tuple[str, ...] = (),
    count: int = 0,
) -> RecencySignal:
    return RecencySignal(
        person_id=person_id,
        name=name,
        categories=categories,
        last_interaction_at=last,
        interaction_count=count,
    )


def _use_case(*signals: RecencySignal, now: datetime = NOW) -> GetStaleRelationships:
    return GetStaleRelationships(FakeRecencyReader(list(signals)), FakeClock(now))


def test_threshold_boundary_includes_exact_age_and_excludes_one_day_fresher() -> None:
    exactly_ninety = _signal("A", "Alice", last=datetime(2026, 3, 3, 23, 59, tzinfo=UTC), count=3)
    eighty_nine = _signal("B", "Bob", last=datetime(2026, 3, 4, 0, 1, tzinfo=UTC), count=1)

    result = _use_case(exactly_ninety, eighty_nine).execute(threshold_days=90)

    assert [row.person_id for row in result.people] == ["A"]
    assert result.people[0].days_since == 90
    assert result.people[0].interaction_count == 3


def test_never_interacted_person_sorts_first_with_null_age() -> None:
    never = _signal("N", "Nina")
    old = _signal("O", "Omar", last=datetime(2020, 1, 1, tzinfo=UTC), count=5)

    result = _use_case(old, never).execute()

    assert [row.person_id for row in result.people] == ["N", "O"]
    assert result.people[0].last_interaction_at is None
    assert result.people[0].days_since is None
    assert result.people[1].days_since == (date(2026, 6, 1) - date(2020, 1, 1)).days


def test_future_interaction_is_not_stale_and_is_not_clamped() -> None:
    future = _signal("F", "Fay", last=datetime(2027, 1, 1, tzinfo=UTC), count=1)

    stale = _use_case(future).execute()
    everything = _use_case(future).execute(threshold_days=0)

    assert stale.people == []
    assert [row.person_id for row in everything.people] == []
    # A signed negative age can only qualify against a negative threshold, which is rejected.
    with pytest.raises(StaleRelationshipsError):
        _use_case(future).execute(threshold_days=-1)


def test_zero_threshold_includes_an_interaction_recorded_today() -> None:
    today = _signal("T", "Tomas", last=datetime(2026, 6, 1, 8, 0, tzinfo=UTC), count=2)

    result = _use_case(today).execute(threshold_days=0)

    assert [row.person_id for row in result.people] == ["T"]
    assert result.people[0].days_since == 0


def test_equal_ages_break_ties_on_normalized_name_then_id() -> None:
    same = datetime(2020, 5, 5, tzinfo=UTC)
    result = _use_case(
        _signal("Z", "Ápple", last=same, count=1),
        _signal("A", "banana", last=same, count=1),
        _signal("B", "apple", last=same, count=1),
    ).execute()

    assert [row.person_id for row in result.people] == ["B", "Z", "A"]


def test_older_interaction_sorts_before_newer_one() -> None:
    result = _use_case(
        _signal("N", "Newer", last=datetime(2025, 1, 1, tzinfo=UTC), count=1),
        _signal("O", "Older", last=datetime(2024, 1, 1, tzinfo=UTC), count=1),
    ).execute()

    assert [row.person_id for row in result.people] == ["O", "N"]


def test_age_and_order_use_the_utc_instant_not_the_local_calendar_date() -> None:
    # 20:00-05:00 on 31 May is 01:00Z on 1 June: the same UTC day as the clock, so the
    # age is zero even though the stored calendar date reads a day earlier.
    same_utc_day = datetime(2026, 5, 31, 20, 0, tzinfo=timezone(timedelta(hours=-5)))
    plainly_older = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)

    result = _use_case(
        _signal("L", "Local", last=same_utc_day, count=1),
        _signal("U", "Utc", last=plainly_older, count=1),
    ).execute(threshold_days=0)

    assert [row.person_id for row in result.people] == ["U", "L"]
    assert [row.days_since for row in result.people] == [1, 0]
    # The stored representation is reported unchanged; only comparison is normalized.
    assert result.people[1].last_interaction_at == same_utc_day


def test_naive_timestamps_are_read_as_utc_rather_than_host_local_time() -> None:
    naive = datetime(2026, 3, 1, 0, 0)
    aware = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

    result = _use_case(
        _signal("N", "Naive", last=naive, count=1),
        _signal("A", "Aware", last=aware, count=1),
    ).execute()

    assert {row.person_id: row.days_since for row in result.people} == {"N": 92, "A": 92}


def test_limit_applies_after_filtering_and_reports_truncation() -> None:
    signals = [_signal(f"P{index}", f"Person {index}") for index in range(5)]

    capped = _use_case(*signals).execute(limit=2)
    complete = _use_case(*signals).execute(limit=5)

    assert [row.person_id for row in capped.people] == ["P0", "P1"]
    assert capped.truncated is True
    assert complete.truncated is False


def test_categories_are_preserved_in_stored_order() -> None:
    result = _use_case(_signal("A", "Alice", categories=("professional", "social"))).execute()

    assert result.people[0].categories == ["professional", "social"]


def test_category_filter_is_normalized_before_reaching_the_reader() -> None:
    reader = FakeRecencyReader([_signal("A", "Alice", categories=("professional",))])
    use_case = GetStaleRelationships(reader, FakeClock(NOW))

    result = use_case.execute(category="Professional")

    assert [row.person_id for row in result.people] == ["A"]
    assert reader.calls == [(date(2026, 6, 1), "professional")]


def test_blank_category_filter_is_treated_as_no_filter() -> None:
    reader = FakeRecencyReader([_signal("A", "Alice")])
    GetStaleRelationships(reader, FakeClock(NOW)).execute(category="   ")

    assert reader.calls == [(date(2026, 6, 1), None)]


def test_reader_receives_the_clock_date_as_the_relationship_as_of_date() -> None:
    reader = FakeRecencyReader([])
    GetStaleRelationships(reader, FakeClock(datetime(2030, 2, 3, 23, 30, tzinfo=UTC))).execute()

    assert reader.calls == [(date(2030, 2, 3), None)]


@pytest.mark.parametrize("threshold_days", [-1, 36501])
def test_threshold_days_outside_the_documented_range_is_rejected(threshold_days: int) -> None:
    with pytest.raises(StaleRelationshipsError):
        _use_case().execute(threshold_days=threshold_days)


@pytest.mark.parametrize("limit", [0, 101])
def test_limit_outside_the_documented_range_is_rejected(limit: int) -> None:
    with pytest.raises(StaleRelationshipsError):
        _use_case().execute(limit=limit)


@pytest.mark.parametrize("threshold_days", [0, 36500])
def test_threshold_days_range_endpoints_are_accepted(threshold_days: int) -> None:
    assert _use_case().execute(threshold_days=threshold_days).people == []


@pytest.mark.parametrize("limit", [1, 100])
def test_limit_range_endpoints_are_accepted(limit: int) -> None:
    assert _use_case().execute(limit=limit).people == []


def test_result_serializes_to_the_documented_json_shape() -> None:
    result = _use_case(
        _signal(
            "A",
            "Alice",
            last=datetime(2026, 3, 1, tzinfo=UTC),
            categories=("professional", "social"),
            count=12,
        )
    ).execute()

    assert result.model_dump(mode="json") == {
        "people": [
            {
                "person_id": "A",
                "name": "Alice",
                "categories": ["professional", "social"],
                "last_interaction_at": "2026-03-01T00:00:00Z",
                "days_since": 92,
                "interaction_count": 12,
            }
        ],
        "truncated": False,
    }
