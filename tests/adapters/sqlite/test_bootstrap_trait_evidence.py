"""Bundle version 3: trait evidence travels, and every accepted version stays fail-closed (M18.3)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteBootstrapRestorer,
    SqliteBundleReader,
    SqliteHybridLogicalClock,
    SqlitePeopleRepository,
    SqliteRecordStore,
    open_db,
)
from people_context.adapters.sqlite.trait_evidence import SqliteTraitEvidenceStore
from people_context.app.exports import ExportSyncBundle
from people_context.app.exports.sync_bundle import render_bundle_json
from people_context.app.people import RememberPerson, RememberPersonInput
from people_context.app.records import RecordInteraction, RecordObservation, RecordTrait
from people_context.app.records.interactions import RecordInteractionInput
from people_context.app.records.observations import RecordObservationInput
from people_context.app.records.traits import RecordTraitInput
from people_context.app.sync import RestoreSyncBundle
from people_context.domain.sync_bundle import SYNC_BUNDLE_VERSION, InvalidBundleError, TargetNotEmptyError

_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _Origin:
    """A source database that can record grounded traits and export a bundle."""

    def __init__(self, path: Path) -> None:
        self.conn = open_db(path)
        clock = _Clock()
        people = SqlitePeopleRepository(self.conn)
        records = SqliteRecordStore(self.conn)
        audit = SqliteAuditLog(self.conn)
        self.evidence = SqliteTraitEvidenceStore(self.conn)
        self.remember = RememberPerson(people, people, audit, clock)
        self.observe = RecordObservation(people, records, audit, clock)
        self.interact = RecordInteraction(people, records, audit, clock)
        self.trait = RecordTrait(people, records, audit, clock, self.evidence)

    def person(self, name: str) -> str:
        return self.remember.execute(RememberPersonInput(name=name)).person.id

    def export(self) -> Any:
        return ExportSyncBundle(SqliteBundleReader(self.conn), _Clock()).execute()


class _NullRestorer:
    def restore(self, document: Any) -> Any:  # pragma: no cover - parsing tests never restore
        raise AssertionError("parsing tests do not restore")


def _parse(text: str) -> Any:
    return RestoreSyncBundle(_NullRestorer()).parse(text)


def _destination(path: Path) -> tuple[sqlite3.Connection, SqliteBootstrapRestorer]:
    conn = open_db(path)
    return conn, SqliteBootstrapRestorer(conn, SqlitePeopleRepository(conn), SqliteHybridLogicalClock(conn))


def _restore(document: Any, path: Path) -> tuple[sqlite3.Connection, Any]:
    conn, restorer = _destination(path)
    return conn, RestoreSyncBundle(restorer).execute(document)


def _downgraded(document: Any, version: int) -> dict[str, Any]:
    """Present the export as an older accepted version by dropping what that version predates."""
    payload = json.loads(render_bundle_json(document))
    payload["version"] = version
    if version < 3:
        payload.pop("trait_evidence")
    if version < 2:
        payload.pop("imports")
    return payload


def _grounded(origin: _Origin) -> tuple[str, str, str]:
    """Record a trait grounded in both an observation and a shared interaction."""
    alice = origin.person("Alice Rivera")
    bob = origin.person("Bob Chen")
    observation = origin.observe.execute(
        RecordObservationInput(person_id=alice, text="Asked for metrics before agreeing")
    )
    interaction = origin.interact.execute(
        RecordInteractionInput(summary="Planning meeting", participant_ids=[alice, bob], occurred_at=_NOW)
    )
    trait = origin.trait.execute(
        RecordTraitInput(
            person_id=alice,
            category="communication_style",
            value="Responds to quantitative evidence",
            evidence_note="Twice in one week.",
            evidence_ids=[observation.id, interaction.id],
        )
    )
    return trait.id, observation.id, interaction.id


def test_export_emits_version_three_carrying_every_link(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    trait_id, observation_id, interaction_id = _grounded(origin)

    document = origin.export()

    assert document.version == SYNC_BUNDLE_VERSION == 3
    assert [(row.trait_id, row.evidence_type, row.evidence_id) for row in document.trait_evidence] == [
        (trait_id, "interaction", interaction_id),
        (trait_id, "observation", observation_id),
    ]


def test_a_restored_trait_still_names_the_records_it_rests_on(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    trait_id, observation_id, interaction_id = _grounded(origin)

    document = _parse(render_bundle_json(origin.export()))
    conn, outcome = _restore(document, tmp_path / "restored.db")

    assert outcome.trait_evidence == 2
    assert [
        (row["trait_id"], row["evidence_type"], row["evidence_id"])
        for row in conn.execute("SELECT * FROM trait_evidence ORDER BY evidence_type")
    ] == [(trait_id, "interaction", interaction_id), (trait_id, "observation", observation_id)]


@pytest.mark.parametrize("version", [1, 2])
def test_an_older_bundle_restores_and_carries_no_links(tmp_path: Path, version: int) -> None:
    origin = _Origin(tmp_path / "origin.db")
    _grounded(origin)

    document = _parse(json.dumps(_downgraded(origin.export(), version)))
    conn, outcome = _restore(document, tmp_path / "restored.db")

    assert outcome.trait_evidence == 0
    assert conn.execute("SELECT COUNT(*) FROM trait_evidence").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM traits").fetchone()[0] == 1


@pytest.mark.parametrize("version", [1, 2])
def test_an_older_bundle_carrying_version_three_state_is_refused(tmp_path: Path, version: int) -> None:
    """A reader that accepts a field must understand it, so an unknown one fails closed."""
    origin = _Origin(tmp_path / "origin.db")
    _grounded(origin)
    payload = json.loads(render_bundle_json(origin.export()))
    payload["version"] = version
    if version < 2:
        payload.pop("imports")

    with pytest.raises(InvalidBundleError) as excinfo:
        _parse(json.dumps(payload))

    assert any("trait_evidence" in detail for detail in excinfo.value.details)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_a_non_empty_evidence_table_refuses_every_accepted_version(tmp_path: Path, version: int) -> None:
    """Freshness is a property of the destination, not of the document being restored."""
    origin = _Origin(tmp_path / "origin.db")
    _grounded(origin)
    document = _parse(json.dumps(_downgraded(origin.export(), version)))

    destination = _Origin(tmp_path / "destination.db")
    _grounded(destination)
    # Every other table is emptied so the refusal can only be about the link rows themselves.
    # Cascade would take them with the traits, so the deletions run with the constraint off:
    # this is constructing a destination state, not exercising one the application produces.
    destination.conn.execute("PRAGMA foreign_keys=OFF")
    for table in (
        "traits",
        "observations",
        "interaction_participants",
        "interactions",
        "persons",
        "aliases",
        "person_search",
        "audit_log",
        "changelog",
    ):
        destination.conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed constants
    destination.conn.commit()
    destination.conn.close()
    _conn, restorer = _destination(tmp_path / "destination.db")

    with pytest.raises(TargetNotEmptyError) as excinfo:
        restorer.restore(document)

    assert excinfo.value.details == ("trait_evidence: 2 row(s)",)


def test_a_link_naming_a_record_the_bundle_omits_is_refused(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    trait_id, _observation_id, _interaction_id = _grounded(origin)
    payload = json.loads(render_bundle_json(origin.export()))
    payload["snapshot"]["observations"] = []

    with pytest.raises(InvalidBundleError) as excinfo:
        _parse(json.dumps(payload))

    assert any("unbundled observation" in detail for detail in excinfo.value.details)
    assert any(trait_id in detail for detail in excinfo.value.details)


def test_a_link_to_another_persons_record_is_refused(tmp_path: Path) -> None:
    """The subject rule is a document-level invariant, not merely a write-time check."""
    origin = _Origin(tmp_path / "origin.db")
    _grounded(origin)
    payload = json.loads(render_bundle_json(origin.export()))
    bob = next(person["id"] for person in payload["snapshot"]["people"] if person["canonical_name"] == "Bob Chen")
    payload["snapshot"]["observations"][0]["person_id"] = bob

    with pytest.raises(InvalidBundleError) as excinfo:
        _parse(json.dumps(payload))

    assert any("observation about another person" in detail for detail in excinfo.value.details)


def test_a_link_to_an_interaction_the_subject_did_not_join_is_refused(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    _grounded(origin)
    payload = json.loads(render_bundle_json(origin.export()))
    alice = next(person["id"] for person in payload["snapshot"]["people"] if person["canonical_name"] == "Alice Rivera")
    interaction = payload["snapshot"]["interactions"][0]
    interaction["participant_ids"] = [person_id for person_id in interaction["participant_ids"] if person_id != alice]

    with pytest.raises(InvalidBundleError) as excinfo:
        _parse(json.dumps(payload))

    assert any("interaction its subject did not join" in detail for detail in excinfo.value.details)


def test_a_repeated_link_is_refused(tmp_path: Path) -> None:
    origin = _Origin(tmp_path / "origin.db")
    _grounded(origin)
    payload = json.loads(render_bundle_json(origin.export()))
    payload["trait_evidence"].append(dict(payload["trait_evidence"][0]))

    with pytest.raises(InvalidBundleError) as excinfo:
        _parse(json.dumps(payload))

    assert any("duplicate trait evidence link" in detail for detail in excinfo.value.details)
