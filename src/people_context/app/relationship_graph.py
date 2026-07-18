"""Application use cases for bounded relationship graph traversal."""

from __future__ import annotations

from pydantic import BaseModel, Field

from people_context.app.relationship_policy import relationship_display_type
from people_context.domain.relationship_graph import GraphPerson, GraphRelationship
from people_context.domain.relationship_vocabulary import normalize_relationship_type
from people_context.ports.graph import GraphReader
from people_context.ports.relationship_vocabulary import RelationshipVocabularyReader
from people_context.ports.repository import PersonReader

MAX_GRAPH_DEPTH = 4
DEFAULT_GRAPH_DEPTH = 2
MAX_GRAPH_NODES = 100
MAX_GRAPH_EDGES = 300


class GraphTraversalError(ValueError):
    """Raised when an application traversal limit is invalid."""


class GraphPersonResult(BaseModel):
    person_id: str
    name: str
    is_self: bool


class GraphEdgeResult(BaseModel):
    subject_id: str
    object_id: str
    type: str
    label: str | None = None
    category: str


class RelationshipGraphResult(BaseModel):
    nodes: list[GraphPersonResult] = Field(default_factory=list)
    edges: list[GraphEdgeResult] = Field(default_factory=list)
    truncated: bool = False


class GraphPersonNotFound(BaseModel):
    error: str = "person_not_found"
    person_id: str


class ConnectionEdgeResult(GraphEdgeResult):
    display_type: str


class ConnectionHop(BaseModel):
    person: GraphPersonResult
    edge: ConnectionEdgeResult


class ConnectionResult(BaseModel):
    connected: bool
    hops: list[ConnectionHop] = Field(default_factory=list)
    reason: str | None = None


class GetRelationshipGraph:
    """Return a minimal-disclosure, bounded relationship subgraph."""

    def __init__(self, people: PersonReader, graph: GraphReader) -> None:
        self._people = people
        self._graph = graph

    def execute(
        self,
        person_id: str,
        *,
        depth: int = DEFAULT_GRAPH_DEPTH,
        types: list[str] | None = None,
    ) -> RelationshipGraphResult | GraphPersonNotFound:
        _validate_depth(depth)
        person = self._people.get(person_id)
        if person is None or person.deleted_at is not None:
            return GraphPersonNotFound(person_id=person_id)
        raw = self._graph.neighbors(person_id, depth)
        allowed = {normalize_relationship_type(value) for value in types} if types else None
        raw_edges = [edge for edge in raw.edges if allowed is None or edge.type in allowed]
        included_ids = {person_id}
        for edge in raw_edges:
            included_ids.update((edge.subject_id, edge.object_id))
        candidate_nodes = [node for node in raw.nodes if node.person_id in included_ids]
        selected_nodes = candidate_nodes[:MAX_GRAPH_NODES]
        selected_ids = {node.person_id for node in selected_nodes}
        candidate_edges = [
            edge
            for edge in raw_edges
            if edge.subject_id in selected_ids and edge.object_id in selected_ids
        ]
        selected_edges = candidate_edges[:MAX_GRAPH_EDGES]
        truncated = len(candidate_nodes) > len(selected_nodes) or len(candidate_edges) > len(selected_edges)
        return RelationshipGraphResult(
            nodes=[_person_result(node) for node in selected_nodes],
            edges=[_edge_result(edge) for edge in selected_edges],
            truncated=truncated,
        )


class FindConnection:
    """Return one deterministic shortest path with perspective-rendered edge types."""

    def __init__(
        self,
        people: PersonReader,
        graph: GraphReader,
        vocabulary: RelationshipVocabularyReader,
    ) -> None:
        self._people = people
        self._graph = graph
        self._vocabulary = vocabulary

    def execute(
        self,
        person_a: str,
        person_b: str,
        *,
        max_depth: int = MAX_GRAPH_DEPTH,
    ) -> ConnectionResult | GraphPersonNotFound:
        _validate_depth(max_depth)
        for person_id in (person_a, person_b):
            person = self._people.get(person_id)
            if person is None or person.deleted_at is not None:
                return GraphPersonNotFound(person_id=person_id)
        path = self._graph.path_between(person_a, person_b, max_depth)
        if path is None:
            return ConnectionResult(connected=False, reason="not_connected")
        hops: list[ConnectionHop] = []
        for index, edge in enumerate(path.edges):
            from_person = path.people[index]
            to_person = path.people[index + 1]
            hops.append(
                ConnectionHop(
                    person=_person_result(to_person),
                    edge=ConnectionEdgeResult(
                        **_edge_result(edge).model_dump(),
                        display_type=relationship_display_type(
                            edge.type,
                            queried_person_id=from_person.person_id,
                            subject_id=edge.subject_id,
                            vocabulary=self._vocabulary,
                        ),
                    ),
                )
            )
        return ConnectionResult(connected=True, hops=hops)


def _validate_depth(depth: int) -> None:
    if depth < 0 or depth > MAX_GRAPH_DEPTH:
        raise GraphTraversalError(f"depth must be between 0 and {MAX_GRAPH_DEPTH}")


def _person_result(person: GraphPerson) -> GraphPersonResult:
    return GraphPersonResult(person_id=person.person_id, name=person.name, is_self=person.is_self)


def _edge_result(edge: GraphRelationship) -> GraphEdgeResult:
    return GraphEdgeResult(
        subject_id=edge.subject_id,
        object_id=edge.object_id,
        type=edge.type,
        label=edge.label,
        category=edge.category,
    )
