"""Deterministic, report-only data-quality findings over stored curation evidence.

The doctor never repairs anything. It reads candidate evidence, applies fixed policy to decide
which of it is a finding, and returns a stably ordered document whose suggested actions are
*structured*: an argv list, or an MCP tool name with an argument mapping. Actions address people
and records by stable id and never by name, so nothing in the report can be interpolated into a
shell and nothing depends on a display name that two people might share — which is precisely
what several of the findings are about.

Findings exist alongside each other, so precedence matters: two people who share a handle are
reported once, as a handle collision, and the same pair is not reported again for whatever other
name material they happen to share.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from people_context.app.exports._document import render_json_document
from people_context.domain.shared import ValidityPeriod
from people_context.ports.clock import Clock
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

DOCTOR_FORMAT = "people-context-doctor"
DOCTOR_VERSION = 1


class FindingCode(StrEnum):
    """Stable finding identifiers; the declaration order is the report order."""

    DUPLICATE_HANDLE = "duplicate_handle"
    DUPLICATE_ALIAS = "duplicate_alias"
    CONTRADICTORY_FACT = "contradictory_fact"
    DANGLING_REFERENCE = "dangling_reference"


#: Every known code in report order, for `--only` validation and deterministic ordering.
FINDING_CODES: tuple[str, ...] = tuple(code.value for code in FindingCode)

_REFERENCE_LABELS = {
    RELATIONSHIP_REFERENCE: "relationship",
    AFFILIATION_REFERENCE: "affiliation",
    INTERACTION_REFERENCE: "interaction",
}


class DoctorError(ValueError):
    """Raised when a requested finding code is not one this release reports."""


class FindingPerson(BaseModel):
    """One person implicated by a finding, addressed by stable id."""

    person_id: str
    name: str
    is_self: bool = False


class NameEvidence(BaseModel):
    """One stored name value that took part in a collision."""

    person_id: str
    value: str
    source: str


class FactEvidence(BaseModel):
    """One stored fact that took part in a contradiction."""

    fact_id: str
    predicate: str
    value: str
    sensitivity: str
    valid_from: date | None = None
    valid_to: date | None = None


class ReferenceEvidence(BaseModel):
    """One stored row that still points at a soft-deleted person."""

    entity_type: str
    entity_id: str


class CliAction(BaseModel):
    """A suggested command as an argument vector, never as a shell string."""

    surface: Literal["cli"] = "cli"
    argv: list[str]


class McpAction(BaseModel):
    """A suggested MCP call as a tool name and an id-only argument mapping."""

    surface: Literal["mcp"] = "mcp"
    tool: str
    arguments: dict[str, str]


SuggestedAction = CliAction | McpAction


class DoctorFinding(BaseModel):
    """One reported data-quality problem and the evidence behind it.

    Evidence collections are per finding class: a finding populates the ones its code implies
    and leaves the rest empty, which keeps one flat shape for every code and lets a later
    release add an evidence kind additively.
    """

    code: str
    message: str
    people: list[FindingPerson] = Field(default_factory=list)
    normalized_name: str | None = None
    predicate: str | None = None
    names: list[NameEvidence] = Field(default_factory=list)
    facts: list[FactEvidence] = Field(default_factory=list)
    references: list[ReferenceEvidence] = Field(default_factory=list)
    actions: list[SuggestedAction] = Field(default_factory=list)


class DoctorReport(BaseModel):
    """The versioned doctor document; a declared machine interface under the M12 promise."""

    format: str = DOCTOR_FORMAT
    version: int = DOCTOR_VERSION
    generated_at: datetime
    codes: list[str] = Field(default_factory=list)
    findings: list[DoctorFinding] = Field(default_factory=list)


class ReportDoctorFindings:
    """Turn stored curation evidence into one deterministic report-only document."""

    def __init__(self, curation: CurationReader, clock: Clock) -> None:
        self._curation = curation
        self._clock = clock

    def execute(self, *, only: Sequence[str] | None = None) -> DoctorReport:
        """Return the findings for every requested code, in fixed code and evidence order."""
        codes = _requested_codes(only)
        findings: list[DoctorFinding] = []
        handle_pairs: set[tuple[str, str]] = set()

        # Handle collisions are computed whenever alias collisions are, even if the caller
        # filtered them out of the output, because they are what suppresses a duplicate pair
        # from being reported twice.
        if FindingCode.DUPLICATE_HANDLE in codes or FindingCode.DUPLICATE_ALIAS in codes:
            handle_findings = _name_findings(
                self._curation.list_shared_handles(),
                code=FindingCode.DUPLICATE_HANDLE,
                excluded_pairs=frozenset(),
            )
            handle_pairs = {_pair_key(finding) for finding in handle_findings}
            if FindingCode.DUPLICATE_HANDLE in codes:
                findings.extend(handle_findings)
        if FindingCode.DUPLICATE_ALIAS in codes:
            findings.extend(
                _name_findings(
                    self._curation.list_shared_names(),
                    code=FindingCode.DUPLICATE_ALIAS,
                    excluded_pairs=frozenset(handle_pairs),
                )
            )
        if FindingCode.CONTRADICTORY_FACT in codes:
            findings.extend(_fact_findings(self._curation.list_conflicting_facts()))
        if FindingCode.DANGLING_REFERENCE in codes:
            findings.extend(_reference_findings(self._curation.list_deleted_person_references()))

        findings.sort(key=_finding_sort_key)
        return DoctorReport(
            generated_at=self._clock.now(),
            codes=[code.value for code in codes],
            findings=findings,
        )


def render_doctor_json(report: DoctorReport) -> str:
    """Render the versioned machine document as canonical JSON text."""
    return render_json_document(report)


def _requested_codes(only: Sequence[str] | None) -> tuple[FindingCode, ...]:
    """Validate `--only` against the known codes, preserving the declared report order."""
    if only is None:
        return tuple(FindingCode)
    requested = {value.strip() for value in only}
    unknown = sorted(value for value in requested if value not in FINDING_CODES)
    if unknown:
        raise DoctorError(f"unknown finding code(s): {', '.join(unknown)}; known codes: {', '.join(FINDING_CODES)}")
    if not requested:
        raise DoctorError("at least one finding code is required")
    return tuple(code for code in FindingCode if code.value in requested)


def _name_findings(
    usages: Iterable[NameUsage],
    *,
    code: FindingCode,
    excluded_pairs: frozenset[tuple[str, str]],
) -> list[DoctorFinding]:
    """Report one finding per unordered pair of active people sharing a normalized value."""
    grouped: dict[str, list[NameUsage]] = {}
    for usage in usages:
        grouped.setdefault(usage.normalized, []).append(usage)

    findings: list[DoctorFinding] = []
    for normalized in sorted(grouped):
        by_person: dict[str, list[NameUsage]] = {}
        for usage in grouped[normalized]:
            by_person.setdefault(usage.person.person_id, []).append(usage)
        person_ids = sorted(by_person)
        for index, first_id in enumerate(person_ids):
            for second_id in person_ids[index + 1 :]:
                if (first_id, second_id) in excluded_pairs:
                    continue
                findings.append(
                    _name_finding(
                        code,
                        normalized,
                        by_person[first_id],
                        by_person[second_id],
                    )
                )
    return findings


def _name_finding(
    code: FindingCode,
    normalized: str,
    first: list[NameUsage],
    second: list[NameUsage],
) -> DoctorFinding:
    people = [_finding_person(usages[0].person) for usages in (first, second)]
    names = [
        NameEvidence(person_id=usage.person.person_id, value=usage.value, source=usage.source)
        for usage in sorted(first + second, key=lambda usage: (usage.person.person_id, usage.source, usage.value))
    ]
    subject = "handle" if code is FindingCode.DUPLICATE_HANDLE else "name"
    return DoctorFinding(
        code=code.value,
        message=f"Two active people share the normalized {subject} {normalized!r}.",
        people=people,
        normalized_name=normalized,
        names=names,
        actions=[
            *(CliAction(argv=["pctx", "show", person.person_id]) for person in people),
            _merge_action(first[0].person, second[0].person),
        ],
    )


def _merge_action(first: PersonRef, second: PersonRef) -> McpAction:
    """Suggest a merge whose direction the merge use case will accept.

    The self person must be the primary target; otherwise the lower id wins, which for ULIDs is
    the person recorded first. Either way the direction is a fixed function of the pair, so the
    same store always yields the same suggestion.
    """
    primary, duplicate = (first, second) if _merge_rank(first) <= _merge_rank(second) else (second, first)
    return McpAction(
        tool="merge_people",
        arguments={"primary_id": primary.person_id, "duplicate_id": duplicate.person_id},
    )


def _merge_rank(person: PersonRef) -> tuple[int, str]:
    return (0 if person.is_self else 1, person.person_id)


def _fact_findings(assertions: Iterable[FactAssertion]) -> list[DoctorFinding]:
    """Report one finding per pair of same-predicate facts whose periods overlap."""
    grouped: dict[tuple[str, str], list[FactAssertion]] = {}
    for assertion in assertions:
        grouped.setdefault((assertion.person.person_id, assertion.predicate), []).append(assertion)

    findings: list[DoctorFinding] = []
    for person_id, predicate in sorted(grouped):
        facts = sorted(grouped[(person_id, predicate)], key=lambda fact: fact.fact_id)
        for index, first in enumerate(facts):
            for second in facts[index + 1 :]:
                if first.value == second.value or not _period(first).overlaps(_period(second)):
                    continue
                findings.append(_fact_finding(predicate, first, second))
    return findings


def _fact_finding(predicate: str, first: FactAssertion, second: FactAssertion) -> DoctorFinding:
    return DoctorFinding(
        code=FindingCode.CONTRADICTORY_FACT.value,
        message=(
            f"Two facts with predicate {predicate!r} assert different values over overlapping periods."
        ),
        people=[_finding_person(first.person)],
        predicate=predicate,
        facts=[_fact_evidence(first), _fact_evidence(second)],
        actions=[
            CliAction(argv=["pctx", "show", first.person.person_id]),
            McpAction(
                tool="correct_record",
                arguments={"entity_type": "fact", "entity_id": second.fact_id},
            ),
        ],
    )


def _fact_evidence(assertion: FactAssertion) -> FactEvidence:
    return FactEvidence(
        fact_id=assertion.fact_id,
        predicate=assertion.predicate,
        value=assertion.value,
        sensitivity=assertion.sensitivity,
        valid_from=assertion.valid_from,
        valid_to=assertion.valid_to,
    )


def _period(assertion: FactAssertion) -> ValidityPeriod:
    return ValidityPeriod(valid_from=assertion.valid_from, valid_to=assertion.valid_to)


def _reference_findings(references: Iterable[DeletedPersonReference]) -> list[DoctorFinding]:
    """Report one finding per soft-deleted person that stored rows still point at.

    Grouping by person rather than by row keeps the report proportional to the problem and
    matches its repair: forgetting the person removes every one of those rows at once.
    """
    grouped: dict[str, tuple[PersonRef, list[DeletedPersonReference]]] = {}
    for reference in references:
        _, rows = grouped.setdefault(reference.person.person_id, (reference.person, []))
        rows.append(reference)

    findings: list[DoctorFinding] = []
    for person_id in sorted(grouped):
        person, rows = grouped[person_id]
        evidence = [
            ReferenceEvidence(entity_type=row.entity_type, entity_id=row.entity_id)
            for row in sorted(rows, key=lambda row: (row.entity_type, row.entity_id))
        ]
        findings.append(
            DoctorFinding(
                code=FindingCode.DANGLING_REFERENCE.value,
                message=_reference_message(evidence),
                people=[_finding_person(person)],
                references=evidence,
                # `pctx show` and `pctx delete` resolve active people only, so the repair for a
                # soft-deleted person is the operator-gated forget tool rather than a command.
                actions=[
                    McpAction(tool="forget", arguments={"target": person_id, "scope": "person"}),
                ],
            )
        )
    return findings


def _reference_message(evidence: list[ReferenceEvidence]) -> str:
    kinds = sorted({_REFERENCE_LABELS.get(row.entity_type, row.entity_type) for row in evidence})
    rows = "1 stored row" if len(evidence) == 1 else f"{len(evidence)} stored rows"
    return f"A soft-deleted person is still referenced by {rows} in: {', '.join(kinds)}."


def _finding_person(person: PersonRef) -> FindingPerson:
    return FindingPerson(person_id=person.person_id, name=person.name, is_self=person.is_self)


def _pair_key(finding: DoctorFinding) -> tuple[str, str]:
    first, second = sorted(person.person_id for person in finding.people)
    return (first, second)


def _finding_sort_key(finding: DoctorFinding) -> tuple[int, str, str, tuple[str, ...]]:
    """Order by declared code, then by the finding's own stable evidence keys.

    Names sort by their normalized value rather than a display name, and the trailing person
    ids make every key total, so two findings can never swap places between runs.
    """
    return (
        FINDING_CODES.index(finding.code),
        finding.normalized_name or "",
        finding.predicate or "",
        tuple(person.person_id for person in finding.people)
        + tuple(fact.fact_id for fact in finding.facts),
    )
