"""Doctor report policy: codes, precedence, ordering, structured actions, and JSON shape."""

from __future__ import annotations

import json
from datetime import date

import pytest

from people_context.app.records import (
    DOCTOR_FORMAT,
    DOCTOR_VERSION,
    FINDING_CODES,
    CliAction,
    DoctorError,
    DoctorFinding,
    DoctorReport,
    McpAction,
    ReportDoctorFindings,
    render_doctor_json,
)
from people_context.ports.curation import (
    AFFILIATION_REFERENCE,
    INTERACTION_REFERENCE,
    RELATIONSHIP_REFERENCE,
    CurationReader,
    DeletedPersonReference,
    FactAssertion,
    NameUsage,
    PersonRef,
)
from tests.app.fakes import FakeClock, FakeCurationReader

ALICE = PersonRef(person_id="01A", name="Alice Zhang")
ALEX = PersonRef(person_id="01B", name="Alex Zhang")
SELF = PersonRef(person_id="01Z", name="Me", is_self=True)


def _handle(person: PersonRef, value: str = "azhang") -> NameUsage:
    return NameUsage(person=person, value=value, normalized=value, source="alias:handle")


def _canonical(person: PersonRef, normalized: str = "alice zhang") -> NameUsage:
    return NameUsage(person=person, value=person.name, normalized=normalized, source="canonical_name")


def _fact(
    fact_id: str,
    value: str,
    *,
    person: PersonRef = ALICE,
    predicate: str = "city",
    valid_from: date | None = None,
    valid_to: date | None = None,
    sensitivity: str = "personal",
) -> FactAssertion:
    return FactAssertion(
        person=person,
        fact_id=fact_id,
        predicate=predicate,
        value=value,
        sensitivity=sensitivity,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def _report(**evidence: object) -> DoctorReport:
    reader = FakeCurationReader(**evidence)  # type: ignore[arg-type]
    return ReportDoctorFindings(reader, FakeClock()).execute()


def _codes(report: DoctorReport) -> list[str]:
    return [finding.code for finding in report.findings]


def test_the_fake_reader_satisfies_the_curation_port() -> None:
    assert isinstance(FakeCurationReader(), CurationReader)


def test_a_clean_store_reports_no_findings_and_still_names_every_code() -> None:
    report = _report()

    assert report.findings == []
    assert report.codes == list(FINDING_CODES)
    assert (report.format, report.version) == (DOCTOR_FORMAT, DOCTOR_VERSION)


def test_two_people_sharing_a_handle_are_reported_once_as_a_handle_collision() -> None:
    report = _report(
        shared_handles=[_handle(ALICE), _handle(ALEX)],
        shared_names=[_canonical(ALICE), _canonical(ALEX)],
    )

    assert _codes(report) == ["duplicate_handle"]
    finding = report.findings[0]
    assert [person.person_id for person in finding.people] == ["01A", "01B"]
    assert finding.normalized_name == "azhang"


def test_a_pair_sharing_only_a_name_is_reported_as_a_duplicate_alias() -> None:
    report = _report(shared_names=[_canonical(ALICE), _canonical(ALEX)])

    assert _codes(report) == ["duplicate_alias"]
    assert report.findings[0].normalized_name == "alice zhang"
    assert [name.source for name in report.findings[0].names] == ["canonical_name", "canonical_name"]


def test_handle_precedence_suppresses_only_the_colliding_pair() -> None:
    """A third person sharing the name but not the handle is still a duplicate-alias finding."""
    carol = PersonRef(person_id="01C", name="Alice  Zhang")
    report = _report(
        shared_handles=[_handle(ALICE), _handle(ALEX)],
        shared_names=[_canonical(ALICE), _canonical(ALEX), _canonical(carol)],
    )

    pairs = [tuple(person.person_id for person in finding.people) for finding in report.findings]
    assert _codes(report) == ["duplicate_handle", "duplicate_alias", "duplicate_alias"]
    assert pairs == [("01A", "01B"), ("01A", "01C"), ("01B", "01C")]


def test_filtering_to_duplicate_alias_still_applies_handle_precedence() -> None:
    report = ReportDoctorFindings(
        FakeCurationReader(
            shared_handles=[_handle(ALICE), _handle(ALEX)],
            shared_names=[_canonical(ALICE), _canonical(ALEX)],
        ),
        FakeClock(),
    ).execute(only=["duplicate_alias"])

    assert report.codes == ["duplicate_alias"]
    assert report.findings == []


def test_three_people_sharing_one_handle_produce_every_unordered_pair() -> None:
    carol = PersonRef(person_id="01C", name="A. Zhang")
    report = _report(shared_handles=[_handle(ALICE), _handle(ALEX), _handle(carol)])

    pairs = [tuple(person.person_id for person in finding.people) for finding in report.findings]
    assert pairs == [("01A", "01B"), ("01A", "01C"), ("01B", "01C")]


def test_a_suggested_merge_makes_the_self_person_the_primary_target() -> None:
    report = _report(shared_handles=[_handle(ALICE), _handle(SELF)])

    merge = _only_mcp_action(report.findings[0])
    assert merge.tool == "merge_people"
    assert merge.arguments == {"primary_id": "01Z", "duplicate_id": "01A"}


def test_a_suggested_merge_otherwise_keeps_the_earlier_id_as_primary() -> None:
    report = _report(shared_handles=[_handle(ALEX), _handle(ALICE)])

    assert _only_mcp_action(report.findings[0]).arguments == {"primary_id": "01A", "duplicate_id": "01B"}


def test_every_executable_action_field_carries_ids_and_never_names() -> None:
    report = _report(
        shared_handles=[_handle(ALICE), _handle(ALEX)],
        conflicting_facts=[_fact("01F1", "Berlin"), _fact("01F2", "Paris")],
        deleted_references=[
            DeletedPersonReference(person=ALICE, entity_type=RELATIONSHIP_REFERENCE, entity_id="01R"),
        ],
    )

    names = {"Alice Zhang", "Alex Zhang", "Me"}
    for finding in report.findings:
        for action in finding.actions:
            executable = action.argv if isinstance(action, CliAction) else list(action.arguments.values())
            assert not names.intersection(executable)
    assert report.findings, "the fixture is expected to produce findings"


def test_a_cli_action_is_an_argument_vector_rather_than_a_shell_string() -> None:
    report = _report(shared_handles=[_handle(ALICE), _handle(ALEX)])

    cli_actions = [action for action in report.findings[0].actions if isinstance(action, CliAction)]
    assert [action.argv for action in cli_actions] == [["pctx", "show", "01A"], ["pctx", "show", "01B"]]
    assert all(action.surface == "cli" for action in cli_actions)


def test_facts_with_the_same_value_are_not_a_contradiction() -> None:
    report = _report(conflicting_facts=[_fact("01F1", "Berlin"), _fact("01F2", "Berlin")])

    assert report.findings == []


def test_facts_for_different_people_or_predicates_are_not_paired() -> None:
    report = _report(
        conflicting_facts=[
            _fact("01F1", "Berlin"),
            _fact("01F2", "Paris", person=ALEX),
            _fact("01F3", "Engineer", predicate="role"),
        ]
    )

    assert report.findings == []


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        # Inclusive endpoints: periods that merely touch on one day do overlap.
        ((date(2020, 1, 1), date(2024, 1, 1)), (date(2024, 1, 1), None), True),
        ((date(2020, 1, 1), date(2023, 12, 31)), (date(2024, 1, 1), None), False),
        # Either bound missing is unbounded on that side.
        ((None, date(2019, 1, 1)), (date(2020, 1, 1), None), False),
        ((None, None), (date(2020, 1, 1), date(2020, 1, 2)), True),
        ((None, date(2020, 6, 1)), (None, None), True),
        ((date(2020, 1, 1), None), (None, None), True),
    ],
)
def test_contradiction_uses_validity_period_overlap_semantics(
    first: tuple[date | None, date | None],
    second: tuple[date | None, date | None],
    expected: bool,
) -> None:
    report = _report(
        conflicting_facts=[
            _fact("01F1", "Berlin", valid_from=first[0], valid_to=first[1]),
            _fact("01F2", "Paris", valid_from=second[0], valid_to=second[1]),
        ]
    )

    assert bool(report.findings) is expected


