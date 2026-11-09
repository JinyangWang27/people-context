"""Search, order, and filters of the paged person index, against SQLite (the browser's people list)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.app.exports.person_index import (
    INVALID_PERSON_CURSOR,
    INVALID_PERSON_SORT,
    PersonIndexError,
)
from people_context.app.groups.commands import AddGroupMembershipInput, CreateGroupInput
from people_context.app.records import SetAffiliationInput
from people_context.domain.group import GroupKind
from people_context.domain.person import Alias, Person
from people_context.domain.shared import Sensitivity

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[ApplicationRuntime]:
    opened = build_runtime(tmp_path / "people.db")
    try:
        yield opened
    finally:
        opened.close()


def _person(runtime: ApplicationRuntime, name: str, *, day: int, edited: int | None = None, **fields: Any) -> str:
    person = Person(
        canonical_name=name,
        created_at=_T0 + timedelta(days=day),
        updated_at=_T0 + timedelta(days=edited if edited is not None else day),
        **fields,
    )
    runtime.repo.save_person(person)
    return person.id


@pytest.fixture
def people(runtime: ApplicationRuntime) -> dict[str, str]:
    ids = {
        "ana": _person(runtime, "Ana Öberg", day=3, edited=9, summary="Works on the MOFT project"),
        "bo": _person(runtime, "Bo Chen", day=1, aliases=[Alias(value="Bobby")]),
        "cy": _person(runtime, "Cy Wu", day=2, edited=5),
        "di": _person(runtime, "Di Lee", day=4),
    }
    cases = runtime.use_cases
    cases.set_affiliation.execute(SetAffiliationInput(person_id=ids["ana"], org="Presight", role="engineer"))
    cases.set_affiliation.execute(SetAffiliationInput(person_id=ids["bo"], org="Presight", role="engineer"))
    cases.set_affiliation.execute(SetAffiliationInput(person_id=ids["cy"], org="Alhamitech", role="engineer"))
    team = cases.create_group.execute(CreateGroupInput(name="MOFT", kind=GroupKind.TEAM))
    clinic = cases.create_group.execute(
        CreateGroupInput(name="Clinic", kind=GroupKind.TEAM, sensitivity=Sensitivity.SENSITIVE)
    )
    cases.add_group_membership.execute(AddGroupMembershipInput(person_id=ids["ana"], group_id=team.id))
    cases.add_group_membership.execute(
        AddGroupMembershipInput(person_id=ids["di"], group_id=team.id, sensitivity=Sensitivity.RESTRICTED)
    )
    cases.add_group_membership.execute(AddGroupMembershipInput(person_id=ids["cy"], group_id=clinic.id))
    return {**ids, "team": team.id, "clinic": clinic.id}


def _names(runtime: ApplicationRuntime, **arguments: Any) -> list[str]:
    return [entry.canonical_name for entry in runtime.use_cases.list_person_index.page(**arguments).people]


def _org(runtime: ApplicationRuntime, name: str) -> str:
    options = runtime.use_cases.list_person_index.filter_options(include_sensitive=False)["organizations"]
    return next(option["id"] for option in options if option["name"] == name)


def test_search_matches_name_alias_and_summary_normalized(runtime: ApplicationRuntime, people: dict[str, str]) -> None:
    assert _names(runtime, query="oberg") == ["Ana Öberg"]  # diacritics and case fold away
    assert _names(runtime, query="bob") == ["Bo Chen"]  # an alias
    assert _names(runtime, query="moft") == ["Ana Öberg"]  # the summary
    assert _names(runtime, query="  ") == ["Ana Öberg", "Bo Chen", "Cy Wu", "Di Lee"]  # blank is no filter
    assert _names(runtime, query="%") == []  # matched literally, not as a LIKE wildcard


def test_timestamp_orders_run_newest_first_and_page_every_person_once(
    runtime: ApplicationRuntime, people: dict[str, str]
) -> None:
    index = runtime.use_cases.list_person_index
    assert _names(runtime, sort="created") == ["Di Lee", "Ana Öberg", "Cy Wu", "Bo Chen"]
    assert _names(runtime, sort="updated") == ["Ana Öberg", "Cy Wu", "Di Lee", "Bo Chen"]
    seen: list[str] = []
    cursor = None
    while True:
        page = index.page(limit=1, cursor=cursor, sort="updated")
        seen += [entry.canonical_name for entry in page.people]
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    assert seen == ["Ana Öberg", "Cy Wu", "Di Lee", "Bo Chen"]


def test_a_cursor_is_refused_under_another_order_or_after_its_anchor_moved(
    runtime: ApplicationRuntime, people: dict[str, str]
) -> None:
    index = runtime.use_cases.list_person_index
    by_name = index.page(limit=1).next_cursor
    by_update = index.page(limit=1, sort="updated").next_cursor
    with pytest.raises(PersonIndexError) as other_order:
        index.page(limit=1, cursor=by_name, sort="created")
    anchor = runtime.repo.get(people["ana"])
    assert anchor is not None
    anchor.updated_at = _T0 + timedelta(days=30)
    runtime.repo.save_person(anchor)
    with pytest.raises(PersonIndexError) as moved:
        index.page(limit=1, cursor=by_update, sort="updated")
    with pytest.raises(PersonIndexError) as unknown:
        index.page(sort="shoe size")
    assert (other_order.value.code, moved.value.code, unknown.value.code) == (
        INVALID_PERSON_CURSOR,
        INVALID_PERSON_CURSOR,
        INVALID_PERSON_SORT,
    )


def test_an_organization_filter_keeps_its_affiliated_people_and_combines_with_search(
    runtime: ApplicationRuntime, people: dict[str, str]
) -> None:
    presight = _org(runtime, "Presight")
    assert _names(runtime, organization_id=presight) == ["Ana Öberg", "Bo Chen"]
    assert _names(runtime, organization_id=presight, query="bo") == ["Bo Chen"]
    assert _names(runtime, organization_id=presight, sort="created") == ["Ana Öberg", "Bo Chen"]


def test_a_group_filter_counts_only_memberships_the_view_may_show(
    runtime: ApplicationRuntime, people: dict[str, str]
) -> None:
    # Di's membership is restricted and the Clinic group itself is sensitive.
    assert _names(runtime, group_id=people["team"]) == ["Ana Öberg"]
    assert _names(runtime, group_id=people["clinic"]) == []
    assert _names(runtime, group_id=people["team"], include_sensitive=True) == ["Ana Öberg", "Di Lee"]
    assert _names(runtime, group_id=people["clinic"], include_sensitive=True) == ["Cy Wu"]


def test_filter_options_list_only_what_has_a_visible_active_member(
    runtime: ApplicationRuntime, people: dict[str, str]
) -> None:
    index = runtime.use_cases.list_person_index
    ordinary = index.filter_options(include_sensitive=False)
    elevated = index.filter_options(include_sensitive=True)
    assert [option["name"] for option in ordinary["organizations"]] == ["Alhamitech", "Presight"]
    assert [option["name"] for option in ordinary["groups"]] == ["MOFT"]
    assert [option["name"] for option in elevated["groups"]] == ["Clinic", "MOFT"]


def test_organization_names_list_each_person_s_organizations_once_in_name_order(
    runtime: ApplicationRuntime, people: dict[str, str]
) -> None:
    # A second role at the same organization, and an ended one elsewhere, for Ana.
    cases = runtime.use_cases
    cases.set_affiliation.execute(SetAffiliationInput(person_id=people["ana"], org="presight", role="lead"))
    cases.set_affiliation.execute(
        SetAffiliationInput(person_id=people["ana"], org="Acme", role="intern", valid_to=date(2020, 1, 1))
    )
    index = runtime.use_cases.list_person_index
    names = index.organization_names(index.page())
    assert names == {
        people["ana"]: ["Acme", "Presight"],
        people["bo"]: ["Presight"],
        people["cy"]: ["Alhamitech"],
    }
    assert runtime.repo.organization_names([]) == {}
