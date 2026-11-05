"""An email address is contact information: never a new person's name, and kept as a handle on rename (#225)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from people_context.app.people import (
    AliasInput,
    EditPerson,
    EditPersonInput,
    EmailNameError,
    RememberPerson,
    RememberPersonInput,
)
from people_context.domain.person import Alias, AliasKind, Person
from tests.app.fakes import FakeAuditLog, FakeClock, FakePeopleRepository

_NOW = datetime(2026, 11, 5, tzinfo=UTC)


def _deps() -> tuple[FakePeopleRepository, FakeAuditLog, FakeClock]:
    return FakePeopleRepository(), FakeAuditLog(), FakeClock(_NOW)


def test_a_new_person_is_never_named_by_an_email_address() -> None:
    people, audit, clock = _deps()
    remember = RememberPerson(people, people, audit, clock)

    with pytest.raises(EmailNameError):
        remember.execute(RememberPersonInput(name="jordan@example.com"))

    assert people.list_people() == []
    assert audit.entries == []


def test_an_address_someone_already_holds_as_a_handle_still_updates_them() -> None:
    people, audit, clock = _deps()
    remember = RememberPerson(people, people, audit, clock)
    jordan = remember.execute(
        RememberPersonInput(name="Jordan Lee", aliases=[AliasInput(value="jordan@example.com", kind=AliasKind.HANDLE)])
    ).person

    result = remember.execute(RememberPersonInput(name="jordan@example.com", summary="Billing owner"))

    assert result.created is False
    assert result.person.id == jordan.id
    assert result.person.canonical_name == "Jordan Lee"


def test_renaming_a_person_stored_under_an_address_keeps_it_as_a_handle() -> None:
    people, audit, clock = _deps()
    # Stored before email names were refused; it still loads and can be corrected.
    legacy = Person(canonical_name="contact@example.com", aliases=[Alias(value="JL", kind=AliasKind.NICKNAME)])
    people.save_person(legacy)

    renamed = EditPerson(people, people, audit, clock).execute(
        EditPersonInput(person_id=legacy.id, name="Jordan Lee", source="agent")
    )

    assert renamed.canonical_name == "Jordan Lee"
    assert [(alias.value, alias.kind) for alias in renamed.aliases] == [
        ("JL", AliasKind.NICKNAME),
        ("contact@example.com", AliasKind.HANDLE),
    ]
    assert audit.entries[-1].payload["fields"] == ["canonical_name", "aliases"]


def test_a_rename_keeps_the_old_name_and_drops_the_alias_it_promotes() -> None:
    people, audit, clock = _deps()
    person = Person(canonical_name="Robert", aliases=[Alias(value="Bobby", kind=AliasKind.NICKNAME)])
    people.save_person(person)
    edit = EditPerson(people, people, audit, clock)

    renamed = edit.execute(EditPersonInput(person_id=person.id, name="Bobby"))
    assert [(alias.value, alias.kind) for alias in renamed.aliases] == [("Robert", AliasKind.FORMER_NAME)]

    # A change of case alone is not a former name.
    recased = edit.execute(EditPersonInput(person_id=person.id, name="BOBBY"))
    assert [alias.value for alias in recased.aliases] == ["Robert"]


def test_a_rename_to_an_email_address_is_refused() -> None:
    with pytest.raises(ValidationError) as exc_info:
        EditPersonInput(person_id="p", name="jordan@example.com")

    assert [error["type"] for error in exc_info.value.errors()] == ["name_is_email"]
