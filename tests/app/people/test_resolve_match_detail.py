"""Tests for the additive `match_detail` explanation on exact resolution matches (M15.3)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from people_context.adapters.sqlite import SqliteAuditLog, SqlitePeopleRepository, open_db
from people_context.app.people.remember import AliasInput, RememberPerson, RememberPersonInput
from people_context.app.people.resolve import ResolutionHints, ResolvePerson
from people_context.app.people.search import SearchPeople
from people_context.domain.organization import Affiliation
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import Provenance
from people_context.ports.context import AffiliationRecord
from people_context.ports.repository import SearchHit
from tests.app.fakes import FakeClock, FakeContextReader, FakePeopleRepository

_TS = datetime(2024, 1, 1, tzinfo=UTC)

# Bilingual fixtures. Each pair is stored twice — once with each script as the canonical name — so the
# same two queries exercise both directions of a cross-script lookup.
_CJK_PAIR = ("Wang Xiaoming", "王小明")
_NON_CJK_PAIR = ("Ekaterina Ivanova", "Екатерина Иванова")


def _person(name: str, *, aliases: list[Alias] | None = None) -> Person:
    return Person(canonical_name=name, aliases=aliases or [], created_at=_TS, updated_at=_TS)


@pytest.mark.parametrize(
    ("canonical", "alias_value", "alias_kind"),
    [
        (_CJK_PAIR[0], _CJK_PAIR[1], AliasKind.NATIVE_SCRIPT),
        (_CJK_PAIR[1], _CJK_PAIR[0], AliasKind.TRANSLITERATION),
        (_NON_CJK_PAIR[0], _NON_CJK_PAIR[1], AliasKind.NATIVE_SCRIPT),
        (_NON_CJK_PAIR[1], _NON_CJK_PAIR[0], AliasKind.TRANSLITERATION),
    ],
)
def test_exact_match_detail_names_the_stored_value_that_matched(
    canonical: str,
    alias_value: str,
    alias_kind: AliasKind,
) -> None:
    repo = FakePeopleRepository()
    person = _person(canonical, aliases=[Alias(value=alias_value, kind=alias_kind)])
    repo.save_person(person)
    resolver = ResolvePerson(repo)

    by_canonical = resolver.execute(canonical).candidates[0]
    by_alias = resolver.execute(alias_value).candidates[0]

    assert by_canonical.person_id == person.id
    assert by_canonical.match_detail == "canonical_name"
    assert by_alias.person_id == person.id
    assert by_alias.match_detail == f"alias:{alias_kind.value}"
    # The detail is purely additive: reason and score are untouched in both directions.
    assert (by_canonical.match_reason, by_canonical.score) == ("exact", 1.0)
    assert (by_alias.match_reason, by_alias.score) == ("exact", 1.0)


@pytest.mark.parametrize(("canonical", "queried"), [_CJK_PAIR, _NON_CJK_PAIR])
def test_detail_reports_alias_kind_and_not_the_alias_value(canonical: str, queried: str) -> None:
    repo = FakePeopleRepository()
    repo.save_person(_person(canonical, aliases=[Alias(value=queried, kind=AliasKind.NATIVE_SCRIPT)]))

    candidate = ResolvePerson(repo).execute(queried).candidates[0]

    assert candidate.match_detail == "alias:native_script"
    assert queried not in (candidate.match_detail or "")


def test_normalized_variants_of_the_canonical_name_still_report_canonical() -> None:
    repo = FakePeopleRepository()
    repo.save_person(_person("José Álvarez", aliases=[Alias(value="Jose Alvarez", kind=AliasKind.OTHER)]))

    candidate = ResolvePerson(repo).execute("jose alvarez").candidates[0]

    # Canonical and alias both normalize to the query; canonical wins.
    assert candidate.match_detail == "canonical_name"


def test_matching_aliases_are_ordered_by_kind_then_id() -> None:
    repo = FakePeopleRepository()
    person = _person(
        "Wang Xiaoming",
        aliases=[
            # The transliteration sorts first by id but later by kind, so kind decides.
            Alias(id="alias-a", value="王小明", kind=AliasKind.TRANSLITERATION),
            Alias(id="alias-z", value="王小明", kind=AliasKind.NATIVE_SCRIPT),
        ],
    )
    repo.save_person(person)

    assert ResolvePerson(repo).execute("王小明").candidates[0].match_detail == "alias:native_script"


def test_detail_is_independent_of_stored_alias_order() -> None:
    aliases = [
        Alias(id="alias-2", value="王小明", kind=AliasKind.NICKNAME),
        Alias(id="alias-1", value="王小明", kind=AliasKind.NICKNAME),
    ]
    details: list[str | None] = []
    for ordered in (aliases, list(reversed(aliases))):
        repo = FakePeopleRepository()
        repo.save_person(_person("Wang Xiaoming", aliases=list(ordered)))
        details.append(ResolvePerson(repo).execute("王小明").candidates[0].match_detail)

    assert details == ["alias:nickname", "alias:nickname"]


def test_detail_is_null_when_the_reader_returns_a_non_matching_person() -> None:
    class LyingRepository(FakePeopleRepository):
        def find_by_normalized_name(self, normalized: str) -> list[Person]:
            return list(self._people.values())

    repo = LyingRepository()
    person = _person("Wang Xiaoming")
    repo.save_person(person)

    candidate = ResolvePerson(repo).execute("完全不同").candidates[0]

    assert candidate.person_id == person.id
    assert candidate.match_reason == "exact"
    assert candidate.match_detail is None


def test_search_stage_candidates_have_no_detail() -> None:
    repo = FakePeopleRepository()
    person = _person("Ekaterina Ivanova", aliases=[Alias(value="Екатерина Иванова", kind=AliasKind.NATIVE_SCRIPT)])
    repo.save_person(person)

    candidate = ResolvePerson(repo).execute("Ekaterina").candidates[0]

    assert candidate.match_reason.startswith("search:")
    assert candidate.match_detail is None


def test_fuzzy_stage_candidates_have_no_detail() -> None:
    repo = FakePeopleRepository()
    repo.save_person(_person("Alice"))

    candidate = ResolvePerson(repo).execute("Alicf").candidates[0]

    assert candidate.match_reason == "fuzzy"
    assert candidate.match_detail is None


def test_search_people_leaves_detail_null() -> None:
    repo = FakePeopleRepository()
    repo.save_person(_person("Wang Xiaoming", aliases=[Alias(value="王小明", kind=AliasKind.NATIVE_SCRIPT)]))

    candidates = SearchPeople(repo).execute("Wang")

    assert candidates
    assert all(candidate.match_detail is None for candidate in candidates)


def test_hint_boosting_preserves_the_exact_detail() -> None:
    repo = FakePeopleRepository()
    context = FakeContextReader()
    person = _person("Wang Xiaoming", aliases=[Alias(value="王小明", kind=AliasKind.NATIVE_SCRIPT)])
    repo.save_person(person)
    context.affiliations.append(
        AffiliationRecord(
            affiliation=Affiliation(
                person_id=person.id,
                org_id="org-1",
                role="Engineer",
                provenance=Provenance(source="test"),
            ),
            organization_name="Acme",
        )
    )

    result = ResolvePerson(repo, context, FakeClock()).execute("王小明", hints=ResolutionHints(org="Acme"))
    candidate = result.candidates[0]

    assert candidate.match_reason == "exact+hint:org"
    assert candidate.score == 1.0
    assert candidate.match_detail == "alias:native_script"


def test_detail_does_not_disturb_ranking_or_ambiguity() -> None:
    repo = FakePeopleRepository()
    canonical_holder = _person("王小明")
    alias_holder = _person("Wang Xiaoming", aliases=[Alias(value="王小明", kind=AliasKind.NATIVE_SCRIPT)])
    weak = _person("Wang Wei")
    for person in (alias_holder, canonical_holder, weak):
        repo.save_person(person)
    repo.forced_hits["王小明"] = [SearchHit(person=weak, score=0.1, matched_value="Wang Wei", match_kind="canonical")]

    result = ResolvePerson(repo).execute("王小明")

    # Two exact matches at 1.0 sort by canonical name and stay ambiguous, exactly as before.
    assert [candidate.person_id for candidate in result.candidates[:2]] == [alias_holder.id, canonical_holder.id]
    assert [candidate.score for candidate in result.candidates[:2]] == [1.0, 1.0]
    assert [candidate.match_reason for candidate in result.candidates[:2]] == ["exact", "exact"]
    assert result.ambiguous is True
    assert [candidate.match_detail for candidate in result.candidates[:2]] == [
        "alias:native_script",
        "canonical_name",
    ]


@pytest.mark.parametrize(
    ("canonical", "alias_value", "alias_kind"),
    [
        (_CJK_PAIR[0], _CJK_PAIR[1], AliasKind.NATIVE_SCRIPT),
        (_CJK_PAIR[1], _CJK_PAIR[0], AliasKind.TRANSLITERATION),
        (_NON_CJK_PAIR[0], _NON_CJK_PAIR[1], AliasKind.NATIVE_SCRIPT),
        (_NON_CJK_PAIR[1], _NON_CJK_PAIR[0], AliasKind.TRANSLITERATION),
    ],
)
def test_real_adapter_reports_detail_in_both_directions(
    canonical: str,
    alias_value: str,
    alias_kind: AliasKind,
) -> None:
    conn = open_db(":memory:")
    repo = SqlitePeopleRepository(conn)
    RememberPerson(repo, repo, SqliteAuditLog(conn), FakeClock(_TS)).execute(
        RememberPersonInput(name=canonical, aliases=[AliasInput(value=alias_value, kind=alias_kind)])
    )
    resolver = ResolvePerson(repo)

    by_canonical = resolver.execute(canonical).candidates[0]
    by_alias = resolver.execute(alias_value).candidates[0]

    assert by_canonical.match_detail == "canonical_name"
    assert by_alias.match_detail == f"alias:{alias_kind.value}"
    assert by_canonical.person_id == by_alias.person_id
    assert (by_canonical.score, by_alias.score) == (1.0, 1.0)
