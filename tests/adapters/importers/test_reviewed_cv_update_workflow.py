"""One fictional revised CV reviewed against records already stored (M24.2).

M24.2 introduces no mechanism. Staging with its review gate, `correct_record`, `supersede_fact`,
and the bounded reads were all released earlier; what the milestone adds is the restraint that
decides which of them a line of a *second* document belongs in, and how often the answer is none of
them.

These exercise that restraint against the real stores, seeded with the records the M22.2 capture
pass committed. The recurring failure they pin is the same one: treating the newest CV as the
profile. A role it no longer mentions gets closed, a title that was historically correct gets
overwritten, a year-only date becomes an invented transition boundary, and a second copy of one
claim reads as corroboration. Each of those is cheap to do and impossible to undo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from people_context.adapters.sqlite import (
    SqliteAuditLog,
    SqliteContextReader,
    SqliteImportStagingStore,
    SqliteOrganizationStore,
    SqlitePeopleRepository,
    SqlitePersonConsolidationReader,
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
    ReviewImport,
    StageCandidates,
)
from people_context.app.insights import SIGNAL_SUCCEEDING_FACT, GetConsolidationContext
from people_context.app.people import RememberPerson
from people_context.app.records import (
    REASON_AFTER_VALID_TO,
    REASON_NOT_AFTER_VALID_FROM,
    CorrectRecord,
    CorrectRecordInput,
    InvalidSupersessionError,
    RecordFact,
    RecordFactInput,
    RecordInteraction,
    RecordNotFoundError,
    RecordObservation,
    RecordTrait,
    SetAffiliation,
    SupersedeFact,
    SupersedeFactInput,
)
from people_context.app.relationships import SetRelationship
from people_context.domain.person import Alias, AliasKind, Person

_NOW = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)

#: What the first CV asserted, and what the review compares the second one against.
_ATTRIBUTION = "Nadia Okonkwo (CV)"

#: The revised document's own attribution. A second source is a second claim, never a second witness.
_REVISED_ATTRIBUTION = "Nadia Okonkwo (CV, revised)"

#: A distinctive line of the revised CV. People Context never receives the document, so this string
#: must not survive anywhere in the store.
_RAW_REVISED_LINE = "RAW-REVISED-CV-Nadia-lists-a-sabbatical-she-would-rather-not-explain"


class _Clock:
    def now(self) -> datetime:
        return _NOW


class _Harness:
    """The stored knowledge one capture pass left behind, plus the tools a review may use.

    The maintenance operations are wired beside the staging lifecycle deliberately: a review is not
    a second import. New claims still go through stage -> review -> commit, while a correction and a
    transition are separate, individually accepted mutations on records that already exist.
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
        self.records = records
        self.context = GetPersonContext(people, SqliteContextReader(self.conn), _Clock())
        self.consolidation = GetConsolidationContext(people, SqlitePersonConsolidationReader(self.conn))
        self.record_fact = RecordFact(people, records, audit, _Clock())
        self.correct = CorrectRecord(records, records, audit, _Clock(), people=people)
        self.supersede = SupersedeFact(records, records, audit, _Clock(), people=people)
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

    def stage(self, candidates: list[dict[str, Any]], *, strict: bool = True) -> Any:
        self._digests += 1
        return self._stage.execute(
            "nadia-cv-revised",
            candidates,
            strict_identity=strict,
            source_kind="cv",
            content_digest=f"{self._digests:064x}",
        )

    def accept_all(self, batch_id: str) -> Any:
        return self.commit.execute(batch_id, [row.id for row in self.review.execute(batch_id).candidates])

    def seed(self) -> str:
        """Commit the M22.2 capture batch, then add the one dated fact a transition can act on."""
        self.accept_all(self.stage(_captured_cv_batch()).batch_id)
        person_id = str(self.conn.execute("SELECT id FROM persons").fetchone()["id"])
        # A bounded assertion, so a later supersession has an original end date to inherit rather
        # than an open period it could quietly widen.
        self.record_fact.execute(
            RecordFactInput(
                person_id=person_id,
                predicate="city",
                value="Bristol",
                valid_from=date(2023, 4, 3),
                valid_to=date(2027, 12, 31),
                confidence=0.7,
                source="import",
                session="cv-1",
                stated_by=_ATTRIBUTION,
            )
        )
        return person_id

    def fact(self, predicate: str) -> Any:
        return self.conn.execute(
            "SELECT * FROM facts WHERE predicate = ? ORDER BY valid_from", (predicate,)
        ).fetchall()

    def affiliation(self, role: str) -> Any:
        return self.conn.execute("SELECT * FROM affiliations WHERE role = ?", (role,)).fetchone()

    def audit_rows(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])

    def record_counts(self) -> dict[str, int]:
        """Durable knowledge only. Staging writes its own audited rows and is not knowledge yet."""
        return {
            table: int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("persons", "facts", "affiliations", "observations", "traits")
        }

    def dump(self) -> str:
        return "\n".join(self.conn.iterdump())


