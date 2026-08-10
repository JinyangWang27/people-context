"""Application policy for the deterministic vCard export (M14.2)."""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from people_context.app.exports.vcard import ExportVCard, VCardExportError, VCardExportResult
from people_context.domain.fact import Fact
from people_context.domain.organization import Affiliation, Organization
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.shared import Provenance, Sensitivity, ValidityPeriod
from people_context.ports.export import ExportSnapshot
from people_context.ports.vcard import VCARD_3_0, VCARD_4_0, VCardProjection
from tests.app.fakes import FakeClock, FakeExportReader

_NOW = datetime(2026, 3, 4, 5, 6, tzinfo=UTC)
_PROVENANCE = Provenance(source="test")


class _RecordingWriter:
    """A writer port that keeps the projection it was handed and renders nothing."""

    def __init__(self) -> None:
        self.projections: list[VCardProjection] = []

    def write_vcards(self, projection: VCardProjection) -> str:
        self.projections.append(projection)
        return "rendered"


def _snapshot(
    *,
    people: list[Person] | None = None,
    organizations: list[Organization] | None = None,
    affiliations: list[Affiliation] | None = None,
    facts: list[Fact] | None = None,
) -> ExportSnapshot:
    """Build the JSON-shaped snapshot the SQLite export reader produces."""
    return ExportSnapshot(
        people=[person.model_dump(mode="json") for person in people or []],
        organizations=[organization.model_dump(mode="json") for organization in organizations or []],
        affiliations=[affiliation.model_dump(mode="json") for affiliation in affiliations or []],
        relationships=[],
        facts=[fact.model_dump(mode="json") for fact in facts or []],
        observations=[],
        traits=[],
        interactions=[],
        reminders=[],
        user_preferences=[],
        audit_log=[],
    )


def _export(
    snapshot: ExportSnapshot,
    *,
    version: str | None = None,
    include_sensitive: bool = False,
) -> tuple[VCardProjection, VCardExportResult]:
    """Run one export and return the projection the adapter received plus the result."""
    writer = _RecordingWriter()
    use_case = ExportVCard(FakeExportReader(snapshot), writer, FakeClock(_NOW))
    result = (
        use_case.execute(include_sensitive=include_sensitive)
        if version is None
        else use_case.execute(version=version, include_sensitive=include_sensitive)
    )
    assert len(writer.projections) == 1
    return writer.projections[0], result


def _birthday(
    person: Person,
    value: str,
    *,
    confidence: float = 1.0,
    recorded_at: datetime = _NOW,
    sensitivity: Sensitivity = Sensitivity.PERSONAL,
) -> Fact:
    return Fact(
        person_id=person.id,
        predicate="birthday",
        value=value,
        recorded_at=recorded_at,
        confidence=confidence,
        sensitivity=sensitivity,
        provenance=_PROVENANCE,
    )


def test_projects_identity_affiliation_and_birthday() -> None:
    person = Person(
        canonical_name="Alice Zhang",
        aliases=[
            Alias(value="Ali", kind=AliasKind.NICKNAME),
            Alias(value="alice@example.com", kind=AliasKind.HANDLE),
        ],
    )
    organization = Organization(name="Acme")
    affiliation = Affiliation(
        person_id=person.id,
        org_id=organization.id,
        role="Engineer",
        provenance=_PROVENANCE,
    )
    snapshot = _snapshot(
        people=[person],
        organizations=[organization],
        affiliations=[affiliation],
        facts=[_birthday(person, "1985-04-12")],
    )

    projection, result = _export(snapshot)

    assert projection.version == VCARD_3_0
    contact = projection.contacts[0]
    assert contact.person_id == person.id
    assert contact.full_name == "Alice Zhang"
    assert contact.nicknames == ("Ali",)
    assert contact.emails == ("alice@example.com",)
    assert contact.affiliation is not None
    assert (contact.affiliation.organization, contact.affiliation.role) == ("Acme", "Engineer")
    assert contact.birthday == date(1985, 4, 12)
    assert result.document == "rendered"
    assert result.exported == 1


def test_excludes_soft_deleted_people() -> None:
    active = Person(canonical_name="Alice")
    removed = Person(canonical_name="Bob", deleted_at=_NOW)

    projection, result = _export(_snapshot(people=[active, removed]))

    assert [contact.full_name for contact in projection.contacts] == ["Alice"]
    assert result.exported == 1


def test_orders_people_by_normalized_name_then_id() -> None:
    same_name_first = Person(id="01AAA", canonical_name="Ada Lovelace")
    same_name_second = Person(id="01BBB", canonical_name="ada lovelace")
    other = Person(canonical_name="Émile Zola")

    projection, _result = _export(_snapshot(people=[other, same_name_second, same_name_first]))

    assert [contact.person_id for contact in projection.contacts] == [
        same_name_first.id,
        same_name_second.id,
        other.id,
    ]


def test_skips_a_person_whose_canonical_name_is_blank() -> None:
    """A card without a usable `FN` is refused by the importer, so it is never written."""
    projection, result = _export(_snapshot(people=[Person(canonical_name="   ")]))

    assert projection.contacts == ()
    assert result.exported == 0


