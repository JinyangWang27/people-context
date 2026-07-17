"""Tests for RememberPerson against in-memory fakes."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from people_context.app.record import (
    AliasInput,
    AmbiguousPersonError,
    RememberPerson,
    RememberPersonInput,
    SelfAlreadyExistsError,
)
from people_context.domain.person import AliasKind, Person
from people_context.domain.shared import normalize_name
from tests.app.fakes import FakeAuditLog, FakeClock, FakePeopleRepository

_T0 = datetime(2025, 1, 1, 8, 0, 0, tzinfo=UTC)
_T1 = datetime(2025, 6, 2, 9, 30, 0, tzinfo=UTC)


def _make() -> tuple[RememberPerson, FakePeopleRepository, FakeAuditLog, FakeClock]:
    repo = FakePeopleRepository()
    audit = FakeAuditLog()
    clock = FakeClock(_T0)
    return RememberPerson(repo, repo, audit, clock), repo, audit, clock


def test_create_new_person_with_aliases_and_audit() -> None:
    remember, repo, audit, _ = _make()

    result = remember.execute(
        RememberPersonInput(
            name="Wang Xiaoming",
            aliases=[AliasInput(value="Ming", kind=AliasKind.NICKNAME)],
            summary="Colleague",
            source="agent:claude-code",
        )
    )

    assert result.created is True
    stored = repo.get(result.person.id)
    assert stored is not None
    assert stored.canonical_name == "Wang Xiaoming"
    assert [a.value for a in stored.aliases] == ["Ming"]
    assert stored.created_at == _T0
    assert stored.updated_at == _T0

    entry = audit.list_entries()[0]
    assert entry.op == "create"
    assert entry.entity_type == "person"
    assert entry.entity_id == result.person.id
    assert entry.payload == {"canonical_name": "Wang Xiaoming", "created": True, "alias_count": 1}
    assert entry.source == "agent:claude-code"
    assert entry.ts == _T0


def test_update_existing_person_merges_aliases_without_duplicates() -> None:
    remember, repo, audit, clock = _make()
    remember.execute(
        RememberPersonInput(name="Wang Xiaoming", aliases=[AliasInput(value="Ming", kind=AliasKind.NICKNAME)])
    )

    clock.set(_T1)
    result = remember.execute(
        RememberPersonInput(
            name="wang  xiaoming",  # same normalized form
            aliases=[
                AliasInput(value="Ming"),  # already known -> skipped
                AliasInput(value="小明", kind=AliasKind.NATIVE_SCRIPT),  # new
            ],
            summary="Updated summary",
        )
    )

    assert result.created is False
    stored = repo.get(result.person.id)
    assert stored is not None
    assert [a.value for a in stored.aliases] == ["Ming", "小明"]
    assert stored.summary == "Updated summary"
    assert stored.updated_at == _T1
    assert stored.created_at == _T0  # unchanged

    entry = audit.list_entries()[0]
    assert entry.op == "update"
    assert entry.payload == {"canonical_name": "Wang Xiaoming", "created": False, "alias_count": 1}


def test_update_does_not_overwrite_summary_when_not_provided() -> None:
    remember, repo, _, _ = _make()
    first = remember.execute(RememberPersonInput(name="Alice", summary="Original"))

    remember.execute(RememberPersonInput(name="Alice"))  # no summary

    stored = repo.get(first.person.id)
    assert stored is not None
    assert stored.summary == "Original"


def test_ambiguous_person_error_when_two_existing_share_normalized_name() -> None:
    remember, repo, _, _ = _make()
    repo.save_person(Person(canonical_name="Wang", created_at=_T0, updated_at=_T0))
    repo.save_person(Person(canonical_name="wang", created_at=_T0, updated_at=_T0))

    with pytest.raises(AmbiguousPersonError) as exc:
        remember.execute(RememberPersonInput(name="Wang"))

    assert len(exc.value.person_ids) == 2
    assert normalize_name("Wang") == "wang"


def test_self_already_exists_error_on_second_self() -> None:
    remember, repo, _, _ = _make()
    repo.save_person(Person(canonical_name="Me", is_self=True, created_at=_T0, updated_at=_T0))

    with pytest.raises(SelfAlreadyExistsError):
        remember.execute(RememberPersonInput(name="Someone Else", is_self=True))


def test_creating_self_when_none_exists_works() -> None:
    remember, repo, _, _ = _make()

    result = remember.execute(RememberPersonInput(name="Me", is_self=True))

    assert result.created is True
    stored = repo.get(result.person.id)
    assert stored is not None
    assert stored.is_self is True
    assert repo.get_self() is not None


def test_updating_the_existing_self_is_allowed() -> None:
    remember, repo, _, clock = _make()
    created = remember.execute(RememberPersonInput(name="Me", is_self=True))

    clock.set(_T1)
    result = remember.execute(RememberPersonInput(name="Me", is_self=True, summary="Updated"))

    assert result.created is False
    assert result.person.id == created.person.id
    assert result.person.summary == "Updated"