def _person(name: str = "Nadia Okonkwo", *handles: str) -> dict[str, Any]:
    return {
        "type": "person",
        "ref": "nadia",
        "name": name,
        "aliases": [{"value": handle, "kind": "handle"} for handle in handles],
    }


def _captured_cv_batch() -> list[dict[str, Any]]:
    """What the M22.2 capture pass committed: the state this review compares against."""
    return [
        _person("Nadia Okonkwo", "nadia.okonkwo@example.com"),
        {
            "type": "affiliation",
            "person_ref": "nadia",
            "org": "Northbridge Analytics",
            "role": "Senior Data Engineer",
            "valid_from": "2023-04-03",
            "stated_by": _ATTRIBUTION,
            "confidence": 0.7,
        },
        {
            "type": "affiliation",
            "person_ref": "nadia",
            "org": "Harbour Data Trust",
            "role": "Board Member",
            "valid_from": "2025-01-15",
            "stated_by": _ATTRIBUTION,
            "confidence": 0.7,
        },
        {
            "type": "fact",
            "person_ref": "nadia",
            "predicate": "education",
            "value": "CV reports study at Northbridge College, 2017-2019; months and days unknown",
            "stated_by": _ATTRIBUTION,
            "confidence": 0.6,
        },
        {
            "type": "fact",
            "person_ref": "nadia",
            "predicate": "background",
            "value": "CV mentions a two-year medical career break",
            "sensitivity": "sensitive",
            "stated_by": _ATTRIBUTION,
            "confidence": 0.5,
        },
    ]


def test_the_bounded_reads_show_the_records_a_comparison_actually_needs() -> None:
    """Before M24.1 the employment half of a CV had nothing to be compared against."""
    harness = _Harness()
    person_id = harness.seed()

    context = harness.consolidation.execute(person_id)

    assert [entry.role for entry in context.affiliations] == ["Board Member", "Senior Data Engineer"]
    assert {entry.org_name for entry in context.affiliations} == {
        "Harbour Data Trust",
        "Northbridge Analytics",
    }
    # Dates and attribution are both present, because a proposal argues from both.
    assert [entry.valid_from for entry in context.affiliations] == [date(2025, 1, 15), date(2023, 4, 3)]
    assert {entry.provenance.stated_by for entry in context.affiliations} == {_ATTRIBUTION}
    assert {fact.predicate for fact in context.facts} == {"city", "education"}


def test_a_claim_the_store_already_holds_is_matched_rather_than_recorded_again() -> None:
    """`already represented` is a judgement made before staging, because nothing makes it after.

    The store deduplicates nothing semantically, and staging a repeat is not idempotent: it simply
    creates a second row. Neither copy gains confidence from the other's existence.
    """
    harness = _Harness()
    person_id = harness.seed()
    stored = harness.consolidation.execute(person_id).facts

    education = [fact for fact in stored if fact.predicate == "education"]
    assert len(education) == 1
    assert "2017-2019" in education[0].value

    # The revised CV repeats the same study. Staging it anyway is what the outcome exists to avoid.
    harness.accept_all(
        harness.stage(
            [
                _person("Nadia Okonkwo", "nadia.okonkwo@example.com"),
                {
                    "type": "fact",
                    "person_ref": "nadia",
                    "predicate": "education",
                    "value": "CV reports study at Northbridge College, 2017-2019; months and days unknown",
                    "stated_by": _REVISED_ATTRIBUTION,
                    "confidence": 0.6,
                },
            ]
        ).batch_id
    )

    rows = harness.fact("education")
    assert len(rows) == 2, "the store keeps both; only the review could have prevented the second"
    # A second document repeating a claim is not a second piece of evidence for it.
    assert {row["confidence"] for row in rows} == {0.6}


