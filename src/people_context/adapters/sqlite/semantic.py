"""sqlite-vec storage, semantic metadata, and eligible-document hydration."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from people_context.adapters.model2vec_embeddings import MODEL_DIMENSION
from people_context.ports.semantic import (
    SemanticDocument,
    SemanticIndexMetadata,
    VectorSearchHit,
)

SEMANTIC_MODEL_ID_KEY = "semantic_embedding_model_id"
SEMANTIC_DIMENSION_KEY = "semantic_embedding_dimension"
_VECTOR_TABLE = "semantic_vectors"


class SqliteVecNotAvailableError(RuntimeError):
    """Raised when sqlite-vec is unavailable or cannot be loaded."""


class SqliteVectorIndex:
    """Store entity embeddings in a same-file vec0 table using cosine distance."""

    def __init__(self, conn: sqlite3.Connection, serialize: Callable[[list[float]], bytes]) -> None:
        self._conn = conn
        self._serialize = serialize
        self._ensure_table()

    def upsert(self, kind: str, entity_id: str, vector: list[float]) -> None:
        self._validate_vector(vector)
        with self._conn:
            self._conn.execute(f"DELETE FROM {_VECTOR_TABLE} WHERE entity_id = ?", (entity_id,))
            self._conn.execute(
                f"INSERT INTO {_VECTOR_TABLE} (entity_id, kind, embedding) VALUES (?, ?, ?)",
                (entity_id, kind, self._serialize(vector)),
            )

    def delete(self, entity_id: str) -> None:
        with self._conn:
            self._conn.execute(f"DELETE FROM {_VECTOR_TABLE} WHERE entity_id = ?", (entity_id,))

    def search(self, kind: str, vector: list[float], limit: int) -> list[VectorSearchHit]:
        self._validate_vector(vector)
        rows = self._conn.execute(
            f"""SELECT entity_id, distance FROM {_VECTOR_TABLE}
                WHERE embedding MATCH ? AND kind = ? AND k = ?
                ORDER BY distance, entity_id""",
            (self._serialize(vector), kind, limit),
        ).fetchall()
        return [VectorSearchHit(entity_id=row["entity_id"], distance=float(row["distance"])) for row in rows]

    def get_metadata(self) -> SemanticIndexMetadata | None:
        values = _read_preferences(self._conn, [SEMANTIC_MODEL_ID_KEY, SEMANTIC_DIMENSION_KEY])
        model_id = values.get(SEMANTIC_MODEL_ID_KEY)
        dimension = values.get(SEMANTIC_DIMENSION_KEY)
        if not isinstance(model_id, str) or not isinstance(dimension, int):
            return None
        return SemanticIndexMetadata(model_id=model_id, dimension=dimension)

    def replace_all(
        self,
        documents: list[SemanticDocument],
        vectors: list[list[float]],
        metadata: SemanticIndexMetadata,
    ) -> None:
        if len(documents) != len(vectors):
            raise ValueError("documents and vectors must have the same length")
        if metadata.dimension != MODEL_DIMENSION:
            raise ValueError(f"semantic index dimension must be {MODEL_DIMENSION}")
        for vector in vectors:
            self._validate_vector(vector)
        now = datetime.now(UTC).isoformat()
        with self._conn:
            self._conn.execute(f"DELETE FROM {_VECTOR_TABLE}")
            self._conn.executemany(
                f"INSERT INTO {_VECTOR_TABLE} (entity_id, kind, embedding) VALUES (?, ?, ?)",
                [
                    (document.entity_id, document.kind, self._serialize(vector))
                    for document, vector in zip(documents, vectors, strict=True)
                ],
            )
            self._write_preference(SEMANTIC_MODEL_ID_KEY, metadata.model_id, now)
            self._write_preference(SEMANTIC_DIMENSION_KEY, metadata.dimension, now)

    def _ensure_table(self) -> None:
        self._conn.execute(
            f"""CREATE VIRTUAL TABLE IF NOT EXISTS {_VECTOR_TABLE} USING vec0(
                entity_id TEXT PRIMARY KEY,
                kind TEXT PARTITION KEY,
                embedding FLOAT[{MODEL_DIMENSION}] DISTANCE_METRIC=cosine
            )"""
        )

    def _validate_vector(self, vector: list[float]) -> None:
        if len(vector) != MODEL_DIMENSION:
            raise ValueError(f"semantic vector must contain {MODEL_DIMENSION} values")

    def _write_preference(self, key: str, value: str | int, updated_at: str) -> None:
        self._conn.execute(
            """INSERT INTO user_preferences (key, value_json, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at""",
            (key, json.dumps(value), updated_at),
        )


class SqliteSemanticDocumentReader:
    """Build deterministic person and eligible-interaction documents from SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list_documents(self) -> list[SemanticDocument]:
        return [*self._person_documents(), *self._interaction_documents()]

    def _person_documents(self) -> list[SemanticDocument]:
        rows = self._conn.execute(
            "SELECT id, canonical_name, summary FROM persons WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
        documents: list[SemanticDocument] = []
        for row in rows:
            aliases = self._conn.execute(
                "SELECT value FROM aliases WHERE person_id = ? ORDER BY id",
                (row["id"],),
            ).fetchall()
            parts = [row["canonical_name"], *(alias["value"] for alias in aliases)]
            if row["summary"]:
                parts.append(row["summary"])
            documents.append(SemanticDocument(kind="person", entity_id=row["id"], text="\n".join(parts)))
        return documents

    def _interaction_documents(self) -> list[SemanticDocument]:
        rows = self._conn.execute(
            """SELECT id, summary FROM interactions
               WHERE sensitivity IN ('public', 'personal') ORDER BY id"""
        ).fetchall()
        return [
            SemanticDocument(kind="interaction", entity_id=row["id"], text=row["summary"])
            for row in rows
        ]


def create_sqlite_vector_index(conn: sqlite3.Connection) -> SqliteVectorIndex:
    """Load sqlite-vec into an existing connection and immediately disable extension loading."""
    try:
        import sqlite_vec
    except ImportError as exc:
        raise SqliteVecNotAvailableError("install the semantic optional dependencies") from exc
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except Exception as exc:
        raise SqliteVecNotAvailableError("sqlite-vec could not be loaded") from exc
    finally:
        conn.enable_load_extension(False)
    return SqliteVectorIndex(conn, sqlite_vec.serialize_float32)


def read_semantic_metadata(conn: sqlite3.Connection) -> SemanticIndexMetadata | None:
    """Read semantic metadata without importing optional semantic packages."""
    values = _read_preferences(conn, [SEMANTIC_MODEL_ID_KEY, SEMANTIC_DIMENSION_KEY])
    model_id = values.get(SEMANTIC_MODEL_ID_KEY)
    dimension = values.get(SEMANTIC_DIMENSION_KEY)
    if not isinstance(model_id, str) or not isinstance(dimension, int):
        return None
    return SemanticIndexMetadata(model_id=model_id, dimension=dimension)


def _read_preferences(conn: sqlite3.Connection, keys: list[str]) -> dict[str, object]:
    placeholders = ", ".join("?" for _ in keys)
    rows = conn.execute(
        f"SELECT key, value_json FROM user_preferences WHERE key IN ({placeholders})",
        keys,
    ).fetchall()
    return {row["key"]: json.loads(row["value_json"]) for row in rows}
