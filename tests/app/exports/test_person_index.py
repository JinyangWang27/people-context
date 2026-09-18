"""Application policy for the versioned person index (M14.1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from people_context.app.exports import (
    DEFAULT_PERSON_PAGE_LIMIT,
    INVALID_PERSON_CURSOR,
    INVALID_PERSON_PAGE_LIMIT,
    MAX_PERSON_PAGE_LIMIT,
    PERSON_INDEX_FORMAT,
    PERSON_INDEX_VERSION,
    ListPersonIndex,
    PersonIndexDocument,
    PersonIndexError,
    render_person_index_json,
)
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.source_cursor import SOURCE_LIST_SCOPE, encode_cursor
from tests.app.fakes import FakeClock, FakePeopleRepository

_NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)


def _index(people: FakePeopleRepository) -> ListPersonIndex:
    return ListPersonIndex(people, FakeClock(_NOW))


def _person(name: str, **fields: object) -> Person:
    return Person(canonical_name=name, created_at=_NOW, updated_at=_NOW, **fields)


def test_entries_are_ordered_by_normalized_name_then_id() -> None:
    people = FakePeopleRepository()
    for name in ("Zoe", "adam", "Ámelie", "Bob"):
        people.save_person(_person(name))

    document = _index(people).execute()

    # Case folding and mark stripping decide the order; a raw comparison would put the
    # capitalized and accented names elsewhere.
    assert [entry.canonical_name for entry in document.people] == ["adam", "Ámelie", "Bob", "Zoe"]


def test_same_name_people_are_separated_by_id() -> None:
    people = FakePeopleRepository()
    first = _person("Alex", id="01AAAAAAAAAAAAAAAAAAAAAAAA")
    second = _person("alex", id="01BBBBBBBBBBBBBBBBBBBBBBBB")
    people.save_person(second)
    people.save_person(first)

    document = _index(people).execute()

    assert [entry.id for entry in document.people] == [first.id, second.id]


def test_soft_deleted_people_are_excluded_by_default_and_marked_when_requested() -> None:
    people = FakePeopleRepository()
    active = _person("Alice")
    gone = _person("Gone", deleted_at=_NOW)
    people.save_person(active)
    people.save_person(gone)

    default = _index(people).execute()
    everything = _index(people).execute(include_deleted=True)

    assert [entry.id for entry in default.people] == [active.id]
    assert default.include_deleted is False
    assert [(entry.id, entry.deleted) for entry in everything.people] == [
        (active.id, False),
        (gone.id, True),
    ]
    assert everything.include_deleted is True


def test_limit_bounds_the_people_that_are_read() -> None:
    people = FakePeopleRepository()
    for name in ("Alice", "Bob", "Carol"):
        people.save_person(_person(name))

    document = _index(people).execute(limit=2)

    assert [entry.canonical_name for entry in document.people] == ["Alice", "Bob"]


def test_entry_carries_identity_only() -> None:
    people = FakePeopleRepository()
    person = _person(
        "Alice Zhang",
        summary="A friend",
        is_self=True,
        aliases=[Alias(value="Ali", kind=AliasKind.NICKNAME), Alias(value="a@example.com", kind=AliasKind.HANDLE)],
    )
    people.save_person(person)

    document = _index(people).execute()

    entry = document.people[0]
    assert entry.model_dump() == {
        "id": person.id,
        "canonical_name": "Alice Zhang",
        "aliases": ["Ali", "a@example.com"],
        "summary": "A friend",
        "is_self": True,
        "deleted": False,
    }


def test_json_document_is_versioned_and_byte_stable() -> None:
    people = FakePeopleRepository()
    people.save_person(_person("Alice"))
    index = _index(people)

    text = render_person_index_json(index.execute())

    assert text.endswith("\n")
    assert text == render_person_index_json(index.execute())
    payload = json.loads(text)
    assert payload["format"] == PERSON_INDEX_FORMAT == "people-context-person-index"
    assert payload["version"] == PERSON_INDEX_VERSION == 1
    assert payload["generated_at"] == "2026-03-04T05:06:00Z"
    # A reader from this release tolerates a later additive field rather than failing.
    payload["future_field"] = "ignored"
    assert len(PersonIndexDocument.model_validate(payload).people) == 1


def test_empty_store_still_produces_a_complete_document() -> None:
    document = _index(FakePeopleRepository()).execute()

    assert document.people == []
    assert json.loads(render_person_index_json(document))["people"] == []


def test_a_page_is_bounded_and_resumes_exactly_after_its_last_person() -> None:
    people = FakePeopleRepository()
    for name in ("Toma Ibarra", "elena marsh", "Élena Marsh", "Ada"):
        people.save_person(_person(name))
    index = _index(people)

    seen: list[str] = []
    cursor = None
    while True:
        page = index.page(limit=1, cursor=cursor)
        seen.extend(entry.id for entry in page.people)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert seen == [entry.id for entry in index.execute().people]
    assert index.page().next_cursor is None
    assert len(index.page().people) == 4


def test_page_limits_and_cursors_are_refused_with_stable_codes() -> None:
    people = FakePeopleRepository()
    first = _person("Ada")
    people.save_person(first)
    people.save_person(_person("Bea"))
    index = _index(people)

    for limit in (0, MAX_PERSON_PAGE_LIMIT + 1):
        with pytest.raises(PersonIndexError) as refused:
            index.page(limit=limit)
        assert refused.value.code == INVALID_PERSON_PAGE_LIMIT
    for cursor in ("not a cursor", encode_cursor(SOURCE_LIST_SCOPE, first.id), encode_cursor("people", "gone")):
        with pytest.raises(PersonIndexError) as refused:
            index.page(cursor=cursor)
        assert refused.value.code == INVALID_PERSON_CURSOR
    assert len(index.page(limit=MAX_PERSON_PAGE_LIMIT).people) == 2
    assert index.page(limit=DEFAULT_PERSON_PAGE_LIMIT).next_cursor is None


def test_a_cursor_naming_a_soft_deleted_person_still_resumes() -> None:
    people = FakePeopleRepository()
    ada, bea = _person("Ada"), _person("Bea")
    people.save_person(ada)
    people.save_person(bea)
    cursor = _index(people).page(limit=1).next_cursor
    people.save_person(ada.model_copy(update={"deleted_at": _NOW}))

    assert [entry.id for entry in _index(people).page(cursor=cursor).people] == [bea.id]