def test_evaluates_affiliations_as_of_the_clock_date() -> None:
    person = Person(canonical_name="Alice")
    organization = Organization(name="Acme")
    expired = Affiliation(
        person_id=person.id,
        org_id=organization.id,
        role="Intern",
        period=ValidityPeriod(valid_to=date(2025, 1, 1)),
        provenance=_PROVENANCE,
    )
    current = Affiliation(
        person_id=person.id,
        org_id=organization.id,
        role="Engineer",
        period=ValidityPeriod(valid_from=date(2025, 2, 1)),
        provenance=_PROVENANCE,
    )

    projection, result = _export(
        _snapshot(
            people=[person],
            organizations=[organization],
            affiliations=[expired, current],
        )
    )

    contact = projection.contacts[0]
    assert contact.affiliation is not None
    assert contact.affiliation.role == "Engineer"
    # The expired row is not an omission; it is simply not active on the export date.
    assert result.omitted_affiliations == 0


def test_selects_one_affiliation_and_counts_the_rest() -> None:
    person = Person(canonical_name="Alice")
    acme = Organization(name="Acme")
    zenith = Organization(name="Zenith")
    first = Affiliation(person_id=person.id, org_id=zenith.id, role="Advisor", provenance=_PROVENANCE)
    second = Affiliation(person_id=person.id, org_id=acme.id, role="Engineer", provenance=_PROVENANCE)
    third = Affiliation(person_id=person.id, org_id=acme.id, role="Architect", provenance=_PROVENANCE)

    projection, result = _export(
        _snapshot(
            people=[person],
            organizations=[zenith, acme],
            affiliations=[first, second, third],
        )
    )

    contact = projection.contacts[0]
    assert contact.affiliation is not None
    # Normalized organization name first, then normalized role.
    assert (contact.affiliation.organization, contact.affiliation.role) == ("Acme", "Architect")
    assert result.omitted_affiliations == 2


def test_ignores_an_affiliation_the_importer_could_not_read_back() -> None:
    """Both `ORG` and `TITLE` must carry a value, so a blank role cannot be exported."""
    person = Person(canonical_name="Alice")
    organization = Organization(name="Acme")
    blank_role = Affiliation(person_id=person.id, org_id=organization.id, role="   ", provenance=_PROVENANCE)
    unknown_org = Affiliation(person_id=person.id, org_id="01MISSING", role="Engineer", provenance=_PROVENANCE)

    projection, result = _export(
        _snapshot(
            people=[person],
            organizations=[organization],
            affiliations=[blank_role, unknown_org],
        )
    )

    assert projection.contacts[0].affiliation is None
    assert result.omitted_affiliations == 0


def test_selects_the_most_confident_then_newest_full_birthday() -> None:
    person = Person(canonical_name="Alice")
    low_confidence = _birthday(person, "1980-01-01", confidence=0.4)
    older = _birthday(person, "1985-04-12", recorded_at=datetime(2020, 1, 1, tzinfo=UTC))
    newest = _birthday(person, "1985-04-13", recorded_at=datetime(2024, 1, 1, tzinfo=UTC))

    projection, result = _export(
        _snapshot(people=[person], facts=[low_confidence, newest, older])
    )

    assert projection.contacts[0].birthday == date(1985, 4, 13)
    assert result.omitted_birthdays == 2
    assert result.skipped_partial_birthdays == 0
    assert result.skipped_unparseable_birthdays == 0


def test_counts_partial_and_unparseable_birthdays_separately() -> None:
    person = Person(canonical_name="Alice")
    facts = [
        _birthday(person, "--04-12"),
        _birthday(person, "--02-29"),
        _birthday(person, "--02-30"),
        _birthday(person, "1985-02-30"),
        _birthday(person, "born in April"),
    ]

    projection, result = _export(_snapshot(people=[person], facts=facts))

    assert projection.contacts[0].birthday is None
    assert result.omitted_birthdays == 0
    assert result.skipped_partial_birthdays == 2
    # An impossible day is not a partial birthday, it is simply unusable.
    assert result.skipped_unparseable_birthdays == 3


def test_a_birthday_outside_its_validity_period_is_neither_exported_nor_counted() -> None:
    """A closed row must not come back, and it is no more an omission than an old job is."""
    person = Person(canonical_name="Alice")
    corrected = Fact(
        person_id=person.id,
        predicate="birthday",
        value="1985-04-12",
        period=ValidityPeriod(valid_to=date(2025, 1, 1)),
        recorded_at=_NOW,
        provenance=_PROVENANCE,
    )
    not_yet_active = Fact(
        person_id=person.id,
        predicate="birthday",
        value="1985-04-13",
        period=ValidityPeriod(valid_from=date(2027, 1, 1)),
        recorded_at=_NOW,
        provenance=_PROVENANCE,
    )
    unusable_but_closed = Fact(
        person_id=person.id,
        predicate="birthday",
        value="sometime in April",
        period=ValidityPeriod(valid_to=date(2025, 1, 1)),
        recorded_at=_NOW,
        provenance=_PROVENANCE,
    )

    projection, result = _export(
        _snapshot(people=[person], facts=[corrected, not_yet_active, unusable_but_closed])
    )

    assert projection.contacts[0].birthday is None
    assert result.omitted_birthdays == 0
    assert result.skipped_unparseable_birthdays == 0


