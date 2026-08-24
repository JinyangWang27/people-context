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

#: Bound values bound into a single statement, well under the 999-variable limit of
#: SQLite builds older than 3.32. An `IN (...)` clause built from a node set is chunked
#: to this size, because the traversal's node count is data-dependent and a store large
#: enough to exceed the limit would otherwise fail the query outright.
_SQL_VARIABLE_CHUNK = 400


class SqliteGraphReader:
    """Traverse active, non-deleted-person relationship edges deterministically."""

    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self._conn = conn
        self._clock = clock

    def neighbors(self, person_id: str, depth: int, node_budget: int | None = None) -> RelationshipSubgraph:
        return self.subgraph([person_id], depth, node_budget)

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

    def subgraph(
        self, person_ids: list[str], depth: int, node_budget: int | None = None
    ) -> RelationshipSubgraph:
        """Breadth-first expansion bounded by `depth` and, optionally, a node budget.

        The budget bounds the work, not the disclosure: callers cap what they return
        separately, after any type filtering, so stopping the traversal at the caller's
        display cap would discard nodes a filtered query should still be able to reach.
        Reaching it is reported as `truncated` so the caller can say so.
        """
        as_of = self._clock.now().date()
        seeds = sorted(set(person_ids))
        truncated = False
        nodes = self._people(seeds, depth=0)
        visited = {node.person_id for node in nodes}
        frontier = [node.person_id for node in nodes]
        for level in range(1, depth + 1):
            if not frontier:
                break
            adjacency = self._frontier_adjacency(frontier, as_of)
            discovered: list[str] = []
            for current in frontier:
                for neighbor, _ in adjacency.get(current, []):
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    discovered.append(neighbor)
            if node_budget is not None and len(nodes) + len(discovered) > node_budget:
                discovered = discovered[: max(node_budget - len(nodes), 0)]
                truncated = True
            # One hydration query per level rather than one per neighbour: a dense store
            # at depth 4 otherwise issues a round trip for every person it reaches.
            level_nodes = self._people(discovered, depth=level)
            nodes.extend(level_nodes)
            frontier = [node.person_id for node in level_nodes]
            if truncated:
                break
        if not nodes:
            return RelationshipSubgraph()
        return RelationshipSubgraph(
            nodes=nodes,
            edges=self._edges_within([node.person_id for node in nodes], as_of),
            truncated=truncated,
        )

    def _people(self, person_ids: list[str], *, depth: int) -> list[GraphPerson]:
        """Hydrate active persons in one statement per chunk, in the given order."""
        if not person_ids:
            return []
        found: dict[str, sqlite3.Row] = {}
        for chunk in _chunked(person_ids):
            placeholders = ", ".join("?" for _ in chunk)
            rows = self._conn.execute(
                f"SELECT id, canonical_name, is_self FROM persons "  # noqa: S608 - placeholders only
                f"WHERE id IN ({placeholders}) AND deleted_at IS NULL",
                chunk,
            ).fetchall()
            found.update({row["id"]: row for row in rows})
        return [_graph_person(found[person_id], depth) for person_id in person_ids if person_id in found]

    def _frontier_adjacency(self, frontier: list[str], as_of: date) -> dict[str, list[tuple[str, str]]]:
        # Rows are collected by edge id across chunks before adjacency is built: an edge
        # whose two endpoints land in different chunks is returned by both queries, and
        # counting it twice would put a duplicate neighbour in the frontier.
        rows: dict[str, sqlite3.Row] = {}
        for chunk in _chunked(frontier):
            placeholders = ", ".join("?" for _ in chunk)
            for row in self._conn.execute(
                f"""
                SELECT r.id, r.subject_id, r.object_id
                FROM relationships r
                JOIN persons subject ON subject.id = r.subject_id AND subject.deleted_at IS NULL
                JOIN persons object ON object.id = r.object_id AND object.deleted_at IS NULL
                WHERE (r.subject_id IN ({placeholders}) OR r.object_id IN ({placeholders}))
                  AND (r.valid_from IS NULL OR r.valid_from <= ?)
                  AND (r.valid_to IS NULL OR r.valid_to >= ?)
                """,  # noqa: S608 - placeholders are generated; all values remain bound
                [*chunk, *chunk, as_of.isoformat(), as_of.isoformat()],
            ).fetchall():
                rows[row["id"]] = row
        frontier_set = set(frontier)
        adjacency: dict[str, list[tuple[str, str]]] = {person_id: [] for person_id in frontier}
        for edge_id in sorted(rows):
            row = rows[edge_id]
            if row["subject_id"] in frontier_set:
                adjacency[row["subject_id"]].append((row["object_id"], row["id"]))
            if row["object_id"] in frontier_set:
                adjacency[row["object_id"]].append((row["subject_id"], row["id"]))
        return adjacency

    def _edges_within(self, node_ids: list[str], as_of: date) -> list[GraphRelationship]:
        # Both sides are chunked, so a node set larger than one chunk needs the cross
        # product of chunk pairs to see every edge. The chunks partition the node set,
        # so each edge is still matched by exactly one pair.
        rows: dict[str, sqlite3.Row] = {}
        for subject_chunk in _chunked(node_ids):
            subject_placeholders = ", ".join("?" for _ in subject_chunk)
            for object_chunk in _chunked(node_ids):
                object_placeholders = ", ".join("?" for _ in object_chunk)
                for row in self._conn.execute(
                    f"""
                    SELECT r.id, r.subject_id, r.object_id, r.type, r.label,
                           COALESCE(rt.category, 'uncategorized') AS category
                    FROM relationships r
                    JOIN persons subject ON subject.id = r.subject_id AND subject.deleted_at IS NULL
                    JOIN persons object ON object.id = r.object_id AND object.deleted_at IS NULL
                    LEFT JOIN relationship_types rt ON rt.type = r.type
                    WHERE r.subject_id IN ({subject_placeholders})
                      AND r.object_id IN ({object_placeholders})
                      AND (r.valid_from IS NULL OR r.valid_from <= ?)
                      AND (r.valid_to IS NULL OR r.valid_to >= ?)
                    """,  # noqa: S608 - placeholders are generated; all values remain bound
                    [*subject_chunk, *object_chunk, as_of.isoformat(), as_of.isoformat()],
                ).fetchall():
                    rows[row["id"]] = row
        return [_graph_edge(rows[edge_id]) for edge_id in sorted(rows)]

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
        people = self._people([person_id], depth=depth)
        return people[0] if people else None

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


def _chunked(values: list[str]) -> list[list[str]]:
    """Split `values` into slices small enough to bind in one statement."""
    return [values[start : start + _SQL_VARIABLE_CHUNK] for start in range(0, len(values), _SQL_VARIABLE_CHUNK)]


def _graph_person(row: sqlite3.Row, depth: int) -> GraphPerson:
    return GraphPerson(
        person_id=row["id"],
        name=row["canonical_name"],
        is_self=bool(row["is_self"]),
        depth=depth,
    )


def _graph_edge(row: sqlite3.Row) -> GraphRelationship:
    return GraphRelationship(
        id=row["id"],
        subject_id=row["subject_id"],
        object_id=row["object_id"],
        type=row["type"],
        label=row["label"],
        category=row["category"],
    )
