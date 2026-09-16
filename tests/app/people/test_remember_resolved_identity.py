"""`RememberPerson` writing to an identity the caller already resolved (M29.1)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from people_context.adapters.sqlite import SqliteAuditLog, SqlitePeopleRepository, open_db
from people_context.app._mutation import PersonNotFoundError
from people_context.app.people import RememberPerson, RememberPersonInput, SelfAlreadyExistsError
from people_context.domain.person import Person

_NOW = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = open_db(tmp_path / "people.db")
    yield connection
    connection.close()


def _remember(conn: sqlite3.Connection) -> tuple[SqlitePeopleRepository, RememberPerson]:
    people = SqlitePeopleRepository(conn)
    return people, RememberPerson(people, people, SqliteAuditLog(conn), _Clock())


def _save(people: SqlitePeopleRepository, person_id: str, name: str) -> str:
    people.save_person(
        Person(id=person_id, canonical_name=name, aliases=[], created_at=_NOW, updated_at=_NOW)
    )
    return person_id


def test_a_resolved_id_updates_that_person_without_consulting_the_name(
    conn: sqlite3.Connection,
) -> None:
    """The point of the field: a name shared by several people is not a lookup any more.

    Resolving by name here would re-raise the ambiguity the caller's choice settled.
    """
    people, remember = _remember(conn)
    chosen = _save(people, "01M29RESOLVED0000000000001", "Priya Sharma")
    _save(people, "01M29RESOLVED0000000000002", "Priya Sharma")
    conn.commit()

    result = remember.execute(
        RememberPersonInput(name="Priya Sharma", person_id=chosen, summary="the one at Acme")
    )

    assert result.created is False
    assert result.person.id == chosen
    assert result.person.summary == "the one at Acme"


def test_a_resolved_id_never_creates_a_person(conn: sqlite3.Connection) -> None:
    _people, remember = _remember(conn)

    with pytest.raises(PersonNotFoundError):
        remember.execute(RememberPersonInput(name="Nobody", person_id="01M29MISSING000000000000001"))


def test_a_retired_identity_is_refused_rather_than_written_to(conn: sqlite3.Connection) -> None:
    people, remember = _remember(conn)
    person_id = _save(people, "01M29RETIRED00000000000001", "Priya Sharma")
    retired = people.get(person_id)
    assert retired is not None
    retired.deleted_at = _NOW
    people.save_person(retired)
    conn.commit()

    with pytest.raises(PersonNotFoundError):
        remember.execute(RememberPersonInput(name="Priya Sharma", person_id=person_id))


def test_marking_a_resolved_person_self_still_guards_the_existing_self(
    conn: sqlite3.Connection,
) -> None:
    """The self guard is not skipped just because the identity arrived resolved."""
    people, remember = _remember(conn)
    remember.execute(RememberPersonInput(name="Sam Self", is_self=True))
    other = _save(people, "01M29NOTSELF000000000000001", "Priya Sharma")
    conn.commit()

    with pytest.raises(SelfAlreadyExistsError):
        remember.execute(RememberPersonInput(name="Priya Sharma", person_id=other, is_self=True))
