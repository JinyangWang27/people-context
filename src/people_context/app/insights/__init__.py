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

__all__ = [
    "DEFAULT_STALE_LIMIT",
    "DEFAULT_THRESHOLD_DAYS",
    "MAX_STALE_LIMIT",
    "MAX_THRESHOLD_DAYS",
    "MIN_STALE_LIMIT",
    "MIN_THRESHOLD_DAYS",
    "GetStaleRelationships",
    "StalePersonResult",
    "StaleRelationshipsError",
    "StaleRelationshipsResult",
]
