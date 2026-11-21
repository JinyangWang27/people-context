"""SQLite education records: storage, correction, lifecycle, and portability."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.adapters.sqlite.db import open_db
from people_context.app.exports.sync_bundle import render_bundle_json
from people_context.app.people import RememberPersonInput
from people_context.app.records import (
    CorrectRecordInput,
    InvalidCorrectionError,
    RecordEducationInput,
    SetAffiliationInput,
)
from people_context.domain.education import Education
from people_context.domain.organization import Organization
from people_context.domain.sync_bundle import InvalidBundleError

_NOW = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[ApplicationRuntime]:
    built = build_runtime(tmp_path / "origin.db", clock=_Clock())
    yield built
    built.close()


def _person(runtime: ApplicationRuntime, name: str) -> str:
    return runtime.use_cases.remember_person.execute(RememberPersonInput(name=name)).person.id


def _study(runtime: ApplicationRuntime, person_id: str, **fields: Any) -> Education:
    fields.setdefault("institution", "University of Edinburgh")
    return runtime.use_cases.record_education.execute(RecordEducationInput(person_id=person_id, **fields))


def _count(runtime: ApplicationRuntime, table: str) -> int:
    return int(runtime.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608 - fixed constants


def test_a_fresh_database_has_the_education_table_and_industry_column() -> None:
    conn = open_db(":memory:")

    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(organizations)")}

    assert "education" in tables
    assert "industry" in columns


def test_education_round_trips_and_shares_the_institution_with_affiliations(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime, "Alice")
    studied = _study(
        runtime,
        alice,
        degree="MSc",
        field_of_study="Cognitive Science",
        valid_from=date(2022, 9, 1),
        valid_to=date(2024, 6, 30),
    )
    worked = runtime.use_cases.set_affiliation.execute(
        SetAffiliationInput(person_id=alice, org="university of edinburgh", role="Research Assistant")
    )

    stored = runtime.records.get_record("education", studied.id)

    assert stored == studied
    assert worked.org_id == studied.org_id
    assert _count(runtime, "organizations") == 1
    assert _count(runtime, "affiliations") == 1


def test_an_exact_repeat_writes_nothing_and_a_second_degree_is_a_second_row(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime, "Alice")
    first = _study(runtime, alice, degree="BA", field_of_study="Cognitive Science")
    audits = _count(runtime, "audit_log")

    repeat = _study(
        runtime, alice, institution="UNIVERSITY OF EDINBURGH", degree="BA", field_of_study="Cognitive Science"
    )
    second = _study(runtime, alice, degree="MSc", field_of_study="Cognitive Science")

    assert repeat == first
    assert second.id != first.id
    assert _count(runtime, "audit_log") == audits + 1


def test_an_unknown_institution_id_is_refused_rather_than_created(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime, "Alice")

    with pytest.raises(Exception, match="organization not found"):
        _study(runtime, alice, institution="01J000000000000000000ZZZZ1")

    assert _count(runtime, "organizations") == 0


def test_correction_changes_education_fields_but_not_the_institution(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime, "Alice")
    studied = _study(runtime, alice, degree="MSc", field_of_study="Cognitive Sciense")

    corrected = runtime.use_cases.correct_record.execute(
        CorrectRecordInput(
            entity_type="education",
            entity_id=studied.id,
            fields={"field_of_study": "Cognitive Science", "valid_to": "2024-06-30"},
        )
    )

    assert isinstance(corrected, Education)
    assert (corrected.field_of_study, corrected.period.valid_to) == ("Cognitive Science", date(2024, 6, 30))
    with pytest.raises(InvalidCorrectionError):
        runtime.use_cases.correct_record.execute(
            CorrectRecordInput(entity_type="education", entity_id=studied.id, fields={"org_id": "elsewhere"})
        )


def test_correction_sets_an_organizations_industry_but_never_its_name(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime, "Alice")
    org_id = _study(runtime, alice).org_id

    corrected = runtime.use_cases.correct_record.execute(
        CorrectRecordInput(entity_type="organization", entity_id=org_id, fields={"industry": "higher education"})
    )

    assert isinstance(corrected, Organization)
    assert corrected.industry == "higher education"
    with pytest.raises(InvalidCorrectionError):
        runtime.use_cases.correct_record.execute(
            CorrectRecordInput(entity_type="organization", entity_id=org_id, fields={"name": "Edinburgh"})
        )


def test_forgetting_a_person_erases_their_education_and_its_history(runtime: ApplicationRuntime) -> None:
    alice, bob = _person(runtime, "Alice"), _person(runtime, "Bob")
    erased = _study(runtime, alice, degree="MSc")
    kept = _study(runtime, bob, degree="PhD")

    preview = runtime.use_cases.preview_forget.execute(alice)
    result = runtime.use_cases.forget.execute(alice, "person")

    assert preview.deleted["education"] == result.deleted["education"] == 1
    assert runtime.records.get_record("education", kept.id) == kept
    history = runtime.conn.execute("SELECT payload_json FROM changelog WHERE entity_id = ?", (erased.id,)).fetchall()
    assert history and all(json.loads(row["payload_json"]) == {"redacted": True} for row in history)


def test_a_person_with_no_education_forgets_with_an_unchanged_summary(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime, "Alice")

    assert "education" not in runtime.use_cases.forget.execute(alice, "person").deleted


def test_forgetting_one_education_row_leaves_the_person(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime, "Alice")
    studied = _study(runtime, alice)

    result = runtime.use_cases.forget.execute(f"education:{studied.id}", "record")

    assert result.deleted == {"education": 1}
    assert _count(runtime, "persons") == 1


def test_merge_moves_education_to_the_survivor(runtime: ApplicationRuntime) -> None:
    alice, duplicate = _person(runtime, "Alice"), _person(runtime, "Alice Duplicate")
    moved = _study(runtime, duplicate, degree="MSc")

    result = runtime.use_cases.merge_people.execute(alice, duplicate)

    assert result.moved.education == 1
    stored = runtime.records.get_record("education", moved.id)
    assert isinstance(stored, Education) and stored.person_id == alice


class TestPortability:
    def _seed(self, runtime: ApplicationRuntime) -> Education:
        alice = _person(runtime, "Alice")
        studied = _study(runtime, alice, degree="MSc", field_of_study="Cognitive Science", valid_from=date(2022, 9, 1))
        runtime.use_cases.correct_record.execute(
            CorrectRecordInput(entity_type="organization", entity_id=studied.org_id, fields={"industry": "education"})
        )
        return studied

    def test_export_and_restore_round_trip_education_and_industry(
        self, runtime: ApplicationRuntime, tmp_path: Path
    ) -> None:
        studied = self._seed(runtime)
        document = runtime.use_cases.export_sync_bundle.execute()
        assert [row.id for row in document.education] == [studied.id]

        destination = build_runtime(tmp_path / "destination.db", clock=_Clock())
        try:
            parsed = destination.use_cases.restore_sync_bundle.parse(render_bundle_json(document))
            assert destination.use_cases.restore_sync_bundle.preview(parsed).counts["education"] == 1
            outcome = destination.use_cases.restore_sync_bundle.execute(parsed)
            restored = destination.records.get_record("education", studied.id)
            institution = destination.records.get_record("organization", studied.org_id)
        finally:
            destination.close()

        assert outcome.education == 1
        assert restored == studied
        assert isinstance(institution, Organization) and institution.industry == "education"

    def test_the_plain_export_carries_education_and_industry(self, runtime: ApplicationRuntime) -> None:
        studied = self._seed(runtime)

        document = runtime.use_cases.export_data.execute()

        assert [row["id"] for row in document.education] == [studied.id]
        assert [row["industry"] for row in document.organizations] == ["education"]

    def test_education_naming_an_unbundled_institution_is_refused(self, runtime: ApplicationRuntime) -> None:
        self._seed(runtime)
        payload = json.loads(render_bundle_json(runtime.use_cases.export_sync_bundle.execute()))
        payload["education"][0]["org_id"] = "01J000000000000000000MISS1"

        with pytest.raises(InvalidBundleError) as raised:
            runtime.use_cases.restore_sync_bundle.parse(json.dumps(payload))

        assert any("unknown organization" in detail for detail in raised.value.details)


def test_person_context_lists_finished_education_newest_first(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime, "Alice")
    _study(runtime, alice, degree="BA", field_of_study="Cognitive Science", valid_to=date(2021, 6, 30))
    _study(runtime, alice, degree="MSc", field_of_study="Cognitive Science", valid_to=date(2024, 6, 30))

    context = runtime.use_cases.get_person_context.execute(alice)

    assert [(row.education.degree, row.institution_name) for row in context.education] == [
        ("MSc", "University of Edinburgh"),
        ("BA", "University of Edinburgh"),
    ]
    assert context.affiliations == []


def test_the_timeline_shows_education_with_its_institution(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime, "Alice")
    _study(runtime, alice, degree="MSc", field_of_study="Cognitive Science", valid_from=date(2022, 9, 1))
    _study(runtime, alice, institution="Open University")

    timeline = runtime.use_cases.get_person_timeline.execute(alice)

    rows = [(entry.summary, entry.detail, entry.basis) for entry in timeline.entries if entry.entry_type == "education"]
    assert sorted(rows) == [
        ("MSc, Cognitive Science", "University of Edinburgh", "valid_from"),
        ("education", "Open University", "created_at"),
    ]


class TestImport:
    _CANDIDATES: list[dict[str, Any]] = [
        {"type": "person", "ref": "alice", "name": "Alice Ahmed", "aliases": []},
        {
            "type": "education",
            "person_ref": "alice",
            "institution": "University of Edinburgh",
            "degree": "MSc",
            "field_of_study": "Cognitive Science",
            "valid_from": "2022-09-01",
            "valid_to": "2024-06-30",
            "stated_by": "LinkedIn",
        },
        {"type": "affiliation", "person_ref": "alice", "org": "Presight", "role": "Data Scientist"},
    ]

    def _stage(self, runtime: ApplicationRuntime) -> str:
        return runtime.use_cases.stage_candidates.execute(
            "linkedin-profile", self._CANDIDATES, source_kind="document"
        ).batch_id

    def test_a_staged_degree_commits_into_an_education_record(self, runtime: ApplicationRuntime) -> None:
        batch = self._stage(runtime)
        rows = runtime.use_cases.review_import.execute(batch).candidates

        result = runtime.use_cases.commit_import.execute(batch, [row.id for row in rows])

        assert result.unresolved_ids == []
        alice = runtime.conn.execute("SELECT id FROM persons WHERE canonical_name = 'Alice Ahmed'").fetchone()["id"]
        context = runtime.use_cases.get_person_context.execute(alice)
        [studied] = context.education
        assert (studied.education.degree, studied.education.field_of_study, studied.institution_name) == (
            "MSc",
            "Cognitive Science",
            "University of Edinburgh",
        )
        assert studied.education.provenance.stated_by == "LinkedIn"
        assert [row.organization_name for row in context.affiliations] == ["Presight"]
        timeline = runtime.use_cases.get_person_timeline.execute(alice)
        [entry] = [entry for entry in timeline.entries if entry.entry_type == "education"]
        assert entry.source_session_id is not None

    def test_review_lines_describe_the_degree(self, runtime: ApplicationRuntime) -> None:
        from people_context.cli.rendering import import_review_lines

        rows = runtime.use_cases.review_import.execute(self._stage(runtime)).candidates

        [line] = [line for line in import_review_lines(rows) if "  education  " in line]
        assert "MSc, Cognitive Science at University of Edinburgh — Alice Ahmed" in line

    def test_an_education_candidate_with_an_inverted_period_is_refused(self, runtime: ApplicationRuntime) -> None:
        candidates = [dict(row) for row in self._CANDIDATES[:2]]
        candidates[1].update(valid_from="2024-06-30", valid_to="2022-09-01")

        with pytest.raises(Exception, match="candidate validation failed"):
            runtime.use_cases.stage_candidates.execute("linkedin-profile", candidates)