def test_an_exactly_dated_transition_preserves_the_old_value_and_its_original_end() -> None:
    harness = _Harness()
    person_id = harness.seed()
    before = harness.fact("city")[0]

    result = harness.supersede.execute(
        SupersedeFactInput(
            fact_id=before["id"],
            new_value="Leeds",
            effective_from=date(2026, 9, 1),
            source="agent",
            stated_by=_REVISED_ATTRIBUTION,
        )
    )

    assert (result.superseded.value, result.replacement.value) == ("Bristol", "Leeds")
    # The old assertion is closed the day before, keeping its value, its dates, and who asserted it.
    assert result.superseded.period.valid_from == date(2023, 4, 3)
    assert result.superseded.period.valid_to == date(2026, 8, 31)
    assert result.superseded.provenance.stated_by == _ATTRIBUTION
    assert result.superseded.recorded_at == datetime.fromisoformat(before["recorded_at"])
    # The replacement inherits the original end rather than widening a bounded claim.
    assert result.replacement.period.valid_from == date(2026, 9, 1)
    assert result.replacement.period.valid_to == date(2027, 12, 31)
    assert result.replacement.confidence == 0.7

    history = [(row["value"], row["valid_from"], row["valid_to"]) for row in harness.fact("city")]
    assert history == [
        ("Bristol", "2023-04-03", "2026-08-31"),
        ("Leeds", "2026-09-01", "2027-12-31"),
    ]

    # What a well-formed transition leaves behind: a succession, reported as history rather than
    # as a contradiction the store should resolve.
    context = harness.consolidation.execute(person_id)
    city_signals = [signal for signal in context.signals if signal.key == "city"]
    assert [signal.kind for signal in city_signals] == [SIGNAL_SUCCEEDING_FACT]


@pytest.mark.parametrize(
    ("guessed", "reason"),
    [
        # "since 2023" with no month: the only date a year offers is the one the fact already has.
        (date(2023, 4, 3), REASON_NOT_AFTER_VALID_FROM),
        # Reaching past the assertion's own end, as a year-end guess would.
        (date(2028, 1, 1), REASON_AFTER_VALID_TO),
    ],
)
def test_an_inexact_date_yields_no_transition_boundary(guessed: date, reason: str) -> None:
    """A year, a month, or the day the CV was read are not transition dates."""
    harness = _Harness()
    harness.seed()
    stored = harness.fact("city")[0]

    with pytest.raises(InvalidSupersessionError) as raised:
        harness.supersede.execute(
            SupersedeFactInput(fact_id=stored["id"], new_value="Leeds", effective_from=guessed)
        )

    assert raised.value.reason == reason
    # Refused means unresolved, not "try another date until one is accepted".
    after = harness.fact("city")
    assert len(after) == 1
    assert (after[0]["value"], after[0]["valid_to"]) == ("Bristol", "2027-12-31")


def test_correcting_a_mere_disagreement_discards_the_rival_assertion() -> None:
    """Why a newer document contradicting a stored value is not, by itself, grounds to correct it.

    `correct_record` overwrites in place. If the newer CV is the only basis, the older claim leaves
    the readable record entirely and the disagreement stops being visible to anyone — which is the
    opposite of preserving a conflict the evidence cannot settle.
    """
    harness = _Harness()
    person_id = harness.seed()
    affiliation_id = harness.affiliation("Senior Data Engineer")["id"]

    harness.correct.execute(
        CorrectRecordInput(
            entity_type="affiliation",
            entity_id=affiliation_id,
            fields={"valid_from": date(2024, 4, 3)},
        )
    )

    context = harness.consolidation.execute(person_id)
    senior = next(entry for entry in context.affiliations if entry.role == "Senior Data Engineer")
    assert senior.valid_from == date(2024, 4, 3)
    # One row, one date: no readable affiliation still says 2023, so the conflict is gone rather
    # than kept, and the store offers no second row where the older claim survived.
    assert [entry.valid_from for entry in context.affiliations if entry.role == "Senior Data Engineer"] == [
        date(2024, 4, 3)
    ]
    assert [row["valid_from"] for row in harness.conn.execute("SELECT valid_from FROM affiliations")] == [
        "2024-04-03",
        "2025-01-15",
    ]


