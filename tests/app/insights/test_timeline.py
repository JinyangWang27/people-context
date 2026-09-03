"""Application policy for the bounded person timeline, against a fake reader."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from people_context.app.insights import (
    ALL_SENSITIVITIES,
    DEFAULT_TIMELINE_LIMIT,
    MAX_TIMELINE_EVIDENCE_LINKS,
    MAX_TIMELINE_LIMIT,
    MIN_TIMELINE_LIMIT,
    ORDINARY_SENSITIVITIES,
    PERSON_TIMELINE_FORMAT,
    PERSON_TIMELINE_VERSION,
    GetPersonTimeline,
    PersonTimelineError,
    person_timeline_document,
    render_timeline_json,
)
from people_context.domain.person import Person
from people_context.domain.shared import Sensitivity
from people_context.ports.timeline import (
    BASIS_CREATED_AT,
    BASIS_OCCURRED_AT,
    BASIS_RECORDED_AT,
    BASIS_VALID_FROM,
    ENTRY_FACT,
    ENTRY_INTERACTION,
    ENTRY_OBSERVATION,
    ENTRY_RELATIONSHIP,
    ENTRY_TRAIT,
    PersonTimelineReader,
    TimelineEvidenceRow,
    TimelineRow,
)
from tests.app.fakes import FakePeopleRepository, FakePersonTimelineReader

ALICE = Person(id="P1", canonical_name="Alice")


def _row(
    entry_type: str,
    entry_id: str,
    effective_at: datetime,
    *,
    basis: str = BASIS_OCCURRED_AT,
    summary: str = "something",
    detail: str | None = None,
    sensitivity: Sensitivity | None = Sensitivity.PERSONAL,
    valid_from: date | None = None,
    valid_to: date | None = None,
    source_session_id: str | None = None,
) -> TimelineRow:
    return TimelineRow(
        entry_type=entry_type,
        entry_id=entry_id,
        effective_at=effective_at,
        basis=basis,
        summary=summary,
        detail=detail,
        sensitivity=sensitivity,
        valid_from=valid_from,
        valid_to=valid_to,
        source_session_id=source_session_id,
    )


def _use_case(
    *rows: TimelineRow,
    evidence: dict[str, list[tuple[Sensitivity | None, TimelineEvidenceRow]]] | None = None,
    people: list[Person] | None = None,
) -> tuple[GetPersonTimeline, FakePersonTimelineReader]:
    repo = FakePeopleRepository()
    for person in people if people is not None else [ALICE]:
        repo.save_person(person)
    reader = FakePersonTimelineReader(list(rows), evidence)
    return GetPersonTimeline(repo, reader), reader


def test_the_fake_reader_satisfies_the_declared_port() -> None:
    assert isinstance(FakePersonTimelineReader(), PersonTimelineReader)


def test_entries_are_newest_first_with_type_then_id_breaking_exact_ties() -> None:
    same_instant = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    use_case, _ = _use_case(
        _row(ENTRY_OBSERVATION, "b", same_instant),
        _row(ENTRY_INTERACTION, "z", same_instant),
        _row(ENTRY_INTERACTION, "a", same_instant),
        _row(ENTRY_FACT, "newer", datetime(2026, 4, 1, tzinfo=UTC)),
    )

    result = use_case.execute(ALICE.id)

    assert [(entry.entry_type, entry.entry_id) for entry in result.entries] == [
        (ENTRY_FACT, "newer"),
        (ENTRY_INTERACTION, "a"),
        (ENTRY_INTERACTION, "z"),
        (ENTRY_OBSERVATION, "b"),
    ]


def test_ordering_compares_instants_rather_than_stored_offsets() -> None:
    """A later instant written at another offset must not sort as the earlier one.

    `2026-06-01T23:30:00-05:00` is 04:30Z on 2 June — later than `2026-06-02T02:00:00+00:00` —
    while sorting first as text. Ordering on the stored text would invert exactly this pair.
    """
    later = _row(
        ENTRY_INTERACTION,
        "later",
        datetime(2026, 6, 1, 23, 30, tzinfo=timezone(timedelta(hours=-5))),
    )
    earlier = _row(ENTRY_INTERACTION, "earlier", datetime(2026, 6, 2, 2, 0, tzinfo=UTC))
    use_case, _ = _use_case(later, earlier)

    assert [entry.entry_id for entry in use_case.execute(ALICE.id).entries] == ["later", "earlier"]


def test_a_naive_stored_timestamp_is_read_as_utc_not_in_the_host_timezone() -> None:
    naive = _row(ENTRY_INTERACTION, "naive", datetime(2026, 6, 1, 12, 0))
    aware_later = _row(ENTRY_INTERACTION, "aware", datetime(2026, 6, 1, 13, 0, tzinfo=UTC))
    use_case, _ = _use_case(naive, aware_later)

    assert [entry.entry_id for entry in use_case.execute(ALICE.id).entries] == ["aware", "naive"]


def test_the_page_is_cut_at_the_limit_and_reports_that_more_exist() -> None:
    rows = [_row(ENTRY_INTERACTION, f"i{index}", datetime(2026, 3, index + 1, tzinfo=UTC)) for index in range(5)]
    use_case, reader = _use_case(*rows)

    result = use_case.execute(ALICE.id, limit=2)

    assert len(result.entries) == 2
    assert result.truncated is True
    # The reader is asked for exactly one row past the page, never for the table.
    assert reader.calls == [(ALICE.id, 2, (Sensitivity.PUBLIC, Sensitivity.PERSONAL))]


def test_an_exactly_full_page_is_not_reported_as_truncated() -> None:
    rows = [_row(ENTRY_INTERACTION, f"i{index}", datetime(2026, 3, index + 1, tzinfo=UTC)) for index in range(2)]
    use_case, _ = _use_case(*rows)

    result = use_case.execute(ALICE.id, limit=2)

    assert len(result.entries) == 2
    assert result.truncated is False


@pytest.mark.parametrize("limit", [MIN_TIMELINE_LIMIT - 1, 0, -1, MAX_TIMELINE_LIMIT + 1])
def test_an_out_of_range_limit_is_refused_before_any_read(limit: int) -> None:
    use_case, reader = _use_case(_row(ENTRY_INTERACTION, "i", datetime(2026, 3, 1, tzinfo=UTC)))

    with pytest.raises(PersonTimelineError):
        use_case.execute(ALICE.id, limit=limit)

    assert reader.calls == []


def test_an_unknown_person_is_not_found_rather_than_an_error() -> None:
    use_case, reader = _use_case(_row(ENTRY_INTERACTION, "i", datetime(2026, 3, 1, tzinfo=UTC)))

    result = use_case.execute("missing")

    assert result.found is False
    assert result.entries == []
    assert reader.calls == []


def test_a_soft_deleted_person_has_no_timeline() -> None:
    removed = Person(id="P9", canonical_name="Gone", deleted_at=datetime(2026, 1, 1, tzinfo=UTC))
    use_case, reader = _use_case(
        _row(ENTRY_INTERACTION, "i", datetime(2026, 3, 1, tzinfo=UTC)),
        people=[removed],
    )

    result = use_case.execute(removed.id)

    assert result.found is False
    assert reader.calls == []


def test_ordinary_reads_ask_only_for_ordinary_levels_and_the_opt_in_asks_for_all() -> None:
    use_case, reader = _use_case(_row(ENTRY_INTERACTION, "i", datetime(2026, 3, 1, tzinfo=UTC)))

    use_case.execute(ALICE.id, include_sensitive=False)
    use_case.execute(ALICE.id, include_sensitive=True)

    assert reader.calls[0][2] == (Sensitivity.PUBLIC, Sensitivity.PERSONAL)
    assert reader.calls[1][2] == (
        Sensitivity.PUBLIC,
        Sensitivity.PERSONAL,
        Sensitivity.SENSITIVE,
        Sensitivity.RESTRICTED,
    )
    assert use_case.execute(ALICE.id, include_sensitive=True).include_sensitive is True


def test_records_without_a_stored_level_are_always_ordinary() -> None:
    """Affiliations and relationships carry no disclosure level, and none is invented for them."""
    edge = _row(
        ENTRY_RELATIONSHIP,
        "r1",
        datetime(2026, 3, 1, tzinfo=UTC),
        basis=BASIS_CREATED_AT,
        sensitivity=None,
    )
    use_case, _ = _use_case(edge)

    entry = use_case.execute(ALICE.id).entries[0]

    assert entry.sensitivity is None


def test_every_entry_reports_the_stored_field_it_was_placed_by() -> None:
    dated = _row(
        ENTRY_FACT,
        "dated",
        datetime(2026, 2, 1, tzinfo=UTC),
        basis=BASIS_VALID_FROM,
        valid_from=date(2026, 2, 1),
        valid_to=date(2026, 12, 31),
    )
    undated = _row(ENTRY_FACT, "undated", datetime(2026, 1, 1, tzinfo=UTC), basis=BASIS_RECORDED_AT)
    use_case, _ = _use_case(dated, undated)

    entries = {entry.entry_id: entry for entry in use_case.execute(ALICE.id).entries}

    assert entries["dated"].basis == BASIS_VALID_FROM
    assert entries["dated"].valid_from == date(2026, 2, 1)
    assert entries["dated"].valid_to == date(2026, 12, 31)
    assert entries["undated"].basis == BASIS_RECORDED_AT
    assert entries["undated"].valid_from is None


def test_provenance_is_carried_through_when_an_import_produced_the_record() -> None:
    use_case, _ = _use_case(
        _row(ENTRY_INTERACTION, "i1", datetime(2026, 3, 1, tzinfo=UTC), source_session_id="S1"),
        _row(ENTRY_INTERACTION, "i2", datetime(2026, 2, 1, tzinfo=UTC)),
    )

    entries = {entry.entry_id: entry for entry in use_case.execute(ALICE.id).entries}

    assert entries["i1"].source_session_id == "S1"
    assert entries["i2"].source_session_id is None


def test_a_trait_names_only_evidence_that_is_itself_ordinary() -> None:
    """The evidence's own level decides, because a visible trait must not disclose a hidden record."""
    trait = _row(ENTRY_TRAIT, "T1", datetime(2026, 3, 1, tzinfo=UTC), sensitivity=Sensitivity.PERSONAL)
    use_case, _ = _use_case(
        trait,
        evidence={
            "T1": [
                (Sensitivity.PUBLIC, TimelineEvidenceRow("observation", "O-public")),
                (Sensitivity.RESTRICTED, TimelineEvidenceRow("observation", "O-restricted")),
                (Sensitivity.SENSITIVE, TimelineEvidenceRow("interaction", "I-sensitive")),
            ]
        },
    )

    ordinary = use_case.execute(ALICE.id).entries[0]
    elevated = use_case.execute(ALICE.id, include_sensitive=True).entries[0]

    assert [link.evidence_id for link in ordinary.evidence] == ["O-public"]
    assert [link.evidence_id for link in elevated.evidence] == ["O-public", "O-restricted", "I-sensitive"]


