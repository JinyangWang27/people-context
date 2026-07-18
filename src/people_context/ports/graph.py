"""Narrow read port for relationship graph traversal."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from people_context.domain.relationship_graph import RelationshipPath, RelationshipSubgraph


@runtime_checkable
class GraphReader(Protocol):
    """Traverse active relationship structure without disclosing person context."""

    def neighbors(self, person_id: str, depth: int) -> RelationshipSubgraph: ...

    def path_between(self, a: str, b: str, max_depth: int) -> RelationshipPath | None: ...

    def subgraph(self, person_ids: list[str], depth: int) -> RelationshipSubgraph: ...
