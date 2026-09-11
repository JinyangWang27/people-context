"""One fictional transcript captured end to end (M26.2).

M26.1 introduced no mechanism. What it added is the judgement that decides which part of a
recorded conversation is supported enough to keep, and these exercise that judgement against the
real stores: a diarization split that the user confirmed is one person and not two, a shared room
microphone that can never be assigned whole, a participant who was handed a task and never
accepted it, an event with no date anyone could establish, and background that only a record type
with a sensitivity field may hold.

The batches here are hand-authored. That is the point and also the limit: committing one proves
the server accepts the shape the skill describes, not that a model produced it from a recording.
The extraction decisions themselves are assessed by a person against the worked cases in
`docs/transcript-review-examples.md` and the rubric in `docs/evals.md`.

The last check is the one the milestone spec names directly: the recording never enters People
Context, so a distinctive line of the fictional transcript, every speaker label in it, and the
working map between the two must appear in no staged row, no committed record, and no receipt.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteContextReader,
    SqliteImportStagingStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqliteRecordStore,
    SqliteRelationshipStore,
    SqliteRelationshipVocabularyStore,
    open_db,
)
from people_context.adapters.sqlite.source_store import SqliteImportSourceStore
from people_context.adapters.sqlite.trait_evidence import SqliteTraitEvidenceStore
from people_context.app.context import GetPersonContext
from people_context.app.imports import (
    CandidateStager,
    CommitImport,
    ImportPipelineError,
    ListImportSources,
    ReviewImport,
    ShowImportSource,
    StageCandidates,
)
from people_context.app.people import RememberPerson
from people_context.app.records import (
    RecordFact,
    RecordInteraction,
    RecordObservation,
    RecordTrait,
    SetAffiliation,
)
from people_context.app.relationships import SetRelationship
from people_context.domain.person import Alias, AliasKind, Person
from people_context.domain.staged_candidate import STAGED_CANDIDATE_MODELS

_NOW = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)

#: A line of the fictional transcript, deliberately distinctive. People Context is never given the
#: recording, so this string must not survive anywhere in the store or in anything it renders.
_RAW_TRANSCRIPT_LINE = (
    "RAW-TRANSCRIPT-LINE-[00:14:32]-Speaker-2-honestly-we-should-just-drop-the-Halden-tier-and-eat-the-churn"
)

#: Every label the recorder emitted. A label is an observation about one recording: it is not a
#: name, not an alias, and not something any durable field may carry.
_SPEAKER_LABELS = ("Speaker 1", "Speaker 2", "Speaker 3", "Speaker 4")

#: The user confirmed the first of these; the second is the agent's own working note. Neither is a
#: durable record, and the review that produced them stays in the conversation.
_SPEAKER_MAP_NOTE = "Speaker 1 and Speaker 3 are both Priya; Speaker 2 is Marcus or Tomas"

#: Attribution for a claim Priya made about herself, on the record and confirmed as hers.
_PRIYA_SAID = "Priya Raghunathan (planning call)"

#: The call had a date on the invitation, so an interaction is representable for it.
_CALL_DATE = "2026-09-04T14:00:00Z"


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _Harness:
    """The whole stage → review → commit lifecycle over one in-memory database.

    Batches are source-tracked, because a transcript review is exactly the case an M18 receipt
    exists for: the agent read an artifact People Context cannot see, and the receipt records that
    the processing happened without claiming anything about what was said in it.
    """

    def __init__(self) -> None:
        self.conn = open_db(":memory:")
        self._digests = 0
        people = SqlitePeopleRepository(self.conn)
        records = SqliteRecordStore(self.conn)
        audit = SqliteAuditLog(self.conn)
        staging = SqliteImportStagingStore(self.conn)
        sources = SqliteImportSourceStore(self.conn)
        evidence = SqliteTraitEvidenceStore(self.conn)
        self.people = people
        self.context = GetPersonContext(people, SqliteContextReader(self.conn), _Clock())
        self.list_sources = ListImportSources(sources)
        self.show_source = ShowImportSource(sources)
        self._stage = StageCandidates(CandidateStager(people, staging, _Clock(), sources, audit))
        self.review = ReviewImport(staging)
        self.commit = CommitImport(
            people,
            staging,
            RememberPerson(people, people, audit, _Clock()),
            RecordInteraction(people, records, audit, _Clock()),
            SetAffiliation(people, SqliteOrganizationStore(self.conn), records, audit, _Clock()),
            RecordFact(people, records, audit, _Clock()),
            RecordObservation(people, records, audit, _Clock()),
            RecordTrait(people, records, audit, _Clock(), evidence),
            SetRelationship(
                people,
                SqliteRelationshipStore(self.conn),
                audit,
                _Clock(),
                SqliteRelationshipVocabularyStore(self.conn),
            ),
            sources,
            audit,
            _Clock(),
            evidence,
        )

    def stage(self, candidates: list[dict[str, Any]], *, source: str = "halden-planning-call") -> Any:
        """Stage one tracked batch the way `pctx import stage-candidates` does.

        The digest stands in for one the agent computed over the export it actually read. The
        source name is a non-content label for the recording, never a line out of it.
        """
        self._digests += 1
        return self._stage.execute(
            source,
            candidates,
            strict_identity=True,
            source_kind="meeting_transcript",
            content_digest=f"{self._digests:064x}",
        )

    def rows(self, batch_id: str) -> list[Any]:
        return self.review.execute(batch_id).candidates

    def accept_all(self, batch_id: str) -> Any:
        return self.commit.execute(batch_id, [row.id for row in self.rows(batch_id)])

    def person_id(self, name: str) -> str:
        row = self.conn.execute("SELECT id FROM persons WHERE canonical_name = ?", (name,)).fetchone()
        assert row is not None, f"no person named {name}"
        return str(row["id"])

    def dump(self) -> str:
        """Every byte of durable state, for asserting what is absent from all of it."""
        return "\n".join(self.conn.iterdump())


def _person(ref: str, name: str, *handles: str) -> dict[str, Any]:
    return {
        "type": "person",
        "ref": ref,
        "name": name,
        "aliases": [{"value": handle, "kind": "handle"} for handle in handles],
    }


def _confirmed_batch() -> list[dict[str, Any]]:
    """The subset of one fictional call the user confirmed, and nothing else.

    Priya was split across two labels and confirmed as one person, so her two claims share a
    single `ref`. Marcus shared a microphone with Tomas: the one statement the user attributed to
    him by name is here, and the statement beside it is not, because confirming one does not
    resolve its neighbour. Dana was in the room and was offered a task she never answered, so she
    appears as a participant and as the subject of nothing.
    """
    return [
        _person("priya", "Priya Raghunathan", "priya.raghunathan@example.com"),
        _person("marcus", "Marcus Oyelaran", "m.oyelaran@example.com"),
        _person("dana", "Dana Whitfield", "dana.whitfield@example.com"),
        # A neutral summary of the call: confirmed participation and a date from the invitation.
        # It reports that the conversation happened, not that everyone in it agreed with it.
        {
            "type": "interaction",
            "summary": "Planning call on the Halden tier; pricing and migration sequencing discussed",
            "participant_refs": ["priya", "marcus", "dana"],
            "date": _CALL_DATE,
            "channel": "call",
            "sensitivity": "personal",
        },
        # Said under one label, repeated under another, confirmed as one person's claim.
        {
            "type": "affiliation",
            "person_ref": "priya",
            "org": "Northbridge Analytics",
            "role": "Staff Engineer",
            "valid_from": "2026-06-01",
            "stated_by": _PRIYA_SAID,
            "confidence": 0.7,
        },
        {
            "type": "fact",
            "person_ref": "priya",
            "predicate": "working_hours",
            "value": "Said she is off the afternoon rotation until the migration ships",
            "stated_by": _PRIYA_SAID,
            "confidence": 0.6,
        },
        # Background that needs protecting goes where a read can withhold it.
        {
            "type": "fact",
            "person_ref": "priya",
            "predicate": "background",
            "value": "Mentioned she is arranging care for a parent this quarter",
            "sensitivity": "sensitive",
            "stated_by": _PRIYA_SAID,
            "confidence": 0.5,
        },
        # The one statement under the shared microphone the user attributed by name. It records
        # what he did on this call; no trait generalises one call into a temperament.
        {
            "type": "observation",
            "person_ref": "marcus",
            "text": "Took the pricing writeup and gave the end of next week as the date",
            "observed_at": _CALL_DATE,
            "sensitivity": "personal",
        },
    ]


def test_a_confirmed_split_label_commits_one_person_rather_than_two() -> None:
    """Several labels, one person: the claims join up and nothing records how they were split."""
    harness = _Harness()

    result = harness.accept_all(harness.stage(_confirmed_batch()).batch_id)

    assert result.unresolved_ids == []
    assert harness.conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 3
    priya = harness.person_id("Priya Raghunathan")

    # Both labels' claims landed on the one person, and neither label became an alias for her.
    assert harness.conn.execute("SELECT COUNT(*) FROM facts WHERE person_id = ?", (priya,)).fetchone()[0] == 2
    assert harness.conn.execute("SELECT COUNT(*) FROM affiliations WHERE person_id = ?", (priya,)).fetchone()[0] == 1
    aliases = [row["value"] for row in harness.conn.execute("SELECT value FROM aliases WHERE person_id = ?", (priya,))]
    assert aliases == ["priya.raghunathan@example.com"]


def test_a_mixed_label_contributes_only_the_statement_that_was_confirmed() -> None:
    """One label, several people: the neighbouring statement stays out until it is clarified."""
    harness = _Harness()

    harness.accept_all(harness.stage(_confirmed_batch()).batch_id)

    marcus = harness.person_id("Marcus Oyelaran")
    observations = [row["text"] for row in harness.conn.execute("SELECT text FROM observations")]
    assert observations == ["Took the pricing writeup and gave the end of next week as the date"]
    assert harness.conn.execute("SELECT person_id FROM observations").fetchone()["person_id"] == marcus

    # The other occupant of that microphone was never confirmed, so no record exists for them and
    # no person was invented to hold the statement beside the confirmed one.
    assert harness.conn.execute("SELECT COUNT(*) FROM persons WHERE canonical_name = 'Tomas Berg'").fetchone()[0] == 0
    assert "eat the churn" not in harness.dump()


def test_participation_alone_produces_no_promise_and_no_attribution() -> None:
    """A task aimed at someone who never answered is a suggestion, not a commitment of theirs."""
    harness = _Harness()

    harness.accept_all(harness.stage(_confirmed_batch()).batch_id)
    dana = harness.person_id("Dana Whitfield")

    for table in ("facts", "observations", "traits", "affiliations"):
        count = harness.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE person_id = ?", (dana,)).fetchone()[0]
        assert count == 0, f"attendance created a {table} row"

    # She is in the interaction, which is the only thing attendance supports.
    assert harness.conn.execute(
        "SELECT COUNT(*) FROM interaction_participants WHERE person_id = ?", (dana,)
    ).fetchone()[0] == 1
    attributions = {
        row["provenance_stated_by"] for row in harness.conn.execute("SELECT provenance_stated_by FROM facts")
    }
    assert attributions == {_PRIYA_SAID}, "only the speaker whose claim it was is named"


def test_a_neutral_interaction_records_the_conversation_rather_than_agreement() -> None:
    harness = _Harness()

    harness.accept_all(harness.stage(_confirmed_batch()).batch_id)

    interaction = harness.conn.execute("SELECT summary, occurred_at, channel FROM interactions").fetchone()
    assert interaction["summary"] == "Planning call on the Halden tier; pricing and migration sequencing discussed"
    # The date came from the invitation, not from the moment the batch was staged.
    assert interaction["occurred_at"].startswith("2026-09-04")
    assert not interaction["occurred_at"].startswith(_NOW.date().isoformat())
    assert harness.conn.execute("SELECT COUNT(*) FROM interaction_participants").fetchone()[0] == 3


def test_an_unestablished_event_date_keeps_the_interaction_out_of_the_batch() -> None:
    """Without a date there is no representable interaction, and staging refuses rather than guessing."""
    harness = _Harness()

    with pytest.raises(ImportPipelineError) as raised:
        harness.stage(
            [
                _person("priya", "Priya Raghunathan"),
                {
                    "type": "interaction",
                    "summary": "Undated call about the Halden tier",
                    "participant_refs": ["priya"],
                },
            ]
        )

    # Refused for the missing date specifically, not for some other malformation.
    assert [detail["loc"][-1] for detail in raised.value.details["details"]] == ["date"]
    assert harness.conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


def test_date_independent_claims_still_commit_when_the_event_date_is_unknown() -> None:
    """The undated recording is not a dead end: what does not depend on the date proceeds."""
    harness = _Harness()

    batch = harness.stage(
        [
            _person("priya", "Priya Raghunathan"),
            {
                "type": "fact",
                "person_ref": "priya",
                "predicate": "working_hours",
                "value": "Said she is off the afternoon rotation until the migration ships",
                "stated_by": _PRIYA_SAID,
                "confidence": 0.6,
            },
        ]
    )
    result = harness.accept_all(batch.batch_id)

    assert len(result.committed_ids) == 2
    assert harness.conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0] == 0
    fact = harness.conn.execute("SELECT valid_from, valid_to FROM facts").fetchone()
    assert fact["valid_from"] is None and fact["valid_to"] is None


def test_an_ambiguous_name_commits_nothing_and_creates_no_person() -> None:
    """A first name two people answer to is a question for the user, not a candidate to pick."""
    harness = _Harness()
    for full in ("Sam Ekwueme", "Sam Okafor"):
        harness.people.save_person(
            Person(canonical_name=full, aliases=[Alias(value="Sam", kind=AliasKind.NICKNAME)])
        )

    batch = harness.stage(
        [
            _person("sam", "Sam"),
            {
                "type": "fact",
                "person_ref": "sam",
                "predicate": "team",
                "value": "Said he is moving to the platform team after the migration",
                "stated_by": "Sam (planning call)",
                "confidence": 0.5,
            },
        ]
    )
    staged = {row.candidate["type"]: row for row in harness.rows(batch.batch_id)}
    assert staged["person"].candidate["match_disposition"] == "ambiguous"
    assert staged["person"].candidate["matched_person_id"] is None

    result = harness.accept_all(batch.batch_id)

    assert result.committed_ids == []
    assert sorted(result.unresolved_ids) == sorted(row.id for row in staged.values())
    assert harness.conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 2
    assert harness.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0


def test_partial_confirmation_commits_only_the_accepted_subset() -> None:
    """Review shows the whole batch; acceptance decides which rows become records."""
    harness = _Harness()
    batch = harness.stage(_confirmed_batch())
    rows = harness.rows(batch.batch_id)

    accepted = [row.id for row in rows if row.candidate["type"] in {"person", "affiliation"}]
    result = harness.commit.execute(batch.batch_id, accepted)

    assert sorted(result.committed_ids) == sorted(accepted)
    assert harness.conn.execute("SELECT COUNT(*) FROM affiliations").fetchone()[0] == 1
    for table in ("facts", "observations", "traits", "interactions"):
        assert harness.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table

    statuses = {row.id: row.status for row in harness.rows(batch.batch_id)}
    assert all(statuses[row_id] == "committed" for row_id in accepted)
    assert all(status == "pending" for row_id, status in statuses.items() if row_id not in accepted)


def test_a_sensitive_claim_is_withheld_from_the_ordinary_read() -> None:
    harness = _Harness()

    harness.accept_all(harness.stage(_confirmed_batch()).batch_id)
    priya = harness.person_id("Priya Raghunathan")

    ordinary = harness.context.execute(priya).model_dump_json()
    elevated = harness.context.execute(priya, include_sensitive=True).model_dump_json()

    assert "care for a parent" not in ordinary
    assert "care for a parent" in elevated
    # The unprotected claims still show, so this is disclosure rather than a failed capture.
    assert "Staff Engineer" in ordinary


def test_a_sensitive_detail_is_never_moved_into_a_record_type_that_cannot_protect_it() -> None:
    """A relationship carries no sensitivity field, so it cannot hold this and must not try."""
    harness = _Harness()

    harness.accept_all(harness.stage(_confirmed_batch()).batch_id)

    assert harness.conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] == 0
    orgs = [row["name"] for row in harness.conn.execute("SELECT name FROM organizations")]
    assert orgs == ["Northbridge Analytics"]
    summaries = [row["summary"] for row in harness.conn.execute("SELECT summary FROM persons")]
    assert all(summary is None or "parent" not in summary for summary in summaries)


def test_a_follow_up_has_no_candidate_type_to_be_staged_as() -> None:
    """There are seven types and none of them is a reminder; a batch naming one is refused whole."""
    assert "reminder" not in set(STAGED_CANDIDATE_MODELS)
    harness = _Harness()

    with pytest.raises(ImportPipelineError) as raised:
        harness.stage(
            [
                _person("marcus", "Marcus Oyelaran"),
                {
                    "type": "reminder",
                    "person_ref": "marcus",
                    "text": "Chase the pricing writeup at the end of next week",
                    "due": "2026-09-18",
                },
            ]
        )

    assert raised.value.details["allowed_types"] == list(STAGED_CANDIDATE_MODELS)
    assert harness.conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0
    assert harness.conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0


def test_a_trait_has_no_owner_to_attach_to_when_the_speech_is_unattributed() -> None:
    """Speech with no confirmed speaker has no person, and a trait cannot be staged without one."""
    harness = _Harness()

    with pytest.raises(ImportPipelineError) as raised:
        harness.stage(
            [
                _person("marcus", "Marcus Oyelaran"),
                {
                    "type": "trait",
                    "person_ref": "unattributed_speaker",
                    "category": "communication_style",
                    "value": "Argues for cutting scope when a deadline slips",
                    "evidence_note": "From the churn remark at 00:14:32.",
                    "confidence": 0.4,
                },
            ]
        )

    assert "unknown person refs" in raised.value.details["details"][0]["msg"]
    assert harness.conn.execute("SELECT COUNT(*) FROM import_staging").fetchone()[0] == 0


def test_labels_do_not_carry_from_one_recording_to_the_next() -> None:
    """`Speaker 1` in a later export is a new observation, not last week's person."""
    harness = _Harness()
    harness.accept_all(harness.stage(_confirmed_batch()).batch_id)

    later = harness.stage(
        [
            _person("first_speaker", "Ines Duarte", "ines.duarte@example.com"),
            {
                "type": "fact",
                "person_ref": "first_speaker",
                "predicate": "team",
                "value": "Said she now leads the Halden migration review",
                "stated_by": "Ines Duarte (follow-up call)",
                "confidence": 0.6,
            },
        ],
        source="halden-followup-call",
    )
    harness.accept_all(later.batch_id)

    names = {row["canonical_name"] for row in harness.conn.execute("SELECT canonical_name FROM persons")}
    assert names == {"Priya Raghunathan", "Marcus Oyelaran", "Dana Whitfield", "Ines Duarte"}
    priya = harness.person_id("Priya Raghunathan")
    priya_aliases = {
        row["value"] for row in harness.conn.execute("SELECT value FROM aliases WHERE person_id = ?", (priya,))
    }
    assert "ines.duarte@example.com" not in priya_aliases


def test_no_marker_text_label_or_speaker_map_reaches_a_row_record_or_receipt() -> None:
    harness = _Harness()

    batch = harness.stage(_confirmed_batch())
    review = harness.review.execute(batch.batch_id)
    result = harness.accept_all(batch.batch_id)

    sources = harness.list_sources.execute()
    assert len(sources.sources) == 1
    shown = harness.show_source.execute(sources.sources[0].id)

    rendered = [
        review.model_dump_json(),
        result.model_dump_json(),
        json.dumps(batch.model_dump(mode="json")),
        sources.model_dump_json(),
        shown.model_dump_json(),
        harness.dump(),
    ]
    for document in rendered:
        assert _RAW_TRANSCRIPT_LINE not in document
        assert _SPEAKER_MAP_NOTE not in document
        for label in _SPEAKER_LABELS:
            assert label not in document, f"{label} reached a persisted surface"

    # The receipt is evidence of processing and says so with a digest, never with content.
    assert shown.source.source_kind == "meeting_transcript"
    assert shown.source.content_digest is not None
