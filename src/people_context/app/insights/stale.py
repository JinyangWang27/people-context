"""Explainable staleness report over stored interaction recency."""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic import BaseModel, Field

from people_context.domain.relationship_vocabulary import normalize_relationship_type
from people_context.domain.shared import normalize_name
from people_context.ports.clock import Clock
from people_context.ports.insights import RecencyReader, RecencySignal

MIN_THRESHOLD_DAYS = 0
MAX_THRESHOLD_DAYS = 36500
DEFAULT_THRESHOLD_DAYS = 90
MIN_STALE_LIMIT = 1
MAX_STALE_LIMIT = 100
DEFAULT_STALE_LIMIT = 20


class StaleRelationshipsError(ValueError):
    """Raised when a staleness parameter falls outside its documented range."""


class StalePersonResult(BaseModel):
    """One person whose ordinary interaction recency reached the threshold."""

    person_id: str
    name: str
    categories: list[str] = Field(default_factory=list)
    last_interaction_at: datetime | None = None
    days_since: int | None = None
    interaction_count: int = 0


class StaleRelationshipsResult(BaseModel):
    """The capped, deterministically ordered staleness report."""

    people: list[StalePersonResult] = Field(default_factory=list)
    truncated: bool = False


class GetStaleRelationships:
    """Apply age, threshold, ordering, and cap policy to stored recency signal.

    The reader supplies stored aggregates only; every time-dependent decision uses the
    injected clock so the report is deterministic under a fake clock.
    """

    def __init__(self, recency: RecencyReader, clock: Clock) -> None:
        self._recency = recency
        self._clock = clock

    def execute(
        self,
        *,
        category: str | None = None,
        threshold_days: int = DEFAULT_THRESHOLD_DAYS,
        limit: int = DEFAULT_STALE_LIMIT,
    ) -> StaleRelationshipsResult:
        """Return people with no ordinary interaction, or one at least `threshold_days` old."""
        _validate(threshold_days, limit)
        today = self._clock.now().astimezone(UTC).date()
        signals = self._recency.list_recency_signals(as_of=today, category=_canonical_category(category))
        qualifying: list[StalePersonResult] = []
        for signal in signals:
            days_since = _days_since(signal, today)
            # A future interaction yields a negative age; it is neither clamped nor stale.
            if days_since is not None and days_since < threshold_days:
                continue
            qualifying.append(
                StalePersonResult(
                    person_id=signal.person_id,
                    name=signal.name,
                    categories=list(signal.categories),
                    last_interaction_at=signal.last_interaction_at,
                    days_since=days_since,
                    interaction_count=signal.interaction_count,
                )
            )
        qualifying.sort(key=_sort_key)
        selected = qualifying[:limit]
        return StaleRelationshipsResult(people=selected, truncated=len(qualifying) > len(selected))


def _validate(threshold_days: int, limit: int) -> None:
    if threshold_days < MIN_THRESHOLD_DAYS or threshold_days > MAX_THRESHOLD_DAYS:
        raise StaleRelationshipsError(
            f"threshold_days must be between {MIN_THRESHOLD_DAYS} and {MAX_THRESHOLD_DAYS}"
        )
    if limit < MIN_STALE_LIMIT or limit > MAX_STALE_LIMIT:
        raise StaleRelationshipsError(f"limit must be between {MIN_STALE_LIMIT} and {MAX_STALE_LIMIT}")


def _canonical_category(category: str | None) -> str | None:
    if category is None:
        return None
    normalized = normalize_relationship_type(category)
    return normalized or None


def _as_utc(value: datetime) -> datetime:
    """Return one comparable UTC instant for a stored timestamp.

    Stored interaction timestamps keep whatever offset the writer supplied, and some
    legacy rows are naive. Ages and ordering are measured against the UTC clock, so an
    aware value is converted and a naive one is read as UTC. The host timezone is never
    consulted, which is what `astimezone()` on a naive value would do.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _days_since(signal: RecencySignal, today: date) -> int | None:
    """Return signed calendar days since the last ordinary interaction, if any."""
    if signal.last_interaction_at is None:
        return None
    return (today - _as_utc(signal.last_interaction_at).date()).days


def _sort_key(row: StalePersonResult) -> tuple[int, datetime, str, str]:
    """Order never-interacted people first, then oldest interaction, name, and id."""
    return (
        0 if row.last_interaction_at is None else 1,
        datetime.min.replace(tzinfo=UTC) if row.last_interaction_at is None else _as_utc(row.last_interaction_at),
        normalize_name(row.name),
        row.person_id,
    )
