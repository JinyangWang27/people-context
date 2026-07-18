"""Internal graph values shared by the graph port and application use cases."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GraphPerson(BaseModel):
    """One active person discovered during graph traversal."""

    person_id: str
    name: str
    is_self: bool = False
    depth: int = 0


class GraphRelationship(BaseModel):
    """One active canonical relationship edge."""

    id: str
    subject_id: str
    object_id: str
    type: str
    label: str | None = None
    category: str = "uncategorized"


class RelationshipSubgraph(BaseModel):
    """A deterministic active-person subgraph before application-layer caps."""

    nodes: list[GraphPerson] = Field(default_factory=list)
    edges: list[GraphRelationship] = Field(default_factory=list)


class RelationshipPath(BaseModel):
    """One ordered path, including its people and connecting edges."""

    people: list[GraphPerson]
    edges: list[GraphRelationship]
