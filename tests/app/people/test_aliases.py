"""Removing and correcting one alias by normalized value, without touching the rest of the person."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from people_context.app.people import (
    AliasConflictError,
    AliasNotFoundError,
    RemoveAlias,
    RemoveAliasInput,
    UpdateAlias,
    UpdateAliasInput,
)
from people_context.app.records import PersonNotFoundError
from people_context.domain.person import Alias, AliasKind, Person
from tests.app.fakes import FakeAuditLog, FakeClock, FakePeopleRepository

_NOW = datetime(2026, 11, 4, tzinfo=UTC)


@pytest.fixture
def deps() -> tuple[FakePeopleRepository, FakeAuditLog, FakeClock, Person]:
    people = FakePeopleRepository()
    person = Person(
        canonical_name="Priya Sharma",
        aliases=[Alias(value="priya@example.com"), Alias(value="Pri", kind=AliasKind.NICKNAME)],
        created_at=_NOW,
        updated_at=_NOW,
    )
    people.save_person(person)
    return people, FakeAuditLog(), FakeClock(_NOW), person


def test_remove_alias_drops_only_the_named_alias_and_audits(deps: tuple) -> None:
    people, audit, clock, person = deps
    use_case = RemoveAlias(people, people, audit, clock)

    result = use_case.execute(RemoveAliasInput(person_id=person.id, value="  PRIYA@example.com "))

    stored = people.get(person.id)
    assert result.model_dump() == {"person_id": person.id, "alias": "priya@example.com", "removed": True}
    assert [alias.value for alias in stored.aliases] == ["Pri"]
    assert stored.canonical_name == "Priya Sharma"
    assert audit.entries[-1].payload == {"alias_removed": "  PRIYA@example.com ", "removed": True}


def test_removing_an_absent_alias_is_a_reported_no_op(deps: tuple) -> None:
    people, audit, clock, person = deps
    use_case = RemoveAlias(people, people, audit, clock)
    use_case.execute(RemoveAliasInput(person_id=person.id, value="priya@example.com"))

    again = use_case.execute(RemoveAliasInput(person_id=person.id, value="priya@example.com"))

    assert again.removed is False
    assert [alias.value for alias in people.get(person.id).aliases] == ["Pri"]


def test_the_canonical_name_is_not_an_alias_to_remove_or_update(deps: tuple) -> None:
    people, audit, clock, person = deps

    with pytest.raises(AliasConflictError) as removed:
        RemoveAlias(people, people, audit, clock).execute(RemoveAliasInput(person_id=person.id, value="priya sharma"))
    with pytest.raises(AliasConflictError):
        UpdateAlias(people, people, audit, clock).execute(
            UpdateAliasInput(person_id=person.id, value="Priya Sharma", kind=AliasKind.NICKNAME)
        )

    assert removed.value.reason == "canonical_name"
    assert audit.entries == []


def test_alias_edits_reject_an_unknown_person(deps: tuple) -> None:
    people, audit, clock, _ = deps

    with pytest.raises(PersonNotFoundError):
        RemoveAlias(people, people, audit, clock).execute(RemoveAliasInput(person_id="missing", value="x"))
    with pytest.raises(PersonNotFoundError):
        UpdateAlias(people, people, audit, clock).execute(UpdateAliasInput(person_id="missing", value="x"))


def test_update_alias_changes_given_fields_in_place_and_keeps_the_id(deps: tuple) -> None:
    people, audit, clock, person = deps
    original = person.aliases[1]

    result = UpdateAlias(people, people, audit, clock).execute(
        UpdateAliasInput(person_id=person.id, value="pri", new_value="Priyu", lang="hi")
    )

    stored = people.get(person.id).aliases[1]
    assert result.changed == ["value", "lang"]
    assert (stored.id, stored.value, stored.kind, stored.lang) == (original.id, "Priyu", AliasKind.NICKNAME, "hi")
    assert audit.entries[-1].payload["alias_before"]["value"] == "Pri"
    assert audit.entries[-1].payload["alias_after"]["value"] == "Priyu"


def test_update_alias_refuses_a_value_colliding_with_another_name(deps: tuple) -> None:
    people, audit, clock, person = deps
    use_case = UpdateAlias(people, people, audit, clock)

    for clash in ("PRIYA SHARMA", "priya@example.com"):
        with pytest.raises(AliasConflictError) as refused:
            use_case.execute(UpdateAliasInput(person_id=person.id, value="Pri", new_value=clash))
        assert refused.value.reason == "duplicate_name"
    assert [alias.value for alias in people.get(person.id).aliases] == ["priya@example.com", "Pri"]


def test_update_alias_may_recase_its_own_value_and_reports_an_unknown_alias(deps: tuple) -> None:
    people, audit, clock, person = deps
    use_case = UpdateAlias(people, people, audit, clock)

    recased = use_case.execute(UpdateAliasInput(person_id=person.id, value="Pri", new_value="PRI"))
    unchanged = use_case.execute(UpdateAliasInput(person_id=person.id, value="PRI", kind=AliasKind.NICKNAME))
    with pytest.raises(AliasNotFoundError):
        use_case.execute(UpdateAliasInput(person_id=person.id, value="Nobody", kind=AliasKind.HANDLE))

    assert recased.changed == ["value"]
    assert unchanged.changed == []


def test_an_email_is_an_alias_only_as_a_handle_on_every_input_path() -> None:
    from pydantic import ValidationError

    from people_context.app.imports.models import CandidateAlias
    from people_context.app.people import AddAliasInput, AliasInput

    for build in (
        lambda kind: AddAliasInput(person_id="p", value="ann@example.com", kind=kind),
        lambda kind: AliasInput(value=" ann@example.com ", kind=kind),
        lambda kind: CandidateAlias(value="ann@example.com", kind=kind),
    ):
        with pytest.raises(ValidationError, match="alias_email_not_handle"):
            build(AliasKind.OTHER)
        assert build(AliasKind.HANDLE).kind is AliasKind.HANDLE
    # `@handle` and names that merely contain `@` are not email addresses.
    assert AddAliasInput(person_id="p", value="@ann", kind=AliasKind.NICKNAME).value == "@ann"


def test_update_alias_checks_the_resulting_alias_and_fixes_a_stored_email_by_making_it_a_handle(deps: tuple) -> None:
    people, audit, clock, person = deps
    use_case = UpdateAlias(people, people, audit, clock)

    with pytest.raises(AliasConflictError) as kept_as_other:
        use_case.execute(UpdateAliasInput(person_id=person.id, value="priya@example.com", lang="en"))
    with pytest.raises(AliasConflictError):
        use_case.execute(UpdateAliasInput(person_id=person.id, value="Pri", new_value="pri@example.com"))
    fixed = use_case.execute(UpdateAliasInput(person_id=person.id, value="priya@example.com", kind=AliasKind.HANDLE))

    assert kept_as_other.value.reason == "alias_email_not_handle"
    assert fixed.changed == ["kind"]
    assert [(a.value, a.kind) for a in people.get(person.id).aliases] == [
        ("priya@example.com", AliasKind.HANDLE),
        ("Pri", AliasKind.NICKNAME),
    ]