def test_the_evidence_lookup_asks_only_for_levels_the_caller_may_read() -> None:
    """Filtering belongs to the read, so the truncation flag can never count a withheld link."""
    use_case, reader = _use_case(_row(ENTRY_TRAIT, "T1", datetime(2026, 3, 1, tzinfo=UTC)))

    use_case.execute(ALICE.id)
    use_case.execute(ALICE.id, include_sensitive=True)

    assert reader.evidence_calls[0] == ("T1", MAX_TIMELINE_EVIDENCE_LINKS, ORDINARY_SENSITIVITIES)
    assert reader.evidence_calls[1] == ("T1", MAX_TIMELINE_EVIDENCE_LINKS, ALL_SENSITIVITIES)


def test_a_citation_carries_the_type_that_makes_its_id_resolvable() -> None:
    """Ids are unique only within their table, so a bare id can name two different records."""
    use_case, _ = _use_case(
        _row(ENTRY_TRAIT, "T1", datetime(2026, 3, 1, tzinfo=UTC)),
        evidence={
            "T1": [
                (Sensitivity.PERSONAL, TimelineEvidenceRow("observation", "shared")),
                (Sensitivity.PERSONAL, TimelineEvidenceRow("interaction", "shared")),
            ]
        },
    )

    entry = use_case.execute(ALICE.id).entries[0]

    assert [(link.evidence_type, link.evidence_id) for link in entry.evidence] == [
        ("observation", "shared"),
        ("interaction", "shared"),
    ]


