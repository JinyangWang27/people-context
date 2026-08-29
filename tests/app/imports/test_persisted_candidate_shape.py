"""The persisted candidate shape restore validates is the shape the stager actually writes.

`domain/import_provenance.py` declares that shape so restore can refuse a bundle carrying a row
commit would raise on. That declaration is hand-written, and a hand-written copy of someone
else's output is exactly the thing that drifts: a field added to a candidate model, or a rename
in the reference rewrite, would silently make restore reject batches this installation produces
and leave a user's own backup unrestorable.

So nothing here asserts the table's contents from memory. Every candidate type is staged through
the real `CandidateStager`, and each row it persists is put through the bundle model that guards
restore. If the two ever disagree, this fails rather than the user's next `sync pull`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any, get_args

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteImportStagingStore,
    SqlitePeopleRepository,
    open_db,
)
from people_context.app.imports import CandidateStager, StageCandidates
from people_context.app.imports.identity import MatchDisposition
from people_context.app.people import AliasInput, RememberPerson, RememberPersonInput
from people_context.domain.import_provenance import (
    EVIDENCE_CAPABLE_STAGED_TYPES,
    STAGED_CANDIDATE_TYPES,
    staged_candidate_references,
    staged_evidence_references,
)
from people_context.domain.staged_candidate import (
    STAGED_CANDIDATE_MODELS,
    MatchDispositionValue,
)
from people_context.domain.sync_bundle import BundleStagingRow

_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
_BATCH = "01J00000000000000000BATCH1"


class _Clock:
    def now(self) -> datetime:
        return _NOW


def _stager(conn: sqlite3.Connection) -> StageCandidates:
    people = SqlitePeopleRepository(conn)
    return StageCandidates(
        CandidateStager(people, SqliteImportStagingStore(conn), _Clock(), None, SqliteAuditLog(conn))
    )


def _every_type() -> list[dict[str, Any]]:
    """One candidate of every staged type, with every optional field populated.

    Optionals are filled deliberately: `exclude_none=True` means an unset one is simply absent,
    so a table missing a field would still pass if nothing ever set it.
    """
    return [
        {
            "type": "person",
            "ref": "alice",
            "name": "Alice Ahmed",
            "aliases": [{"value": "alice@example.com", "kind": "handle"}],
            "summary": "Met at the Berlin offsite.",
            "message_id": "<msg-1@example.com>",
            "date": "2026-07-19T09:00:00Z",
        },
        {"type": "person", "ref": "bob", "name": "Bob Ali", "aliases": []},
        {
            "type": "interaction",
            "summary": "Roadmap review",
            "participant_refs": ["alice", "bob"],
            "date": "2026-07-19T10:00:00Z",
            "channel": "email",
            "message_id": "<msg-2@example.com>",
            "sensitivity": "personal",
            "evidence_ref": "review",
        },
        {
            "type": "affiliation",
            "person_ref": "alice",
            "org": "Globex",
            "role": "Designer",
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
            "confidence": 0.8,
        },
        {
            "type": "fact",
            "person_ref": "alice",
            "predicate": "city",
            "value": "Berlin",
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
            "confidence": 0.9,
            "sensitivity": "personal",
        },
        {
            "type": "observation",
            "person_ref": "alice",
            "text": "Asked for metrics before agreeing to the roadmap",
            "observed_at": "2026-07-19T10:30:00Z",
            "sensitivity": "personal",
            "evidence_ref": "metrics-question",
        },
        {
            "type": "trait",
            "person_ref": "alice",
            "category": "communication_style",
            "value": "Responds to quantitative evidence",
            "evidence_note": "Derived from the 19 Jul roadmap review.",
            "confidence": 0.65,
            "sensitivity": "personal",
            "evidence_refs": ["metrics-question", "review"],
            "evidence_ids": ["obs-1"],
        },
        {
            "type": "relationship",
            "from_ref": "alice",
            "to_ref": "bob",
            "relationship_type": "colleague of",
            "confidence": 0.7,
        },
    ]


def _staged_candidates(*, strict_identity: bool) -> dict[str, list[dict[str, Any]]]:
    conn = open_db(":memory:")
    # An existing person, so a staged person candidate carries a real match outcome rather than
    # the empty one an unmatched candidate would produce.
    RememberPerson(
        SqlitePeopleRepository(conn), SqlitePeopleRepository(conn), SqliteAuditLog(conn), _Clock()
    ).execute(
        RememberPersonInput(
            name="Alice Ahmed", aliases=[AliasInput(value="alice@example.com", kind="handle")]
        )
    )
    batch = _stager(conn).execute("weekly-sync", _every_type(), strict_identity=strict_identity)
    rows = conn.execute(
        "SELECT candidate_json FROM import_staging WHERE batch_id = ?", (batch.batch_id,)
    ).fetchall()
    conn.close()

    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        candidate = json.loads(row["candidate_json"])
        by_type.setdefault(candidate["type"], []).append(candidate)
    return by_type


@pytest.mark.parametrize("strict_identity", [True, False])
def test_every_persisted_candidate_satisfies_the_restore_contract(strict_identity: bool) -> None:
    """Both identity paths, because only the strict one adds the match disposition and count."""
    staged = _staged_candidates(strict_identity=strict_identity)

    assert set(staged) == set(STAGED_CANDIDATE_TYPES), "the fixture must cover every staged type"
    for candidates in staged.values():
        for candidate in candidates:
            BundleStagingRow.model_validate(
                {
                    "id": "01J0000000000000000STAGE01",
                    "batch_id": _BATCH,
                    "source": "weekly-sync",
                    "candidate": candidate,
                    "status": "pending",
                    "created_at": "2026-07-20T12:00:00Z",
                }
            )


def test_the_declared_shape_names_no_field_the_stager_never_writes() -> None:
    """The other direction: a field declared but never written is permission granted for nothing.

    Every declared field must appear on some really-staged candidate of its type, across both
    identity paths. A model that outlived the field it described would keep accepting a key the
    stager stopped producing — which is the one place unexplained text could still sit.
    """
    strict = _staged_candidates(strict_identity=True)
    loose = _staged_candidates(strict_identity=False)

    for kind in STAGED_CANDIDATE_TYPES:
        written: set[str] = set()
        for staged in (strict, loose):
            for candidate in staged[kind]:
                written |= set(candidate)
        declared = set(STAGED_CANDIDATE_MODELS[kind].model_fields)
        assert declared <= written, f"{kind} declares fields the stager never writes: {declared - written}"


def test_the_declared_match_dispositions_are_the_ones_matching_produces() -> None:
    """The domain cannot import the producing enum, so the two are pinned against each other."""
    assert set(get_args(MatchDispositionValue)) == {member.value for member in MatchDisposition}


def test_every_reference_the_stager_writes_names_a_row_of_the_kind_its_field_promises() -> None:
    """Restore refuses a reference to any other row, so this pins what each field may name.

    There are two reference namespaces and restore checks them separately. A person reference
    must name a person candidate, because that is the one map commit builds its resolution from.
    An evidence reference must name an observation or interaction candidate, because a trait
    resolves it through that candidate's commit mapping. A rule requiring person targets for both
    would refuse exactly the evidence rows the stager now writes.
    """
    conn = open_db(":memory:")
    RememberPerson(
        SqlitePeopleRepository(conn), SqlitePeopleRepository(conn), SqliteAuditLog(conn), _Clock()
    ).execute(
        RememberPersonInput(
            name="Alice Ahmed", aliases=[AliasInput(value="alice@example.com", kind="handle")]
        )
    )
    batch = _stager(conn).execute("weekly-sync", _every_type(), strict_identity=True)
    rows = conn.execute(
        "SELECT id, candidate_json FROM import_staging WHERE batch_id = ?", (batch.batch_id,)
    ).fetchall()
    conn.close()

    candidates = {row["id"]: json.loads(row["candidate_json"]) for row in rows}
    persons = {row_id for row_id, candidate in candidates.items() if candidate["type"] == "person"}
    evidence = {
        row_id
        for row_id, candidate in candidates.items()
        if candidate["type"] in EVIDENCE_CAPABLE_STAGED_TYPES
    }
    referenced: set[str] = set()
    evidence_referenced: set[str] = set()
    for candidate in candidates.values():
        referenced |= staged_candidate_references(candidate)
        evidence_referenced |= staged_evidence_references(candidate)

    assert referenced, "the fixture must contain candidates that reference people"
    assert evidence_referenced, "the fixture must contain a trait citing same-batch evidence"
    assert evidence_referenced <= evidence
    assert (referenced - evidence_referenced) <= persons
