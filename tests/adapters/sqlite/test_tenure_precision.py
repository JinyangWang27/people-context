"""Affiliation and education dates stated to a year or a month, end to end through SQLite."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.filesystem.vault_writer import FileSystemVaultWriter
from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.adapters.sqlite.db import open_db
from people_context.adapters.sqlite.record_store import SqliteRecordStore
from people_context.adapters.sqlite.vault_reader import SqliteVaultReader
from people_context.app.exports.brief import render_brief_markdown
from people_context.app.exports.sync_bundle import render_bundle_json
from people_context.app.people import RememberPersonInput
from people_context.app.records import CorrectRecordInput, RecordEducationInput, SetAffiliationInput
from people_context.domain.organization import Affiliation
from people_context.domain.sync_bundle import InvalidBundleError
from people_context.domain.tenure import DatePrecision

_NOW = datetime(2026, 11, 22, 9, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[ApplicationRuntime]:
    built = build_runtime(tmp_path / "origin.db", clock=_Clock())
    yield built
    built.close()


def _person(runtime: ApplicationRuntime, name: str = "Alice") -> str:
    return runtime.use_cases.remember_person.execute(RememberPersonInput(name=name)).person.id


def _role(runtime: ApplicationRuntime, person_id: str, **fields: Any) -> Affiliation:
    fields.setdefault("org", "Huawei")
    fields.setdefault("role", "Solution Manager")
    return runtime.use_cases.set_affiliation.execute(SetAffiliationInput(person_id=person_id, **fields))


def _precisions(affiliation: Affiliation) -> tuple[DatePrecision, DatePrecision]:
    return affiliation.period.valid_from_precision, affiliation.period.valid_to_precision


def test_migration_014_reads_existing_rows_back_as_exact_days(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    migrations = resources.files("people_context.adapters.sqlite.migrations")
    for name in ("010_schema.sql", "011_person_names.sql", "012_family_name_order.sql", "013_education.sql"):
        conn.executescript(migrations.joinpath(name).read_text())
    conn.execute("PRAGMA user_version = 13")
    conn.executescript(
        """
        INSERT INTO persons (id, canonical_name, canonical_name_normalized, created_at, updated_at)
        VALUES ('p', 'Alice', 'alice', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        INSERT INTO organizations (id, name, name_normalized) VALUES ('o', 'Acme', 'acme');
        INSERT INTO affiliations (id, person_id, org_id, role, valid_from, valid_to, provenance_source, created_at)
        VALUES ('a', 'p', 'o', 'Engineer', '2020-03-04', NULL, 'agent', '2026-01-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    upgraded = open_db(path)
    try:
        stored = SqliteRecordStore(upgraded).get_record("affiliation", "a")
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 14
    finally:
        upgraded.close()

    assert isinstance(stored, Affiliation)
    assert stored.period.valid_from == date(2020, 3, 4)
    assert _precisions(stored) == (DatePrecision.DAY, DatePrecision.DAY)


def test_a_role_stated_in_years_round_trips_as_years(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime)

    written = _role(runtime, alice, valid_from="2012", valid_to="2018-06")
    stored = runtime.records.get_record("affiliation", written.id)

    assert stored == written
    assert (written.period.from_text, written.period.to_text) == ("2012", "2018-06")
    assert (written.period.valid_from, written.period.valid_to) == (date(2012, 1, 1), date(2018, 6, 30))


def test_an_exact_repeat_matches_only_at_the_same_precision(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime)

    first = _role(runtime, alice, valid_from="2015")
    repeat = _role(runtime, alice, valid_from="2015")
    a_day = _role(runtime, alice, valid_from="2015-01-01")

    assert repeat.id == first.id
    assert a_day.id != first.id
    assert _precisions(a_day)[0] is DatePrecision.DAY


def test_education_stated_in_years_is_kept_as_years(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime)

    studied = runtime.use_cases.record_education.execute(
        RecordEducationInput(
            person_id=alice, institution="Tsinghua University", degree="PhD", valid_from="2010", valid_to="2015"
        )
    )
    [row] = runtime.use_cases.get_person_context.execute(alice).education

    assert row.education == studied
    assert (row.education.period.from_text, row.education.period.to_text) == ("2010", "2015")


def test_person_context_reports_a_current_role_with_its_precision(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime)
    _role(runtime, alice, org="Presight", role="Head of BU", valid_from="2026-06")

    [current] = runtime.use_cases.get_person_context.execute(alice).affiliations
    period = current.affiliation.period

    assert (period.valid_from, period.valid_from_precision) == (date(2026, 6, 1), DatePrecision.MONTH)


def test_correcting_a_date_takes_the_precision_it_is_given(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime)
    written = _role(runtime, alice, valid_from="2015-03-04", valid_to="2018-05-06")

    def correct(**fields: Any) -> Affiliation:
        updated = runtime.use_cases.correct_record.execute(
            CorrectRecordInput(entity_type="affiliation", entity_id=written.id, fields=fields)
        )
        assert isinstance(updated, Affiliation)
        assert runtime.records.get_record("affiliation", written.id) == updated
        return updated

    to_a_year = correct(valid_from="2015")
    assert (to_a_year.period.from_text, to_a_year.period.to_text) == ("2015", "2018-05-06")

    to_a_month = correct(valid_to="2018-05")
    assert (to_a_month.period.valid_to, to_a_month.period.valid_to_precision) == (
        date(2018, 5, 31),
        DatePrecision.MONTH,
    )

    back_to_a_day = correct(valid_from="2015-03-04", valid_to=None)
    assert _precisions(back_to_a_day) == (DatePrecision.DAY, DatePrecision.DAY)
    assert back_to_a_day.period.valid_to is None

    role_only = correct(role="Senior Solution Manager")
    assert role_only.period == back_to_a_day.period


def test_the_timeline_and_brief_render_a_year_as_a_year(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime)
    _role(runtime, alice, valid_from="2012", valid_to="2018")

    timeline = runtime.use_cases.get_person_timeline.execute(alice)
    [entry] = [entry for entry in timeline.entries if entry.entry_type == "affiliation"]
    document = runtime.use_cases.compose_person_brief.execute(alice, include_history=True)

    assert (entry.valid_from_precision, entry.valid_to_precision) == (DatePrecision.YEAR, DatePrecision.YEAR)
    assert entry.effective_at == datetime(2012, 1, 1, tzinfo=UTC)
    assert document is not None
    assert "valid 2012 to 2018" in render_brief_markdown(document)


def test_the_vault_renders_a_current_role_to_its_stated_month(runtime: ApplicationRuntime, tmp_path: Path) -> None:
    alice = _person(runtime)
    _role(runtime, alice, org="Presight", role="Head of BU", valid_from="2026-06")

    snapshot = SqliteVaultReader(runtime.conn, _Clock()).read_vault()
    FileSystemVaultWriter().write_vault(tmp_path / "vault", snapshot)
    text = "\n".join(path.read_text() for path in (tmp_path / "vault").rglob("*.md"))

    assert "active since 2026-06" in text
    assert "2026-06-01" not in text


def test_consolidation_context_carries_the_precision(runtime: ApplicationRuntime) -> None:
    alice = _person(runtime)
    _role(runtime, alice, valid_from="2012", valid_to="2018")

    [row] = runtime.use_cases.get_consolidation_context.execute(alice).affiliations

    assert (row.valid_from, row.valid_from_precision) == (date(2012, 1, 1), DatePrecision.YEAR)


class TestPortability:
    def test_sync_restore_keeps_each_bound_at_its_precision(self, runtime: ApplicationRuntime, tmp_path: Path) -> None:
        alice = _person(runtime)
        role = _role(runtime, alice, valid_from="2012", valid_to="2018-06")
        studied = runtime.use_cases.record_education.execute(
            RecordEducationInput(person_id=alice, institution="UESTC", valid_from="2008", valid_to="2012")
        )
        document = runtime.use_cases.export_sync_bundle.execute()

        destination = build_runtime(tmp_path / "destination.db", clock=_Clock())
        try:
            parsed = destination.use_cases.restore_sync_bundle.parse(render_bundle_json(document))
            destination.use_cases.restore_sync_bundle.execute(parsed)
            restored_role = destination.records.get_record("affiliation", role.id)
            restored_study = destination.records.get_record("education", studied.id)
        finally:
            destination.close()

        assert restored_role == role
        assert restored_study == studied

    def test_a_bundled_bound_its_precision_contradicts_is_refused(self, runtime: ApplicationRuntime) -> None:
        _role(runtime, _person(runtime), valid_from="2012")
        payload = json.loads(render_bundle_json(runtime.use_cases.export_sync_bundle.execute()))
        payload["snapshot"]["affiliations"][0]["period"]["valid_from"] = "2012-03-01"

        with pytest.raises(InvalidBundleError):
            runtime.use_cases.restore_sync_bundle.parse(json.dumps(payload))


class TestImport:
    _CANDIDATES: list[dict[str, Any]] = [
        {"type": "person", "ref": "bruce", "name": "Bruce Li", "aliases": []},
        {
            "type": "affiliation",
            "person_ref": "bruce",
            "org": "Huawei",
            "role": "Smart City Senior Solution Manager",
            "valid_from": "2018",
            "valid_to": "2020",
            "stated_by": "LinkedIn",
        },
        {
            "type": "education",
            "person_ref": "bruce",
            "institution": "UESTC",
            "degree": "Bachelor's",
            "valid_from": "2008-09",
            "valid_to": "2012",
        },
    ]

    def test_staged_years_commit_as_years_and_review_shows_them_as_stated(self, runtime: ApplicationRuntime) -> None:
        from people_context.cli.rendering import import_review_lines

        batch = runtime.use_cases.stage_candidates.execute(
            "linkedin", self._CANDIDATES, source_kind="document"
        ).batch_id
        rows = runtime.use_cases.review_import.execute(batch).candidates
        lines = import_review_lines(rows)

        result = runtime.use_cases.commit_import.execute(batch, [row.id for row in rows])

        assert any("Smart City Senior Solution Manager at Huawei (2018–2020)" in line for line in lines)
        assert any("Bachelor's at UESTC (2008-09–2012)" in line for line in lines)
        assert result.unresolved_ids == []
        bruce = runtime.conn.execute("SELECT id FROM persons WHERE canonical_name = 'Bruce Li'").fetchone()["id"]
        timeline = runtime.use_cases.get_person_timeline.execute(bruce)
        spans = {
            entry.entry_type: (entry.valid_from_precision, entry.valid_to_precision)
            for entry in timeline.entries
            if entry.entry_type in ("affiliation", "education")
        }
        assert spans == {
            "affiliation": (DatePrecision.YEAR, DatePrecision.YEAR),
            "education": (DatePrecision.MONTH, DatePrecision.YEAR),
        }

    @pytest.mark.parametrize(("valid_from", "valid_to"), [("2020", "2018"), ("2018-13", None), ("Nov 2018", None)])
    def test_an_impossible_or_unparseable_span_is_refused_at_staging(
        self, runtime: ApplicationRuntime, valid_from: str, valid_to: str | None
    ) -> None:
        candidates = [dict(row) for row in self._CANDIDATES[:2]]
        candidates[1].update(valid_from=valid_from, valid_to=valid_to)

        with pytest.raises(Exception, match="candidate validation failed"):
            runtime.use_cases.stage_candidates.execute("linkedin", candidates)
