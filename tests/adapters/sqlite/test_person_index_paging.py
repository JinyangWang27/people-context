"""Keyset paging of the person index over a large real store (M30.1)."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from ulid import ULID

from people_context.adapters.sqlite import SqlitePeopleRepository, open_db
from people_context.app.exports import ListPersonIndex
from people_context.domain.shared import normalize_name

_PEOPLE = 10_000
_NOW = datetime(2026, 9, 1, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _store(tmp_path: Path) -> SqlitePeopleRepository:
    conn = open_db(tmp_path / "people.db")
    # Bulk rows keep the fixture fast; every tenth name repeats so ties must break by id.
    names = ["Elena Marsh" if index % 10 == 0 else f"Toma Ibarra {index:05d}" for index in range(_PEOPLE)]
    stamp = _NOW.isoformat()
    with conn:
        conn.executemany(
            "INSERT INTO persons (id, canonical_name, canonical_name_normalized, is_self, created_at, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            [(str(ULID()), name, normalize_name(name), stamp, stamp) for name in names],
        )
    return SqlitePeopleRepository(conn)


def _walk(index: ListPersonIndex, limit: int) -> list[str]:
    seen: list[str] = []
    cursor = None
    while True:
        page = index.page(limit=limit, cursor=cursor)
        assert len(page.people) <= limit
        seen.extend(entry.id for entry in page.people)
        cursor = page.next_cursor
        if cursor is None:
            return seen


def test_following_next_cursor_reaches_every_person_exactly_once(tmp_path: Path) -> None:
    repository = _store(tmp_path)
    index = ListPersonIndex(repository, _Clock())

    unpaged = [entry.id for entry in index.execute().people]
    first = index.page()

    assert len(first.people) == 50
    for limit in (50, 200):
        seen = _walk(index, limit)
        assert len(seen) == _PEOPLE
        assert set(Counter(seen).values()) == {1}
        # The pages concatenate into exactly the order the unpaged document sorts into.
        assert seen == unpaged


def test_an_unpaged_limit_keeps_its_meaning(tmp_path: Path) -> None:
    index = ListPersonIndex(_store(tmp_path), _Clock())

    document = index.execute(limit=1000)

    assert len(document.people) == 1000
    assert document.next_cursor is None