def test_truncation_counts_only_links_the_caller_may_read() -> None:
    """A visible trait with nothing disclosable must not flag that hidden links exist."""
    hidden = [
        (Sensitivity.RESTRICTED, TimelineEvidenceRow("observation", f"O{index:03d}"))
        for index in range(MAX_TIMELINE_EVIDENCE_LINKS + 1)
    ]
    use_case, _ = _use_case(
        _row(ENTRY_TRAIT, "T1", datetime(2026, 3, 1, tzinfo=UTC), sensitivity=Sensitivity.PERSONAL),
        evidence={"T1": hidden},
    )

    entry = use_case.execute(ALICE.id).entries[0]

    assert entry.evidence == []
    assert entry.evidence_truncated is False


def test_a_trait_with_more_readable_links_than_one_page_reports_says_so() -> None:
    links = [
        (Sensitivity.PERSONAL, TimelineEvidenceRow("observation", f"O{index:03d}"))
        for index in range(MAX_TIMELINE_EVIDENCE_LINKS + 1)
    ]
    use_case, reader = _use_case(
        _row(ENTRY_TRAIT, "T1", datetime(2026, 3, 1, tzinfo=UTC)),
        evidence={"T1": links},
    )

    entry = use_case.execute(ALICE.id).entries[0]

    assert len(entry.evidence) == MAX_TIMELINE_EVIDENCE_LINKS
    assert entry.evidence_truncated is True
    assert reader.evidence_calls == [("T1", MAX_TIMELINE_EVIDENCE_LINKS, ORDINARY_SENSITIVITIES)]


