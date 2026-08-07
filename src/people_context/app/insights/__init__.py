"""Read-only reports derived from stored signal, without recording new data."""

from people_context.app.insights.stale import (
    DEFAULT_STALE_LIMIT,
    DEFAULT_THRESHOLD_DAYS,
    MAX_STALE_LIMIT,
    MAX_THRESHOLD_DAYS,
    MIN_STALE_LIMIT,
    MIN_THRESHOLD_DAYS,
    GetStaleRelationships,
    StalePersonResult,
    StaleRelationshipsError,
    StaleRelationshipsResult,
)
from people_context.app.insights.upcoming import (
    BIRTHDAY_LABEL,
    BIRTHDAY_PREDICATE,
    DEFAULT_WINDOW_DAYS,
    MAX_WINDOW_DAYS,
    MIN_WINDOW_DAYS,
    ListUpcomingDates,
    UpcomingDateEntry,
    UpcomingDateKind,
    UpcomingDatesError,
    UpcomingDatesResult,
)

__all__ = [
    "BIRTHDAY_LABEL",
    "BIRTHDAY_PREDICATE",
    "DEFAULT_STALE_LIMIT",
    "DEFAULT_THRESHOLD_DAYS",
    "DEFAULT_WINDOW_DAYS",
    "MAX_STALE_LIMIT",
    "MAX_THRESHOLD_DAYS",
    "MAX_WINDOW_DAYS",
    "MIN_STALE_LIMIT",
    "MIN_THRESHOLD_DAYS",
    "MIN_WINDOW_DAYS",
    "GetStaleRelationships",
    "ListUpcomingDates",
    "StalePersonResult",
    "StaleRelationshipsError",
    "StaleRelationshipsResult",
    "UpcomingDateEntry",
    "UpcomingDateKind",
    "UpcomingDatesError",
    "UpcomingDatesResult",
]