def test_a_changed_role_at_one_organisation_has_no_supported_transition() -> None:
    """There is no affiliation supersession, and a correction must not be used to fake one."""
    harness = _Harness()
    harness.seed()
    stored = harness.affiliation("Senior Data Engineer")

    # The narrow transition operation is for facts alone; an affiliation id is simply not found.
    with pytest.raises(RecordNotFoundError):
        harness.supersede.execute(
            SupersedeFactInput(
                fact_id=stored["id"], new_value="Principal Data Engineer", effective_from=date(2026, 3, 1)
            )
        )

    unchanged = harness.affiliation("Senior Data Engineer")
    assert (unchanged["role"], unchanged["valid_from"], unchanged["valid_to"]) == (
        "Senior Data Engineer",
        "2023-04-03",
        None,
    )


def test_a_separately_supported_new_role_does_not_close_the_old_one() -> None:
    """The old affiliation stays historically correct; the two simply stand together."""
    harness = _Harness()
    harness.seed()

    harness.accept_all(
        harness.stage(
            [
                _person("Nadia Okonkwo", "nadia.okonkwo@example.com"),
                {
                    "type": "affiliation",
                    "person_ref": "nadia",
                    "org": "Northbridge Analytics",
                    "role": "Principal Data Engineer",
                    "valid_from": "2026-03-01",
                    "stated_by": _REVISED_ATTRIBUTION,
                    "confidence": 0.6,
                },
            ]
        ).batch_id
    )

    assert harness.affiliation("Principal Data Engineer")["valid_from"] == "2026-03-01"
    # Adding one did not end the other, and nothing invented a closing date for it.
    assert harness.affiliation("Senior Data Engineer")["valid_to"] is None


def test_a_role_the_newer_cv_omits_is_neither_closed_nor_deleted() -> None:
    """Silence is not an ending. The commonest way a review destroys history."""
    harness = _Harness()
    person_id = harness.seed()

    # The revised CV mentions the day job and says nothing at all about the board seat.
    harness.accept_all(
        harness.stage(
            [
                _person("Nadia Okonkwo", "nadia.okonkwo@example.com"),
                {
                    "type": "fact",
                    "person_ref": "nadia",
                    "predicate": "certification",
                    "value": "CV reports a data engineering certification awarded 2026",
                    "stated_by": _REVISED_ATTRIBUTION,
                    "confidence": 0.6,
                },
            ]
        ).batch_id
    )

    board = harness.affiliation("Board Member")
    assert board is not None
    assert (board["valid_from"], board["valid_to"]) == ("2025-01-15", None)
    roles = {entry.role for entry in harness.consolidation.execute(person_id).affiliations}
    assert roles == {"Board Member", "Senior Data Engineer"}


def test_a_target_that_changed_after_review_is_caught_by_the_reread() -> None:
    """The acceptance was for the record as reviewed, not for whatever is there when it is written."""
    harness = _Harness()
    person_id = harness.seed()
    reviewed = harness.consolidation.execute(person_id).facts
    reviewed_city = next(fact for fact in reviewed if fact.predicate == "city")
    assert reviewed_city.valid_to == date(2027, 12, 31)

    # Somebody else corrects the end date between the review and the write.
    harness.correct.execute(
        CorrectRecordInput(
            entity_type="fact",
            entity_id=reviewed_city.fact_id,
            fields={"valid_to": date(2026, 6, 30)},
        )
    )

    reread = next(
        fact for fact in harness.consolidation.execute(person_id).facts if fact.predicate == "city"
    )
    assert reread.valid_to == date(2026, 6, 30), "the reread sees a different record than was accepted"

    # Applying the stale acceptance anyway is refused rather than silently retargeted.
    with pytest.raises(InvalidSupersessionError) as raised:
        harness.supersede.execute(
            SupersedeFactInput(
                fact_id=reviewed_city.fact_id, new_value="Leeds", effective_from=date(2026, 9, 1)
            )
        )

    assert raised.value.reason == REASON_AFTER_VALID_TO
    assert len(harness.fact("city")) == 1


