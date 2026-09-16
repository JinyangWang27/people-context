"""The persistence half of editable staging (M29.1).

Two things are checked here that a fake port cannot show. The store's own guard — no instruction
arriving at it rewrites a row that is no longer pending — and the three bounded person-name
queries, which exist so that a name shared by thousands of people costs a bounded read rather
than thousands of materialized records.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from people_context.adapters.semantic_indexing import IndexingPeopleRepository
from people_context.adapters.sqlite import SqliteImportStagingStore, SqlitePeopleRepository, open_db
from people_context.adapters.sqlite.repository import matching_people_sql
from people_context.app.imports.identity import (
    MatchDisposition,
    collision_page,
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
    assert repo.match_normalized_names(tokens).total == 2
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

    assert repo.match_normalized_names(["priya sharma"]).total == 0
    assert repo.matches_normalized_names(person_id, ["priya sharma"]) is False


def test_a_blank_token_set_resolves_to_nobody(conn: sqlite3.Connection) -> None:
    repo = SqlitePeopleRepository(conn)
    _people(conn, 2)

    assert repo.match_normalized_names([]).total == 0
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


def test_the_matching_queries_use_both_normalized_name_indexes(conn: sqlite3.Connection) -> None:
    """Regression: an `OR` across the join scanned `persons` end to end.

    SQLite cannot satisfy a disjunction spanning two tables from either index, so the readable
    form planned as a full scan plus an alias probe per row — and this runs once per staged person
    candidate and again per ambiguous row at review, so the cost was the whole store times the
    batch. Asserting the plan is the only way to keep that from coming back unnoticed.
    """
    tokens = json.dumps(["a", "b"])
    plan = [
        row["detail"]
        for row in conn.execute("EXPLAIN QUERY PLAN " + matching_people_sql(), (tokens, tokens))
    ]

    assert any("idx_persons_canonical_norm" in step for step in plan), plan
    assert any("idx_aliases_value_norm" in step for step in plan), plan
    assert not any(step.startswith("SCAN p") for step in plan), plan


def test_a_token_matching_only_an_alias_still_resolves(conn: sqlite3.Connection) -> None:
    """The union must not lose the alias half the single query used to cover."""
    repo = SqlitePeopleRepository(conn)
    repo.save_person(
        Person(
            id="01M29ALIASONLY00000000000001",
            canonical_name="P. Sharma",
            aliases=[Alias(value="priya@acme.test", kind=AliasKind.HANDLE)],
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    conn.commit()

    assert repo.match_normalized_names(["priya@acme.test"]).total == 1
    assert repo.matches_normalized_names("01M29ALIASONLY00000000000001", ["priya@acme.test"]) is True


def test_one_person_matched_by_both_a_name_and_an_alias_counts_once(conn: sqlite3.Connection) -> None:
    """`UNION` does the deduplication the old `DISTINCT` did."""
    repo = SqlitePeopleRepository(conn)
    repo.save_person(
        Person(
            id="01M29BOTHHALVES00000000000001",
            canonical_name="Priya Sharma",
            aliases=[Alias(value="priya@acme.test", kind=AliasKind.HANDLE)],
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    conn.commit()

    assert repo.match_normalized_names(["priya sharma", "priya@acme.test"]).total == 1
    assert len(repo.page_by_normalized_names(["priya sharma", "priya@acme.test"], 10)) == 1


@pytest.mark.parametrize("tokens", [[], ["   "], ["", "\t"]])
def test_a_token_set_that_normalizes_to_nothing_matches_nobody(
    conn: sqlite3.Connection, tokens: list[str]
) -> None:
    """An empty token set is not a wildcard, and must never be sent to the reader as one.

    These helpers take arbitrary tokens, so the guard is the contract rather than a formality:
    without it, an empty `IN ()` would answer "nobody" only by accident of SQL.
    """
    repo = SqlitePeopleRepository(conn)
    _people(conn, 2)

    assert match_person_candidate(repo, tokens).disposition is MatchDisposition.UNMATCHED
    assert person_candidate_matches(repo, tokens, "01M29COLLIDE00000000000000") is False
    assert collision_page(repo, tokens, 10) == []


def test_punctuation_is_kept_by_normalization_and_still_matched(conn: sqlite3.Connection) -> None:
    """`normalize_name` folds case and whitespace, not characters: `---` is a real token."""
    repo = SqlitePeopleRepository(conn)
    repo.save_person(
        Person(id="01M29PUNCT000000000000000001", canonical_name="---", aliases=[], created_at=_NOW, updated_at=_NOW)
    )
    conn.commit()

    assert match_person_candidate(repo, ["---"]).disposition is MatchDisposition.MATCHED


def test_the_count_and_the_chosen_person_come_from_one_snapshot(conn: sqlite3.Connection) -> None:
    """Regression: a count then a lookup could straddle a concurrent insert.

    Staging matches before it takes the write lock, so a person created between the two reads
    turned a fresh ambiguity into a confident unique match — and the candidate and its dependents
    would commit against one of several identities, which is what this matcher exists to prevent.
    One statement cannot disagree with itself.
    """
    _people(conn, 2)
    repo = SqlitePeopleRepository(conn)

    matches = repo.match_normalized_names(["priya sharma"])

    assert matches.total == 2
    assert matches.first is not None
    assert match_person_candidate(repo, ["Priya Sharma"]).disposition is MatchDisposition.AMBIGUOUS


def test_a_unique_match_names_the_person_it_counted(conn: sqlite3.Connection) -> None:
    person_id = _people(conn, 1)[0]
    repo = SqlitePeopleRepository(conn)

    matches = repo.match_normalized_names(["priya sharma"])

    assert matches.total == 1
    assert matches.first is not None and matches.first.id == person_id


def test_the_semantic_wrapper_forwards_every_matching_query(conn: sqlite3.Connection) -> None:
    """A wrapper that dropped one of these would silently change who a candidate resolves to."""
    repo = SqlitePeopleRepository(conn)
    person_id = _people(conn, 1)[0]
    wrapped = IndexingPeopleRepository(repo, _NullUpdater(), lambda _message: None)

    assert wrapped.match_normalized_names(["priya sharma"]).total == 1
    assert [match.id for match in wrapped.page_by_normalized_names(["priya sharma"], 10)] == [person_id]
    assert wrapped.matches_normalized_names(person_id, ["priya sharma"]) is True


class _NullUpdater:
    """Stands in for the semantic index, which these reads never touch."""

    def refresh_person(self, person: Person) -> None:  # pragma: no cover - never reached by reads
        raise AssertionError("a read must not refresh the index")

    def remove_person(self, person_id: str) -> None:  # pragma: no cover - never reached by reads
        raise AssertionError("a read must not refresh the index")


def test_a_candidate_with_more_handles_than_sqlite_takes_parameters_still_matches(
    conn: sqlite3.Connection,
) -> None:
    """Regression: one bound parameter per token crossed SQLite's variable ceiling.

    A person candidate may carry thousands of handle aliases well inside the staging request
    limits, and each token was bound twice — once per branch of the union. Past roughly 16,000
    aliases that raised `OperationalError: too many SQL variables`, which reaches the CLI as a
    traceback and MCP as a tool failure, where both owe a structured refusal.
    """
    repo = SqlitePeopleRepository(conn)
    wanted = "01M29MANYTOKENS00000000000001"
    repo.save_person(
        Person(
            id=wanted,
            canonical_name="Ada Lovelace",
            aliases=[Alias(value="ada@x.test", kind=AliasKind.HANDLE)],
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    conn.commit()
    limit = conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    tokens = [f"handle-{index}@x.test" for index in range(limit)] + ["ada@x.test"]

    matches = repo.match_normalized_names(tokens)

    assert matches.total == 1
    assert matches.first is not None and matches.first.id == wanted
    assert repo.matches_normalized_names(wanted, tokens) is True
    assert [match.id for match in repo.page_by_normalized_names(tokens, 10)] == [wanted]


def test_the_staging_store_reserves_the_write_lock_before_it_reads(tmp_path: Path) -> None:
    """Amend, withdraw, and commit all read the batch and then decide from what they read.

    A deferred `BEGIN` takes the lock at the first write, which is after the decision, so a
    concurrent writer could land in between and the promised `batch_changed` refusal would surface
    as a SQLite busy error instead. The staging store supplies the reserving boundary itself, so
    the guarantee does not depend on a source store being wired to provide one.

    Asserted by behaviour rather than by the statement issued: while this boundary is open, a
    second connection cannot write. Under a deferred `BEGIN` it could.
    """
    path = tmp_path / "reserving.db"
    holder = open_db(path)
    contender = open_db(path)
    contender.execute("PRAGMA busy_timeout = 0")
    store = SqliteImportStagingStore(holder)
    try:
        with store.unit_of_work, pytest.raises(sqlite3.OperationalError, match="locked"):
            contender.execute(
                "INSERT INTO import_staging (id, batch_id, source, candidate_json, status, created_at)"
                " VALUES ('x', 'b', 's', '{}', 'pending', '2026-09-16T09:00:00+00:00')"
            )
        # And once it closes, the same write goes through.
        contender.execute(
            "INSERT INTO import_staging (id, batch_id, source, candidate_json, status, created_at)"
            " VALUES ('x', 'b', 's', '{}', 'pending', '2026-09-16T09:00:00+00:00')"
        )
        contender.commit()
    finally:
        contender.close()
        holder.close()
