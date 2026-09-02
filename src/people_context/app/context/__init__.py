"""Person context and communication preference use cases."""

from people_context.app.context.guidance import CommunicationGuidanceResult, GetCommunicationGuidance
from people_context.app.context.models import PersonAffiliationContext, PersonRelationshipContext
from people_context.app.context.preferences import SetCommunicationPhilosophy, SetCommunicationPhilosophyInput
from people_context.app.context.query import GetPersonContext, PersonContextResult, PersonIdentity
from people_context.app.context.stats import (
    STATS_FORMAT,
    STATS_VERSION,
    CountEntry,
    EnvironmentGates,
    PeopleCounts,
    ReportStoreStats,
    StatsReport,
    StorageUsage,
    render_stats_json,
)

__all__ = [
    "STATS_FORMAT",
    "STATS_VERSION",
    "CommunicationGuidanceResult",
    "CountEntry",
    "EnvironmentGates",
    "GetCommunicationGuidance",
    "GetPersonContext",
    "PeopleCounts",
    "PersonAffiliationContext",
    "PersonContextResult",
    "PersonIdentity",
    "PersonRelationshipContext",
    "ReportStoreStats",
    "SetCommunicationPhilosophy",
    "SetCommunicationPhilosophyInput",
    "StatsReport",
    "StorageUsage",
    "render_stats_json",
]
