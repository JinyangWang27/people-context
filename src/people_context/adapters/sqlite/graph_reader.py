"""SQLite recursive-CTE implementation of the relationship graph port."""

from __future__ import annotations

import sqlite3
from datetime import date

from people_context.domain.relationship_graph import (
    GraphPerson,
    GraphRelationship,
    RelationshipPath,
    RelationshipSubgraph,
)

_SEPARATOR = "\x1f"


class SqliteGraphReader:
    """Traverse active, non-deleted-person relationship edges deterministically."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def neighbors(self, person_id: str, depth: int) -> RelationshipSubgraph:
        return self.subgraph([person_id], depth)

    def path_between(self, a: str, b: str, max_depth: int) -> RelationshipPath | None:
        if a == b:
            person = self._person(a, 0)
            return RelationshipPath(people=[person], edges=[]) if person is not None else None
        row = self._conn.execute(
            """
            WITH RECURSIVE paths(current_id, depth, visited, edge_path, person_path) AS (
                SELECT ?, 0, '|' || ? || '|', '', ?
                UNION ALL
                SELECT
                    CASE WHEN r.subject_id = paths.current_id THEN r.object_id ELSE r.subject_id END,
                    paths.depth + 1,
                    paths.visited || CASE
                        WHEN r.subject_id = paths.current_id THEN r.object_id ELSE r.subject_id
                    END || '|',
                    CASE WHEN paths.edge_path = '' THEN r.id ELSE paths.edge_path || ? || r.id END,
                    paths.person_path || ? || CASE
                        WHEN r.subject_id = paths.current_id THEN r.object_id ELSE r.subject_id
                    END
                FROM paths
                JOIN relationships r
                  ON r.subject_id = paths.current_id OR r.object_id = paths.current_id
                JOIN persons subject ON subject.id = r.subject_id AND subject.deleted_at IS NULL
                JOIN persons object ON object.id = r.object_id AND object.deleted_at IS NULL
                WHERE paths.depth < ?
                  AND (r.valid_to IS NULL OR r.valid_to >= ?)
                  AND instr(
                      paths.visited,
                      '|' || CASE
                          WHEN r.subject_id = paths.current_id THEN r.object_id ELSE r.subject_id
                      END || '|'
                  ) = 0
            )
            SELECT edge_path, person_path
            FROM paths
            WHERE current_id = ?
            ORDER BY depth, edge_path
            LIMIT 1
            """,
            (a, a, a, _SEPARATOR, _SEPARATOR, max_depth, date.today().isoformat(), b),
        ).fetchone()
        if row is None:
            return None
        person_ids = row["person_path"].split(_SEPARATOR)
        edge_ids = row["edge_path"].split(_SEPARATOR) if row["edge_path"] else []
        people = [self._person(person_id, index) for index, person_id in enumerate(person_ids)]
        if any(person is None for person in people):
            return None
        edges = [self._edge(edge_id) for edge_id in edge_ids]
        if any(edge is None for edge in edges):
            return None
        return RelationshipPath(
            people=[person for person in people if person is not None],
            edges=[edge for edge in edges if edge is not None],
        )

    def subgraph(self, person_ids: list[str], depth: int) -> RelationshipSubgraph:
        if not person_ids:
            return RelationshipSubgraph()
        seeds = sorted(set(person_ids))
        values = ", ".join("(?, 0, '|' || ? || '|', '')" for _ in seeds)
        params: list[object] = []
        for person_id in seeds:
            params.extend((person_id, person_id))
        params.extend((depth, date.today().isoformat()))
        rows = self._conn.execute(
            f"""
            WITH RECURSIVE walk(person_id, depth, visited, path_key) AS (
                VALUES {values}
                UNION ALL
                SELECT
                    CASE WHEN r.subject_id = walk.person_id THEN r.object_id ELSE r.subject_id END,
                    walk.depth + 1,
                    walk.visited || CASE
                        WHEN r.subject_id = walk.person_id THEN r.object_id ELSE r.subject_id
                    END || '|',
                    CASE WHEN walk.path_key = '' THEN r.id ELSE walk.path_key || '/' || r.id END
                FROM walk
                JOIN relationships r ON r.subject_id = walk.person_id OR r.object_id = walk.person_id
                JOIN persons subject ON subject.id = r.subject_id AND subject.deleted_at IS NULL
                JOIN persons object ON object.id = r.object_id AND object.deleted_at IS NULL
                WHERE walk.depth < ?
                  AND (r.valid_to IS NULL OR r.valid_to >= ?)
                  AND instr(
                      walk.visited,
                      '|' || CASE
                          WHEN r.subject_id = walk.person_id THEN r.object_id ELSE r.subject_id
                      END || '|'
                  ) = 0
            ), minimum_depth AS (
                SELECT person_id, MIN(depth) AS depth
                FROM walk
                GROUP BY person_id
            ), discovered AS (
                SELECT walk.person_id, walk.depth, MIN(walk.path_key) AS path_key
                FROM walk
                JOIN minimum_depth
                  ON minimum_depth.person_id = walk.person_id AND minimum_depth.depth = walk.depth
                GROUP BY walk.person_id, walk.depth
            )
            SELECT p.id AS person_id, p.canonical_name AS name, p.is_self, discovered.depth
            FROM discovered
            JOIN persons p ON p.id = discovered.person_id AND p.deleted_at IS NULL
            ORDER BY discovered.depth, discovered.path_key, p.id
            """,  # noqa: S608 - VALUES contains placeholders only
            params,
        ).fetchall()
        nodes = [
            GraphPerson(
                person_id=row["person_id"],
                name=row["name"],
                is_self=bool(row["is_self"]),
                depth=row["depth"],
            )
            for row in rows
        ]
        if not nodes:
            return RelationshipSubgraph()
        node_ids = [node.person_id for node in nodes]
        placeholders = ", ".join("?" for _ in node_ids)
        edge_rows = self._conn.execute(
            f"""
            SELECT r.id, r.subject_id, r.object_id, r.type, r.label,
                   COALESCE(rt.category, 'uncategorized') AS category
            FROM relationships r
            JOIN persons subject ON subject.id = r.subject_id AND subject.deleted_at IS NULL
            JOIN persons object ON object.id = r.object_id AND object.deleted_at IS NULL
            LEFT JOIN relationship_types rt ON rt.type = r.type
            WHERE r.subject_id IN ({placeholders})
              AND r.object_id IN ({placeholders})
              AND (r.valid_to IS NULL OR r.valid_to >= ?)
            ORDER BY r.id
            """,  # noqa: S608 - placeholders are generated, values remain bound
            [*node_ids, *node_ids, date.today().isoformat()],
        ).fetchall()
        return RelationshipSubgraph(
            nodes=nodes,
            edges=[_graph_edge(row) for row in edge_rows],
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

    def _edge(self, edge_id: str) -> GraphRelationship | None:
        row = self._conn.execute(
            """
            SELECT r.id, r.subject_id, r.object_id, r.type, r.label,
                   COALESCE(rt.category, 'uncategorized') AS category
            FROM relationships r
            JOIN persons subject ON subject.id = r.subject_id AND subject.deleted_at IS NULL
            JOIN persons object ON object.id = r.object_id AND object.deleted_at IS NULL
            LEFT JOIN relationship_types rt ON rt.type = r.type
            WHERE r.id = ? AND (r.valid_to IS NULL OR r.valid_to >= ?)
            """,
            (edge_id, date.today().isoformat()),
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
