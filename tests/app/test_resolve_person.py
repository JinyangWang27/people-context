"""Tests for ResolvePerson and SearchPeople against in-memory fakes and the real adapter."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from people_context.adapters.sqlite import SqliteAuditLog, SqlitePeopleRepository, open_db
from people_context.app.record import RememberPerson, RememberPersonInput
from people_context.app.resolve_person import ResolvePerson
from people_context.app.search_people import SearchPeople
from people_context.domain.person import Alias, AliasKind, Person
from people_context.ports.repository import SearchHit
from tests.app.fakes import FakeClock, FakePeopleRepository

_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _person(name: str, *, aliases: list[Alias] | None = None, summary: str | None = None) -> Person:
    return Person(canonical_name=name, aliases=aliases or [], summary=summary, created_at=_TS, updated_at=_TS)


def test_exact_match_beats_search_results() -> None:
    repo = FakePeopleRepository()
    target = _person(
        "Wang Xiaoming",
        aliases=[Alias(value="Ming", kind=AliasKind.NICKNAME)],
        summary="Colleague at Acme",
    )
    repo.save_person(target)
    repo.save_person(_person("Wang Wei"))

    result = ResolvePerson(repo).execute("Wang Xiaoming")

    top = result.candidates[0]
    assert top.person_id == target.id
    assert top.score == 1.0
    assert top.match_reason == "exact"
    assert top.aliases == ["Ming"]
    assert top.summary == "Colleague at Acme"


def test_search_scores_mapped_into_band_and_dedupe_keeps_best() -> None:
    repo = FakePeopleRepository()
    a, b = _person("Wang Xiaoming"), _person("Wang Wei")
    repo.save_person(a)
    repo.save_person(b)
    repo.forced_hits["wang"] = [
        SearchHit(person=a, score=1.0, matched_value="Wang Xiaoming", match_kind="canonical"),
        SearchHit(person=a, score=0.2, matched_value="Wang", match_kind="alias"),  # duplicate, worse
        SearchHit(person=b, score=0.5, matched_value="Wang Wei", match_kind="canonical"),
    ]

    result = ResolvePerson(repo).execute("wang")

    assert [c.person_id for c in result.candidates] == [a.id, b.id]
    assert result.candidates[0].score == pytest.approx(0.8)  # 0.4 + 0.4 * 1.0
    assert result.candidates[0].match_reason == "search:canonical"
    assert result.candidates[1].score == pytest.approx(0.6)  # 0.4 + 0.4 * 0.5
    assert all(0.4 <= c.score <= 0.8 for c in result.candidates)


def test_below_threshold_candidates_dropped() -> None:
    repo = FakePeopleRepository()
    keep, drop = _person("Keeper"), _person("Dropped")
    repo.save_person(keep)
    repo.save_person(drop)
    repo.forced_hits["q"] = [
        SearchHit(person=keep, score=0.5, matched_value="Keeper", match_kind="canonical"),
        SearchHit(person=drop, score=-0.2, matched_value="Dropped", match_kind="canonical"),  # -> 0.32 < 0.35
    ]

    result = ResolvePerson(repo).execute("q")

    assert [c.person_id for c in result.candidates] == [keep.id]


def test_limit_respected_and_sorted_desc() -> None:
    repo = FakePeopleRepository()
    people = [_person(f"Name{i}") for i in range(5)]
    for p in people:
        repo.save_person(p)
    repo.forced_hits["n"] = [
        SearchHit(person=p, score=0.1 * i, matched_value=p.canonical_name, match_kind="canonical")
        for i, p in enumerate(people, start=1)
    ]

    result = ResolvePerson(repo).execute("n", limit=2)

    assert len(result.candidates) == 2
    scores = [c.score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)


def test_ambiguous_true_for_two_close_candidates() -> None:
    repo = FakePeopleRepository()
    a, b = _person("Wang An"), _person("Wang Bo")
    repo.save_person(a)
    repo.save_person(b)
    repo.forced_hits["Wang"] = [
        SearchHit(person=a, score=0.9, matched_value="Wang An", match_kind="canonical"),
        SearchHit(person=b, score=0.85, matched_value="Wang Bo", match_kind="canonical"),
    ]

    result = ResolvePerson(repo).execute("Wang")

    assert len(result.candidates) == 2
    assert result.ambiguous is True


def test_ambiguous_false_for_single_dominant_candidate() -> None:
    repo = FakePeopleRepository()
    a, b = _person("Wang An"), _person("Wang Bo")
    repo.save_person(a)
    repo.save_person(b)
    repo.forced_hits["Wang"] = [
        SearchHit(person=a, score=0.9, matched_value="Wang An", match_kind="canonical"),
        SearchHit(person=b, score=0.1, matched_value="Wang Bo", match_kind="canonical"),  # 0.44, gap 0.32
    ]

    result = ResolvePerson(repo).execute("Wang")

    assert result.ambiguous is False


def test_ambiguous_false_for_empty_result() -> None:
    repo = FakePeopleRepository()
    result = ResolvePerson(repo).execute("nobody")
    assert result.candidates == []
    assert result.ambiguous is False


def test_candidates_carry_aliases_and_summary_from_search() -> None:
    repo = FakePeopleRepository()
    person = _person("Alexandra", aliases=[Alias(value="Alex", kind=AliasKind.NICKNAME)], summary="Neighbour")
    repo.save_person(person)

    result = ResolvePerson(repo).execute("alexa")  # substring, not an exact alias

    top = result.candidates[0]
    assert top.aliases == ["Alex"]
    assert top.summary == "Neighbour"
    assert top.match_reason.startswith("search:")


@pytest.mark.parametrize(
    ("query", "name", "expected_score"),
    [("Alicf", "Alice", 0.45), ("Alixe", "Alicea", 0.38)],
)
def test_fuzzy_matches_names_at_distance_one_or_two(query: str, name: str, expected_score: float) -> None:
    repo = FakePeopleRepository()
    person = _person(name)
    repo.save_person(person)

    result = ResolvePerson(repo).execute(query)

    assert [(candidate.person_id, candidate.score, candidate.match_reason) for candidate in result.candidates] == [
        (person.id, expected_score, "fuzzy")
    ]


def test_fuzzy_compares_aliases() -> None:
    repo = FakePeopleRepository()
    person = _person("Robert", aliases=[Alias(value="Bobby", kind=AliasKind.NICKNAME)])
    repo.save_person(person)

    result = ResolvePerson(repo).execute("Bobbi")

    assert result.candidates[0].person_id == person.id
    assert result.candidates[0].match_reason == "fuzzy"


def test_fuzzy_skips_queries_shorter_than_three_characters() -> None:
    repo = FakePeopleRepository()
    repo.save_person(_person("Amy"))
    repo.forced_hits["Am"] = []

    assert ResolvePerson(repo).execute("Am").candidates == []


@pytest.mark.parametrize("query", ["Alice", "ali"])
def test_exact_or_strong_search_match_suppresses_fuzzy(query: str) -> None:
    repo = FakePeopleRepository()
    strong = _person("Alice")
    fuzzy = _person("Alixe")
    repo.save_person(strong)
    repo.save_person(fuzzy)

    result = ResolvePerson(repo).execute(query)

    assert all(candidate.match_reason != "fuzzy" for candidate in result.candidates)


def test_fuzzy_candidates_follow_order_limit_and_ambiguity_rules() -> None:
    repo = FakePeopleRepository()
    distance_one = _person("Alice")
    distance_two = _person("Aline")
    repo.save_person(distance_two)
    repo.save_person(distance_one)

    result = ResolvePerson(repo).execute("Alise", limit=2)

    assert [candidate.person_id for candidate in result.candidates] == [distance_one.id, distance_two.id]
    assert result.ambiguous is True


def test_weak_search_candidate_can_be_replaced_by_better_fuzzy_score() -> None:
    repo = FakePeopleRepository()
    person = _person("Alice")
    repo.save_person(person)
    repo.forced_hits["Alise"] = [
        SearchHit(person=person, score=-0.1, matched_value="Alice", match_kind="canonical")
    ]

    result = ResolvePerson(repo).execute("Alise")

    assert result.candidates[0].score == 0.45
    assert result.candidates[0].match_reason == "fuzzy"


def test_search_people_returns_ranked_candidates_with_search_reasons() -> None:
    repo = FakePeopleRepository()
    person = _person("Alice", aliases=[Alias(value="Ally", kind=AliasKind.NICKNAME)])
    repo.save_person(person)

    candidates = SearchPeople(repo).execute("ali")

    assert candidates[0].person_id == person.id
    assert candidates[0].match_reason == "search:canonical"
    assert 0.0 < candidates[0].score <= 1.0


def test_integration_remember_then_resolve_with_real_adapter() -> None:
    conn = open_db(":memory:")
    repo = SqlitePeopleRepository(conn)
    audit = SqliteAuditLog(conn)
    clock = FakeClock(_TS)

    remember = RememberPerson(repo, repo, audit, clock)
    remember.execute(
        RememberPersonInput(
            name="Wang Xiaoming",
            aliases=[],
            summary="Colleague",
        )
    )

    result = ResolvePerson(repo).execute("Wang Xiaoming")
    assert result.candidates[0].canonical_name == "Wang Xiaoming"
    assert result.candidates[0].score == 1.0
    assert result.candidates[0].match_reason == "exact"

    # partial name resolves via search stage
    partial = ResolvePerson(repo).execute("Wang")
    assert partial.candidates
    assert partial.candidates[0].match_reason.startswith("search:")