def test_evidence_is_read_once_per_trait_and_never_for_another_entry_type() -> None:
    use_case, reader = _use_case(
        _row(ENTRY_TRAIT, "T1", datetime(2026, 3, 1, tzinfo=UTC)),
        _row(ENTRY_INTERACTION, "I1", datetime(2026, 2, 1, tzinfo=UTC)),
        _row(ENTRY_TRAIT, "T2", datetime(2026, 1, 1, tzinfo=UTC)),
    )

    use_case.execute(ALICE.id)

    assert [call[0] for call in reader.evidence_calls] == ["T1", "T2"]


def test_evidence_is_read_only_for_traits_that_reached_the_page() -> None:
    """A trait cut by the limit must not have its evidence read, let alone reported."""
    use_case, reader = _use_case(
        _row(ENTRY_TRAIT, "T-new", datetime(2026, 3, 1, tzinfo=UTC)),
        _row(ENTRY_TRAIT, "T-old", datetime(2026, 1, 1, tzinfo=UTC)),
    )

    use_case.execute(ALICE.id, limit=1)

    assert [call[0] for call in reader.evidence_calls] == ["T-new"]


def test_the_default_limit_is_used_when_the_caller_says_nothing() -> None:
    use_case, reader = _use_case(_row(ENTRY_INTERACTION, "i", datetime(2026, 3, 1, tzinfo=UTC)))

    result = use_case.execute(ALICE.id)

    assert result.limit == DEFAULT_TIMELINE_LIMIT
    assert reader.calls[0][1] == DEFAULT_TIMELINE_LIMIT


def test_the_document_is_the_result_plus_its_declared_version() -> None:
    use_case, _ = _use_case(_row(ENTRY_INTERACTION, "i1", datetime(2026, 3, 1, tzinfo=UTC), summary="Coffee"))

    document = person_timeline_document(use_case.execute(ALICE.id))

    assert document.format == PERSON_TIMELINE_FORMAT
    assert document.version == PERSON_TIMELINE_VERSION
    assert document.person_id == ALICE.id
    assert [entry.entry_id for entry in document.entries] == ["i1"]


def test_the_rendered_document_is_deterministic_and_ends_in_a_newline() -> None:
    use_case, _ = _use_case(_row(ENTRY_INTERACTION, "i1", datetime(2026, 3, 1, tzinfo=UTC)))

    first = render_timeline_json(person_timeline_document(use_case.execute(ALICE.id)))
    second = render_timeline_json(person_timeline_document(use_case.execute(ALICE.id)))

    assert first == second
    assert first.endswith("\n")