def test_a_closed_birthday_never_outranks_its_active_replacement() -> None:
    """Confidence orders the selection, so an expired row must be gone before it is ranked."""
    person = Person(canonical_name="Alice")
    closed = Fact(
        person_id=person.id,
        predicate="birthday",
        value="1985-04-12",
        period=ValidityPeriod(valid_to=date(2025, 1, 1)),
        recorded_at=_NOW,
        confidence=1.0,
        provenance=_PROVENANCE,
    )
    replacement = _birthday(person, "1986-05-13", confidence=0.6)

    projection, result = _export(_snapshot(people=[person], facts=[closed, replacement]))

    assert projection.contacts[0].birthday == date(1986, 5, 13)
    assert result.omitted_birthdays == 0


def test_elevated_birthdays_are_invisible_by_default() -> None:
    person = Person(canonical_name="Alice")
    sensitive = _birthday(person, "1985-04-12", sensitivity=Sensitivity.SENSITIVE)
    restricted = _birthday(person, "not a date", sensitivity=Sensitivity.RESTRICTED)

    projection, result = _export(_snapshot(people=[person], facts=[sensitive, restricted]))

    assert projection.contacts[0].birthday is None
    assert result.omitted_birthdays == 0
    assert result.skipped_unparseable_birthdays == 0


def test_include_sensitive_widens_birthday_selection() -> None:
    person = Person(canonical_name="Alice")
    sensitive = _birthday(person, "1985-04-12", sensitivity=Sensitivity.SENSITIVE)
    restricted = _birthday(person, "not a date", sensitivity=Sensitivity.RESTRICTED)

    projection, result = _export(
        _snapshot(people=[person], facts=[sensitive, restricted]),
        include_sensitive=True,
    )

    assert projection.contacts[0].birthday == date(1985, 4, 12)
    assert result.skipped_unparseable_birthdays == 1


def test_other_predicates_never_reach_the_projection() -> None:
    person = Person(canonical_name="Alice")
    other = Fact(
        person_id=person.id,
        predicate="dietary",
        value="vegetarian",
        recorded_at=_NOW,
        provenance=_PROVENANCE,
    )

    projection, result = _export(_snapshot(people=[person], facts=[other]))

    assert projection.contacts[0].birthday is None
    assert result.skipped_unparseable_birthdays == 0


def test_emails_come_only_from_parseable_handle_aliases() -> None:
    person = Person(
        canonical_name="Alice",
        aliases=[
            Alias(value="@alice", kind=AliasKind.HANDLE),
            Alias(value="alice@example.com", kind=AliasKind.HANDLE),
            Alias(value="Alice@Example.com", kind=AliasKind.HANDLE),
            Alias(value="zoe@example.com", kind=AliasKind.OTHER),
        ],
    )

    projection, _result = _export(_snapshot(people=[person]))

    assert projection.contacts[0].emails == ("alice@example.com",)


def test_nicknames_drop_duplicates_and_a_repeat_of_the_canonical_name() -> None:
    person = Person(
        canonical_name="Alice Zhang",
        aliases=[
            Alias(value="Ali", kind=AliasKind.NICKNAME),
            Alias(value="ali", kind=AliasKind.NICKNAME),
            Alias(value="alice zhang", kind=AliasKind.NICKNAME),
            Alias(value="Zhang Wei", kind=AliasKind.NATIVE_SCRIPT),
        ],
    )

    projection, _result = _export(_snapshot(people=[person]))

    assert projection.contacts[0].nicknames == ("Ali",)


def test_repeated_exports_of_one_snapshot_are_identical() -> None:
    people = [Person(canonical_name=name) for name in ("Bob", "Alice", "Carol")]
    snapshot = _snapshot(people=people)

    first, _ = _export(snapshot)
    second, _ = _export(snapshot)

    assert first == second


def test_selects_the_requested_dialect() -> None:
    projection, result = _export(_snapshot(people=[Person(canonical_name="Alice")]), version=VCARD_4_0)

    assert projection.version == VCARD_4_0
    assert result.version == VCARD_4_0


def test_refuses_an_unsupported_dialect_before_reading() -> None:
    reader = FakeExportReader(_snapshot(people=[Person(canonical_name="Alice")]))
    use_case = ExportVCard(reader, _RecordingWriter(), FakeClock(_NOW))

    with pytest.raises(VCardExportError):
        use_case.execute(version="2.1")

    assert reader.calls == 0


def test_the_use_case_module_imports_no_adapter() -> None:
    """The projection is handed to a port; the app never reaches the serializer directly."""
    source = Path(ExportVCard.__module__.replace(".", "/") + ".py")
    root = Path(__file__).parents[3] / "src"
    tree = ast.parse((root / source).read_text(encoding="utf-8"))
    imported = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(module.startswith("people_context.adapters") for module in imported)
