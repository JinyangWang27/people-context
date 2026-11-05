"""Opt-in narrowing of the advertised MCP tool surface.

Every tool schema is re-sent to the model with every request, so the size of the advertised surface is a
per-turn token cost that has nothing to do with the work being done. The `core` set keeps the tools the
everyday workflows use — identify, read, capture, correct, forget, the staged-import review gate, and
reminders — and leaves the specialised ones out. The default stays `all`: removing a tool from the default
surface is a breaking change under docs/compatibility.md, so narrowing is something an integration opts into.

Tool sets only ever subtract ordinary tools. Operator-elevated tools keep their own process-environment gate
and are never added or removed here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

__all__ = [
    "CORE_TOOLS",
    "EXTENDED_TOOLS",
    "OPERATOR_ELEVATED_TOOLS",
    "ToolSet",
    "apply_tool_set",
]


class ToolSet(StrEnum):
    """The advertised MCP tool surfaces an operator can choose between."""

    ALL = "all"
    CORE = "core"


#: Tools advertised in every tool set: the reads and writes the shipped skills and the server guide rely on,
#: plus `forget`, so a person can always ask for erasure from a narrowed session.
CORE_TOOLS: frozenset[str] = frozenset(
    {
        # Identify and read.
        "resolve_person",
        "search_people",
        "get_person_context",
        "get_person_timeline",
        "get_communication_guidance",
        "upcoming_dates",
        # Capture and correct.
        "remember",
        "remember_person",
        "add_alias",
        "update_alias",
        "remove_alias",
        "supersede_fact",
        "correct_record",
        "set_communication_philosophy",
        "forget",
        # Staged extraction behind the review gate.
        "stage_candidates",
        "review_import",
        "amend_candidate",
        "withdraw_candidates",
        "commit_import",
        # Reminders.
        "set_reminder",
        "list_reminders",
        "complete_reminder",
    }
)

#: Tools advertised only in the `all` set. Most specialise a capture `remember` already covers, or serve
#: graph, group, consolidation, semantic, and import-amendment workflows.
EXTENDED_TOOLS: frozenset[str] = frozenset(
    {
        "record_fact",
        "record_trait",
        "record_observation",
        "record_interaction",
        "set_affiliation",
        "set_relationship",
        "merge_people",
        "import_content",
        "get_relationship_graph",
        "find_connection",
        "explain_shared_connections",
        "get_stale_relationships",
        "get_consolidation_context",
        "semantic_search",
        "create_group",
        "find_groups",
        "get_group",
        "add_group_membership",
        "close_group_membership",
        "list_group_memberships",
    }
)

#: Tools registered only when the operator sets their process environment flag. Tool sets never touch them.
OPERATOR_ELEVATED_TOOLS: frozenset[str] = frozenset({"get_sensitive_person_context", "export_data"})


def apply_tool_set(mcp: MCPServer, tool_set: ToolSet) -> None:
    """Remove the tools the chosen set does not advertise from an already-registered server."""
    if tool_set is ToolSet.ALL:
        return
    for name in sorted(EXTENDED_TOOLS):
        mcp.remove_tool(name)