def test_a_contradiction_reports_both_facts_and_suggests_correcting_the_later_one() -> None:
    report = _report(
        conflicting_facts=[
            _fact("01F1", "Berlin", sensitivity="public"),
            _fact("01F2", "Paris", sensitivity="sensitive"),
        ]
    )

    finding = report.findings[0]
    assert finding.predicate == "city"
    assert [(fact.fact_id, fact.value, fact.sensitivity) for fact in finding.facts] == [
        ("01F1", "Berlin", "public"),
        ("01F2", "Paris", "sensitive"),
    ]
    assert _only_mcp_action(finding).arguments == {"entity_type": "fact", "entity_id": "01F2"}


def test_a_correction_suggestion_declares_the_payload_the_operator_must_supply() -> None:
    """`correct_record` rejects empty `fields`, and choosing the surviving value is not ours."""
    report = _report(conflicting_facts=[_fact("01F1", "Berlin"), _fact("01F2", "Paris")])

    assert _only_mcp_action(report.findings[0]).requires == ["fields"]


def test_a_complete_suggestion_declares_nothing_for_the_operator_to_supply() -> None:
    report = _report(
        shared_handles=[_handle(ALICE), _handle(ALEX)],
        deleted_references=[
            DeletedPersonReference(person=ALICE, entity_type=RELATIONSHIP_REFERENCE, entity_id="01R"),
        ],
    )

    merge, forget = (_only_mcp_action(finding) for finding in report.findings)
    assert (merge.tool, merge.requires) == ("merge_people", [])
    assert (forget.tool, forget.requires) == ("forget", [])


