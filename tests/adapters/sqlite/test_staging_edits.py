"""The persistence half of editable staging (M29.1).

Two things are checked here that a fake port cannot show. The store's own guard — no instruction
arriving at it rewrites a row that is no longer pending — and the three bounded person-name
queries, which exist so that a name shared by thousands of people costs a bounded read rather
than thousands of materialized records.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from people_context.adapters.sqlite import SqliteImportStagingStore, SqlitePeopleRepository, open_db
from people_context.app.imports.identity import (
    MatchDisposition,
    match_person_candidate,
    person_candidate_matches,
)
from people_context.domain.person import Alias, AliasKind, Person
from people_context.ports.imports import StagedImportRow

_NOW = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "people.db")
    yield connection
    connection.close()


def _row(row_id: str, status: str = "pending") -> StagedImportRow:
    return StagedImportRow(
        id=row_id,
        batch_id="batch-1",
        source="import/agent:review",
        candidate={"type": "person", "name": "Nadia Okonkwo", "aliases": []},
        status=status,
        created_at=_NOW,
    )


def _stored(conn: sqlite3.Connection, row_id: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM import_staging WHERE id = ?", (row_id,)).fetchone()


def test_update_candidate_replaces_a_pending_row_in_place(conn: sqlite3.Connection) -> None:
    store = SqliteImportStagingStore(conn)
    store.stage_batch([_row("c1")])

    store.update_candidate("c1", {"type": "person", "name": "N. Okonkwo", "aliases": []})

    stored = _stored(conn, "c1")
    assert '"N. Okonkwo"' in stored["candidate_json"]
    assert stored["status"] == "pending"
    assert stored["batch_id"] == "batch-1"


def test_update_candidate_refuses_to_rewrite_a_row_that_is_no_longer_pending(
    conn: sqlite3.Connection,
) -> None:
    store = SqliteImportStagingStore(conn)
    store.stage_batch([_row("c1"), _row("c2")])
    store.mark_committed(["c1"])
    store.mark_status(["c2"], "rejected")

    for row_id in ("c1", "c2"):
        store.update_candidate(row_id, {"type": "person", "name": "Rewritten", "aliases": []})
        assert "Rewritten" not in _stored(conn, row_id)["candidate_json"]


def test_mark_status_moves_only_pending_rows(conn: sqlite3.Connection) -> None:
    store = SqliteImportStagingStore(conn)
    store.stage_batch([_row("c1"), _row("c2")])
    store.mark_committed(["c1"])

    store.mark_status(["c1", "c2"], "rejected")

    assert _stored(conn, "c1")["status"] == "committed"
    assert _stored(conn, "c2")["status"] == "rejected"


def test_the_rejected_status_needs_no_migration(conn: sqlite3.Connection) -> None:
    """`import_staging.status` is plain TEXT, so a new value is purely additive."""
    store = SqliteImportStagingStore(conn)
    store.stage_batch([_row("c1")])

    store.mark_status(["c1"], "rejected")

    assert store.list_batch("batch-1")[0].status == "rejected"


# --- the bounded person-name queries -------------------------------------


def _people(conn: sqlite3.Connection, count: int, name: str = "Priya Sharma") -> list[str]:
    repo = SqlitePeopleRepository(conn)
    ids = []
    for index in range(count):
        person_id = f"01M29COLLIDE{index:014d}"
        repo.save_person(Person(id=person_id, canonical_name=name, aliases=[], created_at=_NOW, updated_at=_NOW))
        ids.append(person_id)
    conn.commit()
    return ids


def test_the_count_and_page_answer_over_every_token_at_once(conn: sqlite3.Connection) -> None:
    repo = SqlitePeopleRepository(conn)
    by_name = _people(conn, 1, "Priya Sharma")[0]
    handle_owner = Person(
        id="01M29HANDLEOWNER000000000001",
        canonical_name="P. Sharma",
        aliases=[Alias(value="priya@acme.test", kind=AliasKind.HANDLE)],
        created_at=_NOW,
        updated_at=_NOW,
    )
    repo.save_person(handle_owner)
    conn.commit()

    tokens = ["priya@acme.test", "priya sharma"]
    assert repo.count_distinct_by_normalized_names(tokens) == 2
    assert {match.id for match in repo.page_by_normalized_names(tokens, 10)} == {by_name, handle_owner.id}


def test_the_page_is_ordered_by_canonical_name_then_id(conn: sqlite3.Connection) -> None:
    repo = SqlitePeopleRepository(conn)
    for person_id, name in (("01M29ORDER2", "Zoe Adams"), ("01M29ORDER1", "Zoe Adams"), ("01M29ORDER3", "Al Adams")):
        repo.save_person(Person(id=person_id, canonical_name=name, aliases=[], created_at=_NOW, updated_at=_NOW))
    conn.commit()

    page = repo.page_by_normalized_names(["zoe adams", "al adams"], 10)

    assert [match.id for match in page] == ["01M29ORDER3", "01M29ORDER1", "01M29ORDER2"]


def test_a_deleted_person_is_not_a_match(conn: sqlite3.Connection) -> None:
    repo = SqlitePeopleRepository(conn)
    person_id = _people(conn, 1)[0]
    person = repo.get(person_id)
    assert person is not None
    person.deleted_at = _NOW
    repo.save_person(person)
    conn.commit()

    assert repo.count_distinct_by_normalized_names(["priya sharma"]) == 0
    assert repo.matches_normalized_names(person_id, ["priya sharma"]) is False


def test_a_blank_token_set_resolves_to_nobody(conn: sqlite3.Connection) -> None:
    repo = SqlitePeopleRepository(conn)
    _people(conn, 2)

    assert repo.count_distinct_by_normalized_names([]) == 0
    assert repo.page_by_normalized_names(["priya sharma"], 0) == []
    assert repo.matches_normalized_names("01M29COLLIDE00000000000000", []) is False


def test_matching_a_common_name_loads_no_person_records(conn: sqlite3.Connection) -> None:
    """The point of the three queries: a huge collision set is never materialized."""
    ids = _people(conn, 5_000)
    repo = SqlitePeopleRepository(conn)
    loaded: list[str] = []
    original = repo.get

    def _tracking_get(person_id: str) -> Person | None:
        loaded.append(person_id)
        return original(person_id)

    repo.get = _tracking_get  # type: ignore[method-assign]

    match = match_person_candidate(repo, ["Priya Sharma"])

    assert match.disposition is MatchDisposition.AMBIGUOUS
    assert match.match_count == 5_000
    assert loaded == []
    assert repo.page_by_normalized_names(["priya sharma"], 10) != []
    assert person_candidate_matches(repo, ["Priya Sharma"], ids[-1]) is True
    assert loaded == []
