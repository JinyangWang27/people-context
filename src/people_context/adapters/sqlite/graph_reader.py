"""SQLite breadth-first implementation of the relationship graph port."""

from __future__ import annotations

import sqlite3
from datetime import date

from people_context.domain.relationship_graph import (
    GraphPerson,
    GraphRelationship,
    RelationshipPath,
    RelationshipSubgraph,
)
from people_context.ports.clock import Clock


class SqliteGraphReader:
    """Traverse active, non-deleted-person relationship edges deterministically."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self._conn = conn
        self._clock = clock

    def neighbors(self, person_id: str, depth: int) -> RelationshipSubgraph:
        return self.subgraph([person_id], depth)

    def path_between(self, a: str, b: str, max_depth: int) -> RelationshipPath | None:
        as_of = self._clock.now().date()
        if a == b:
            person = self._person(a, 0)
            return RelationshipPath(people=[person], edges=[]) if person is not None else None
        if self._person(a, 0) is None or self._person(b, 0) is None:
            return None

        frontier = [a]
        visited = {a}
        parents: dict[str, tuple[str, str]] = {}
        for _ in range(max_depth):
            if not frontier:
                break
            adjacency = self._frontier_adjacency(frontier, as_of)
            next_frontier: list[str] = []
            for current in frontier:
                for neighbor, edge_id in adjacency.get(current, []):
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    parents[neighbor] = (current, edge_id)
                    if neighbor == b:
                        return self._hydrate_path(a, b, parents, as_of)
                    next_frontier.append(neighbor)
            frontier = next_frontier
        return None

    def subgraph(self, person_ids: list[str], depth: int) -> RelationshipSubgraph:
        as_of = self._clock.now().date()
        seeds = sorted(set(person_ids))
        nodes: list[GraphPerson] = []
        frontier: list[str] = []
        visited: set[str] = set()
        for person_id in seeds:
            person = self._person(person_id, 0)
            if person is None:
                continue
            nodes.append(person)
            frontier.append(person_id)
            visited.add(person_id)
        for level in range(1, depth + 1):
            if not frontier:
                break
            adjacency = self._frontier_adjacency(frontier, as_of)
            next_frontier: list[str] = []
            for current in frontier:
                for neighbor, _ in adjacency.get(current, []):
                    if neighbor in visited:
                        continue
                    person = self._person(neighbor, level)
                    if person is None:
                        continue
                    visited.add(neighbor)
                    next_frontier.append(neighbor)
                    nodes.append(person)
            frontier = next_frontier
        if not nodes:
            return RelationshipSubgraph()
        return RelationshipSubgraph(
            nodes=nodes,
            edges=self._edges_within([node.person_id for node in nodes], as_of),
        )

    def _frontier_adjacency(self, frontier: list[str], as_of: date) -> dict[str, list[tuple[str, str]]]:
        if not frontier:
            return {}
        placeholders = ", ".join("?" for _ in frontier)
        rows = self._conn.execute(
            f"""
            SELECT r.id, r.subject_id, r.object_id
            FROM relationships r
            JOIN persons subject ON subject.id = r.subject_id AND subject.deleted_at IS NULL
            JOIN persons object ON object.id = r.object_id AND object.deleted_at IS NULL
            WHERE (r.subject_id IN ({placeholders}) OR r.object_id IN ({placeholders}))
              AND (r.valid_from IS NULL OR r.valid_from <= ?)
              AND (r.valid_to IS NULL OR r.valid_to >= ?)
            ORDER BY r.id
            """,  # noqa: S608 - placeholders are generated; all values remain bound
            [*frontier, *frontier, as_of.isoformat(), as_of.isoformat()],
        ).fetchall()
        frontier_set = set(frontier)
        adjacency: dict[str, list[tuple[str, str]]] = {person_id: [] for person_id in frontier}
        for row in rows:
            if row["subject_id"] in frontier_set:
                adjacency[row["subject_id"]].append((row["object_id"], row["id"]))
            if row["object_id"] in frontier_set:
                adjacency[row["object_id"]].append((row["subject_id"], row["id"]))
        return adjacency

    def _edges_within(self, node_ids: list[str], as_of: date) -> list[GraphRelationship]:
        placeholders = ", ".join("?" for _ in node_ids)
        rows = self._conn.execute(
            f"""
            SELECT r.id, r.subject_id, r.object_id, r.type, r.label,
                   COALESCE(rt.category, 'uncategorized') AS category
            FROM relationships r
            JOIN persons subject ON subject.id = r.subject_id AND subject.deleted_at IS NULL
            JOIN persons object ON object.id = r.object_id AND object.deleted_at IS NULL
            LEFT JOIN relationship_types rt ON rt.type = r.type
            WHERE r.subject_id IN ({placeholders})
              AND r.object_id IN ({placeholders})
              AND (r.valid_from IS NULL OR r.valid_from <= ?)
              AND (r.valid_to IS NULL OR r.valid_to >= ?)
            ORDER BY r.id
            """,  # noqa: S608 - placeholders are generated; all values remain bound
            [*node_ids, *node_ids, as_of.isoformat(), as_of.isoformat()],
        ).fetchall()
        return [_graph_edge(row) for row in rows]

    def _hydrate_path(
        self,
        start: str,
        target: str,
        parents: dict[str, tuple[str, str]],
        as_of: date,
    ) -> RelationshipPath | None:
        person_ids = [target]
        edge_ids: list[str] = []
        current = target
        while current != start:
            parent = parents.get(current)
            if parent is None:
                return None
            previous, edge_id = parent
            person_ids.append(previous)
            edge_ids.append(edge_id)
            current = previous
        person_ids.reverse()
        edge_ids.reverse()
        people = [self._person(person_id, index) for index, person_id in enumerate(person_ids)]
        edges = [self._edge(edge_id, as_of) for edge_id in edge_ids]
        if any(person is None for person in people) or any(edge is None for edge in edges):
            return None
        return RelationshipPath(
            people=[person for person in people if person is not None],
            edges=[edge for edge in edges if edge is not None],
        )

    def _person(self, person_id: str, depth: int) -> GraphPerson | None:
        row = self._conn.execute(
            "SELECT id, canonical_name, is_self FROM persons WHERE id = ? AND deleted_at IS NULL",
            (person_id,),
        ).fetchone()
        if row is None:
            return None
        return GraphPerson(
            person_id=row["id"],
            name=row["canonical_name"],
            is_self=bool(row["is_self"]),
            depth=depth,
        )

    def _edge(self, edge_id: str, as_of: date) -> GraphRelationship | None:
        row = self._conn.execute(
            """
            SELECT r.id, r.subject_id, r.object_id, r.type, r.label,
                   COALESCE(rt.category, 'uncategorized') AS category
            FROM relationships r
            JOIN persons subject ON subject.id = r.subject_id AND subject.deleted_at IS NULL
            JOIN persons object ON object.id = r.object_id AND object.deleted_at IS NULL
            LEFT JOIN relationship_types rt ON rt.type = r.type
            WHERE r.id = ?
              AND (r.valid_from IS NULL OR r.valid_from <= ?)
              AND (r.valid_to IS NULL OR r.valid_to >= ?)
            """,
            (edge_id, as_of.isoformat(), as_of.isoformat()),
        ).fetchone()
        return _graph_edge(row) if row is not None else None


def _graph_edge(row: sqlite3.Row) -> GraphRelationship:
    return GraphRelationship(
        id=row["id"],
        subject_id=row["subject_id"],
        object_id=row["object_id"],
        type=row["type"],
        label=row["label"],
        category=row["category"],
    )