def test_dangling_references_group_under_one_finding_per_soft_deleted_person() -> None:
    report = _report(
        deleted_references=[
            DeletedPersonReference(person=ALEX, entity_type=INTERACTION_REFERENCE, entity_id="01I"),
            DeletedPersonReference(person=ALICE, entity_type=RELATIONSHIP_REFERENCE, entity_id="01R"),
            DeletedPersonReference(person=ALICE, entity_type=AFFILIATION_REFERENCE, entity_id="01F"),
        ]
    )

    assert [finding.people[0].person_id for finding in report.findings] == ["01A", "01B"]
    assert [(row.entity_type, row.entity_id) for row in report.findings[0].references] == [
        ("affiliation", "01F"),
        ("relationship", "01R"),
    ]
    assert _only_mcp_action(report.findings[0]).arguments == {"target": "01A", "scope": "person"}


def test_findings_are_ordered_by_declared_code_then_stable_evidence_keys() -> None:
    report = _report(
        shared_handles=[_handle(ALICE), _handle(ALEX)],
        shared_names=[
            _canonical(ALEX, "zzz"),
            _canonical(SELF, "zzz"),
            _canonical(ALICE, "aaa"),
            _canonical(SELF, "aaa"),
        ],
        conflicting_facts=[_fact("01F1", "Berlin"), _fact("01F2", "Paris")],
        deleted_references=[
            DeletedPersonReference(person=ALICE, entity_type=RELATIONSHIP_REFERENCE, entity_id="01R"),
        ],
    )

    assert _codes(report) == [
        "duplicate_handle",
        "duplicate_alias",
        "duplicate_alias",
        "contradictory_fact",
        "dangling_reference",
    ]
    assert [finding.normalized_name for finding in report.findings[1:3]] == ["aaa", "zzz"]


def test_the_report_is_byte_identical_for_the_same_evidence() -> None:
    evidence = {
        "shared_handles": [_handle(ALICE), _handle(ALEX)],
        "conflicting_facts": [_fact("01F1", "Berlin"), _fact("01F2", "Paris")],
    }
    first = render_doctor_json(_report(**evidence))
    second = render_doctor_json(_report(**evidence))

    assert first == second
    assert first.endswith("\n")


def test_only_filters_to_the_requested_codes_in_declared_order() -> None:
    reader = FakeCurationReader(
        conflicting_facts=[_fact("01F1", "Berlin"), _fact("01F2", "Paris")],
        deleted_references=[
            DeletedPersonReference(person=ALICE, entity_type=RELATIONSHIP_REFERENCE, entity_id="01R"),
        ],
    )

    report = ReportDoctorFindings(reader, FakeClock()).execute(only=["dangling_reference", "contradictory_fact"])

    assert report.codes == ["contradictory_fact", "dangling_reference"]
    assert _codes(report) == ["contradictory_fact", "dangling_reference"]


def test_only_does_not_read_evidence_for_codes_it_excluded() -> None:
    reader = FakeCurationReader()

    ReportDoctorFindings(reader, FakeClock()).execute(only=["dangling_reference"])

    assert reader.calls == ["list_deleted_person_references"]


@pytest.mark.parametrize("only", [["nope"], ["contradictory_fact", "nope"], []])
def test_an_unknown_or_empty_only_selection_is_refused(only: list[str]) -> None:
    with pytest.raises(DoctorError):
        ReportDoctorFindings(FakeCurationReader(), FakeClock()).execute(only=only)


def test_the_json_document_keeps_its_declared_versioned_shape() -> None:
    report = _report(
        shared_handles=[_handle(ALICE), _handle(ALEX)],
        conflicting_facts=[
            _fact("01F1", "Berlin", valid_from=date(2020, 1, 1)),
            _fact("01F2", "Paris"),
        ],
        deleted_references=[
            DeletedPersonReference(person=SELF, entity_type=INTERACTION_REFERENCE, entity_id="01I"),
        ],
    )

    document = json.loads(render_doctor_json(report))

    assert document["format"] == "people-context-doctor"
    assert document["version"] == 1
    assert set(document) == {"format", "version", "generated_at", "codes", "findings"}
    assert set(document["findings"][0]) == {
        "code",
        "message",
        "people",
        "normalized_name",
        "predicate",
        "names",
        "facts",
        "references",
        "actions",
    }
    assert set(document["findings"][0]["people"][0]) == {"person_id", "name", "is_self"}
    surfaces = {action["surface"] for finding in document["findings"] for action in finding["actions"]}
    assert surfaces == {"cli", "mcp"}
    for finding in document["findings"]:
        for action in finding["actions"]:
            expected = (
                {"surface", "argv"}
                if action["surface"] == "cli"
                else {"surface", "tool", "arguments", "requires"}
            )
            assert set(action) == expected


def test_the_declared_codes_are_the_ones_the_report_can_emit() -> None:
    assert FINDING_CODES == (
        "duplicate_handle",
        "duplicate_alias",
        "contradictory_fact",
        "dangling_reference",
    )


def _only_mcp_action(finding: DoctorFinding) -> McpAction:
    actions = [action for action in finding.actions if isinstance(action, McpAction)]
    assert len(actions) == 1
    return actions[0]
