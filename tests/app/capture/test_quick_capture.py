"""QuickCapture: one statement, one transaction, never a guessed identity."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from people_context.adapters.runtime import ApplicationRuntime, build_runtime
from people_context.app.capture import CONFIDENT_WRITE_SCORE, QuickCaptureInput, classify_note
from people_context.app.people import RememberPersonInput
from people_context.domain.shared import Sensitivity
from people_context.domain.trait import TraitCategory


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[ApplicationRuntime]:
    built = build_runtime(tmp_path / "capture.db")
    try:
        yield built
    finally:
        built.close()


def _seed(runtime: ApplicationRuntime, name: str, **kwargs: object) -> str:
    return runtime.use_cases.remember_person.execute(RememberPersonInput(name=name, **kwargs)).person.id  # type: ignore[arg-type]


class TestClassifyNote:
    @pytest.mark.parametrize(
        ("note", "kind", "category"),
        [
            ("prefers short emails and hates surprise calls", "trait", TraitCategory.COMMUNICATION_STYLE),
            ("likes hiking", "trait", TraitCategory.PREFERENCE),
            ("allergic to peanuts", "trait", TraitCategory.PREFERENCE),
            ("avoid the reorg topic", "trait", TraitCategory.TOPICS_TO_AVOID),
            ("had coffee, discussed the Q3 plan", "interaction", None),
            ("met her at the conference yesterday", "interaction", None),
            ("moved to Berlin", "fact", None),
            ("two kids, Ada and Ben", "fact", None),
        ],
    )
    def test_fixed_keyword_rule(self, note: str, kind: str, category: TraitCategory | None) -> None:
        assert classify_note(note) == (kind, category)


class TestIdentity:
    def test_creates_the_person_when_nobody_matches(self, runtime: ApplicationRuntime) -> None:
        result = runtime.use_cases.quick_capture.execute(QuickCaptureInput(person="Alice Ng", note="moved to Berlin"))

        assert result.status == "recorded" and result.created
        assert result.canonical_name == "Alice Ng"
        assert [record.kind for record in result.recorded] == ["fact"]
        assert runtime.repo.get(result.person_id or "") is not None

    def test_attaches_to_an_exact_match_without_creating(self, runtime: ApplicationRuntime) -> None:
        existing = _seed(runtime, "Alice Ng")

        result = runtime.use_cases.quick_capture.execute(QuickCaptureInput(person="alice ng", note="moved to Berlin"))

        assert result.status == "recorded" and not result.created
        assert result.person_id == existing
        assert len(runtime.repo.list_people()) == 1

    def test_ambiguous_name_records_nothing(self, runtime: ApplicationRuntime) -> None:
        _seed(runtime, "Priya Raman")
        _seed(runtime, "Priya Shah")

        result = runtime.use_cases.quick_capture.execute(QuickCaptureInput(person="Priya", note="moved to Berlin"))

        assert result.status == "ambiguous"
        assert {candidate.canonical_name for candidate in result.candidates} == {"Priya Raman", "Priya Shah"}
        assert result.recorded == []
        assert len(runtime.repo.list_people()) == 2
        assert runtime.context_reader.list_facts(result.candidates[0].person_id) == []

    def test_loose_fuzzy_match_records_nothing(self, runtime: ApplicationRuntime) -> None:
        """A one-letter-off name may be shown as a candidate but must never receive the write."""
        existing = _seed(runtime, "Alicia Stone")

        result = runtime.use_cases.quick_capture.execute(QuickCaptureInput(person="Alicja Stone", note="x"))

        assert result.status == "unconfirmed"
        assert result.candidates[0].person_id == existing
        assert result.candidates[0].score < CONFIDENT_WRITE_SCORE
        assert runtime.context_reader.list_facts(existing) == []
        assert len(runtime.repo.list_people()) == 1

    def test_bare_name_records_nothing(self, runtime: ApplicationRuntime) -> None:
        result = runtime.use_cases.quick_capture.execute(QuickCaptureInput(person="Alice Ng"))

        assert result.status == "nothing_to_record"
        assert runtime.repo.list_people() == []


class TestRecording:
    def test_org_and_note_in_one_call(self, runtime: ApplicationRuntime) -> None:
        result = runtime.use_cases.quick_capture.execute(
            QuickCaptureInput(person="Alice Ng", note="prefers short emails", org="Acme")
        )

        kinds = [record.kind for record in result.recorded]
        assert kinds == ["affiliation", "trait"]
        assert result.recorded[0].summary == "member at Acme"
        context = runtime.use_cases.get_person_context.execute(result.person_id or "", purpose="communication")
        assert [item.organization_name for item in context.affiliations] == ["Acme"]
        assert [trait.category for trait in context.traits] == [TraitCategory.COMMUNICATION_STYLE]

    def test_explicit_kind_overrides_the_rule(self, runtime: ApplicationRuntime) -> None:
        result = runtime.use_cases.quick_capture.execute(
            QuickCaptureInput(person="Alice Ng", note="likes hiking", kind="fact", predicate="hobby")
        )

        assert result.recorded[0].kind == "fact"
        assert result.recorded[0].summary == "hobby: likes hiking"

    def test_interaction_lists_the_person_as_participant(self, runtime: ApplicationRuntime) -> None:
        result = runtime.use_cases.quick_capture.execute(
            QuickCaptureInput(person="Alice Ng", note="had coffee today about the launch")
        )

        interactions = runtime.context_reader.list_interactions(result.person_id or "")
        assert result.recorded[0].kind == "interaction"
        assert [item.participant_ids for item in interactions] == [[result.person_id]]

    def test_relationship_is_recorded_from_self(self, runtime: ApplicationRuntime) -> None:
        me = _seed(runtime, "Me", is_self=True)

        result = runtime.use_cases.quick_capture.execute(
            QuickCaptureInput(person="Alice Ng", relationship="manager of")
        )

        assert result.recorded[0].kind == "relationship"
        edges = runtime.context_reader.list_active_relationships(result.person_id or "", runtime.clock.now().date())
        assert len(edges) == 1
        assert {edges[0].relationship.subject_id, edges[0].relationship.object_id} == {me, result.person_id}

    def test_relationship_without_self_records_nothing(self, runtime: ApplicationRuntime) -> None:
        result = runtime.use_cases.quick_capture.execute(
            QuickCaptureInput(person="Alice Ng", note="x", relationship="friend_of")
        )

        assert result.status == "no_self"
        assert runtime.repo.list_people() == []

    def test_sensitivity_is_carried_and_gates_ordinary_reads(self, runtime: ApplicationRuntime) -> None:
        result = runtime.use_cases.quick_capture.execute(
            QuickCaptureInput(person="Alice Ng", note="in treatment", sensitivity=Sensitivity.SENSITIVE)
        )

        context = runtime.use_cases.get_person_context.execute(result.person_id or "")
        assert context.facts == []
        assert context.withheld.sensitive == 1

    def test_every_write_shares_one_transaction_id(self, runtime: ApplicationRuntime) -> None:
        result = runtime.use_cases.quick_capture.execute(
            QuickCaptureInput(person="Alice Ng", note="moved to Berlin", org="Acme", role="CTO")
        )

        rows = runtime.conn.execute(
            "SELECT DISTINCT transaction_id FROM changelog WHERE entity_id IN (?, ?, ?)",
            (result.person_id, result.recorded[0].id, result.recorded[1].id),
        ).fetchall()
        assert len(rows) == 1 and rows[0][0] is not None

    def test_a_failing_write_rolls_back_the_person_it_created(
        self, runtime: ApplicationRuntime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("disk full")

        monkeypatch.setattr(runtime.use_cases.record_fact, "execute", boom)

        with pytest.raises(RuntimeError):
            runtime.use_cases.quick_capture.execute(QuickCaptureInput(person="Alice Ng", note="moved to Berlin"))

        assert runtime.repo.list_people() == []


class TestInvalidRequests:
    """Refused before any resolution or write: the person is never created for a request that records nothing."""

    @pytest.mark.parametrize(
        "data",
        [
            QuickCaptureInput(person="Alice Ng", note="x", kind="affiliation"),
            QuickCaptureInput(person="Alice Ng", note="x", kind="relationship"),
            QuickCaptureInput(person="Alice Ng", org="Acme", kind="fact"),
            QuickCaptureInput(person="Alice Ng", org="Mayo Clinic", role="patient", sensitivity=Sensitivity.SENSITIVE),
            QuickCaptureInput(person="Alice Ng", relationship="friend_of", sensitivity=Sensitivity.RESTRICTED),
        ],
    )
    def test_structural_kind_without_payload_or_with_elevated_sensitivity(
        self, runtime: ApplicationRuntime, data: QuickCaptureInput
    ) -> None:
        result = runtime.use_cases.quick_capture.execute(data)

        assert result.status == "invalid_request"
        assert result.recorded == []
        assert runtime.repo.list_people() == []

    def test_sensitive_note_alone_is_still_fine(self, runtime: ApplicationRuntime) -> None:
        result = runtime.use_cases.quick_capture.execute(
            QuickCaptureInput(person="Alice Ng", note="patient at Mayo Clinic", sensitivity=Sensitivity.SENSITIVE)
        )

        assert result.status == "recorded" and result.recorded[0].kind == "fact"


class TestStructuralPayloadsAreIndependentOfNoteKind:
    """`kind` says how to record the note; `org` and `relationship` record their own rows regardless."""

    @pytest.mark.parametrize("kind", ["auto", "fact", "trait", "interaction"])
    def test_org_is_recorded_alongside_any_note_kind(self, runtime: ApplicationRuntime, kind: str) -> None:
        result = runtime.use_cases.quick_capture.execute(
            QuickCaptureInput(person="Alice Ng", note="prefers short emails", kind=kind, org="Acme")  # type: ignore[arg-type]
        )

        assert result.status == "recorded"
        assert [record.kind for record in result.recorded][0] == "affiliation"
        assert len(result.recorded) == 2

    def test_relationship_is_recorded_alongside_an_explicit_note_kind(self, runtime: ApplicationRuntime) -> None:
        _seed(runtime, "Me", is_self=True)

        result = runtime.use_cases.quick_capture.execute(
            QuickCaptureInput(person="Alice Ng", note="moved to Berlin", kind="fact", relationship="manager of")
        )

        assert {record.kind for record in result.recorded} == {"relationship", "fact"}

    @pytest.mark.parametrize("kind", ["affiliation", "relationship"])
    def test_a_structural_kind_carrying_a_note_is_refused_rather_than_dropping_it(
        self, runtime: ApplicationRuntime, kind: str
    ) -> None:
        _seed(runtime, "Me", is_self=True)

        result = runtime.use_cases.quick_capture.execute(
            QuickCaptureInput(
                person="Alice Ng",
                note="moved to Berlin",
                kind=kind,  # type: ignore[arg-type]
                org="Acme",
                relationship="manager of",
            )
        )

        assert result.status == "invalid_request"
        assert "would not be" in (result.message or "")
        assert runtime.repo.list_people() == [] or [p.canonical_name for p in runtime.repo.list_people()] == ["Me"]