def test_a_run_that_fails_partway_leaves_the_earlier_action_committed() -> None:
    """Separate calls are separate. There is no collective rollback to claim, or to rely on."""
    harness = _Harness()
    harness.seed()
    affiliation_id = harness.affiliation("Senior Data Engineer")["id"]

    # Accepted action one: a genuine error in the stored start year.
    harness.correct.execute(
        CorrectRecordInput(
            entity_type="affiliation",
            entity_id=affiliation_id,
            fields={"valid_from": date(2024, 4, 3)},
        )
    )
    after_first = harness.audit_rows()

    # Accepted action two, on a date the transition contract refuses.
    with pytest.raises(InvalidSupersessionError):
        harness.supersede.execute(
            SupersedeFactInput(
                fact_id=harness.fact("city")[0]["id"], new_value="Leeds", effective_from=date(2028, 1, 1)
            )
        )

    # The first action stands; reporting it as rolled back would be a false report.
    assert harness.affiliation("Senior Data Engineer")["valid_from"] == "2024-04-03"
    # The failed action wrote nothing at all, not even half of its pair of rows.
    assert harness.audit_rows() == after_first
    assert len(harness.fact("city")) == 1


def test_an_ambiguous_subject_updates_nothing() -> None:
    """No retargeted writes: a revised CV is not applied to whichever person looked likeliest."""
    harness = _Harness()
    harness.seed()
    for alias in ("jamie@example.com", "j.okonkwo@example.com"):
        harness.people.save_person(
            Person(canonical_name="Jamie Okonkwo", aliases=[Alias(value=alias, kind=AliasKind.HANDLE)])
        )
    before = harness.record_counts()

    batch = harness.stage(
        [
            _person("Jamie Okonkwo"),
            {
                "type": "fact",
                "person_ref": "nadia",
                "predicate": "certification",
                "value": "Revised CV reports a data engineering certification awarded 2026",
                "stated_by": _REVISED_ATTRIBUTION,
            },
        ]
    )
    result = harness.accept_all(batch.batch_id)

    assert result.committed_ids == []
    assert result.unresolved_ids != []
    # Staging is an audited proposal and leaves its own trace; what must not move is the knowledge.
    assert harness.record_counts() == before
    assert harness.affiliation("Senior Data Engineer")["role"] == "Senior Data Engineer"


def test_sensitive_background_stays_protected_through_the_review() -> None:
    """Adding affiliations to the comparison opened no second route to a protected fact."""
    harness = _Harness()
    person_id = harness.seed()

    ordinary = harness.context.execute(person_id).model_dump_json()
    elevated = harness.context.execute(person_id, include_sensitive=True).model_dump_json()
    context = harness.consolidation.execute(person_id)

    assert "medical career break" not in ordinary
    assert "medical career break" in elevated
    assert "medical career break" not in context.model_dump_json()
    # The unprotected records are all still there, so this is disclosure and not a failed read.
    assert {entry.role for entry in context.affiliations} == {"Board Member", "Senior Data Engineer"}


def test_the_revised_document_itself_reaches_no_durable_row() -> None:
    """People Context reads no CV, first or second. Only distilled claims ever arrive."""
    harness = _Harness()
    harness.seed()

    harness.accept_all(
        harness.stage(
            [
                _person("Nadia Okonkwo", "nadia.okonkwo@example.com"),
                {
                    "type": "fact",
                    "person_ref": "nadia",
                    "predicate": "certification",
                    "value": "CV reports a data engineering certification awarded 2026",
                    "stated_by": _REVISED_ATTRIBUTION,
                    "confidence": 0.6,
                },
            ]
        ).batch_id
    )

    assert _RAW_REVISED_LINE not in harness.dump()
