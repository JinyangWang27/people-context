"""Contract tests for the bundled root usage skill (M10.1).

The Claude Code marketplace validator checks manifests but not skill body content,
so these tests pin the skill's location, frontmatter, and the behavioural invariants
the M10 agent-utilization spec makes binding: resolution-first identity handling, the
strict staged-capture vocabulary, review-before-commit, and disclosure-gate framing.
"""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPOSITORY_ROOT / "skills" / "people-context-usage" / "SKILL.md"


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a Markdown file into its simple ``key: value`` frontmatter and body."""
    assert text.startswith("---\n"), "skill must open with YAML frontmatter"
    _, frontmatter, body = text.split("---\n", 2)
    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        assert separator, f"malformed frontmatter line: {line!r}"
        fields[key.strip()] = value.strip()
    return fields, body


class TestUsageSkill:
    """Verify the checked-in root usage skill contract."""

    def test_skill_lives_at_plugin_root(self) -> None:
        # Claude Code discovers skills under ``<plugin-root>/skills``; the marketplace
        # plugin source is the repository root, so the skill must live at repo-root
        # ``skills/`` and never inside the ``.claude-plugin/`` manifest directory.
        assert SKILL_PATH.is_file()
        assert not (REPOSITORY_ROOT / ".claude-plugin" / "skills").exists()

    def test_frontmatter_declares_matching_name_and_description(self) -> None:
        fields, _ = _split_frontmatter(SKILL_PATH.read_text(encoding="utf-8"))

        assert fields["name"] == "people-context-usage"
        assert fields["name"] == SKILL_PATH.parent.name
        assert fields["description"]

    def test_teaches_resolution_first_and_ambiguity_contract(self) -> None:
        body = SKILL_PATH.read_text(encoding="utf-8").lower()

        assert "resolve_person" in body
        assert "ambiguous" in body
        # Context and guidance are distinct reads, both after resolution.
        assert "get_person_context" in body
        assert "get_communication_guidance" in body

    def test_meeting_preparation_composes_existing_reads_only(self) -> None:
        body = SKILL_PATH.read_text(encoding="utf-8")
        lowered = body.lower()

        assert "meeting" in lowered
        # The brief is composed from resolution plus the three bounded reads, and adds no
        # server tool: identity first, then context, guidance, and that person's reminders.
        assert "resolve_person" in body
        assert "get_person_context" in body
        assert "get_communication_guidance" in body
        assert "list_reminders" in body
        # Guidance is fetched for every attendee, not only when the user says "tone":
        # the composed brief promises how to communicate with them. Compare against
        # collapsed whitespace so the assertion survives Markdown line wrapping.
        flowed = " ".join(lowered.split())
        assert "do not skip it because the user did not use the word" in flowed
        # Preparation records nothing; capture stays an after-the-fact user-approved flow.
        assert "read-only" in lowered

    def test_teaches_strict_candidate_vocabulary(self) -> None:
        body = SKILL_PATH.read_text(encoding="utf-8")

        for candidate_type in (
            "`person`",
            "`interaction`",
            "`affiliation`",
            "`fact`",
            "`observation`",
            "`trait`",
            "`relationship`",
        ):
            assert candidate_type in body
        # Concise fields only; raw source text is never copied into candidates.
        lowered = body.lower()
        assert "raw" in lowered and "transcript" in lowered
        assert "batch-local" in lowered

    def test_teaches_the_fact_observation_trait_distinction_for_unstructured_sources(self) -> None:
        body = SKILL_PATH.read_text(encoding="utf-8")
        lowered = " ".join(body.lower().split())

        # The three epistemic levels are the point of the extraction workflow: an agent that
        # flattens them writes a personality claim where the source only showed one behaviour.
        assert "transcript" in lowered
        assert "observation" in lowered and "trait" in lowered
        assert "evidence_note" in body and "confidence" in body
        # An inferred trait must never default to certainty or arrive without a derivation.
        assert "required" in lowered
        # Evidence notes are derivations, not copied passages.
        assert "never a quoted passage" in lowered or "never a transcript excerpt" in lowered

    def test_omits_elevated_relationships_rather_than_downgrading_them(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        # The durable relationship model has no sensitivity field, so an elevated edge stays
        # out of the graph entirely instead of entering it stripped of its protection.
        assert "ordinary-disclosure only" in lowered
        assert "sensitive or restricted relationship" in lowered
        assert "rather than downgrading it" in lowered

    def test_names_the_cli_staging_path_for_agents_without_mcp(self) -> None:
        body = SKILL_PATH.read_text(encoding="utf-8")
        lowered = " ".join(body.lower().split())

        assert "pctx import stage-candidates" in body
        # What the command takes is the distillation, never the source it came from.
        assert "candidate json — never the transcript" in lowered

    def test_treats_staging_as_proposal_and_never_auto_commits(self) -> None:
        body = SKILL_PATH.read_text(encoding="utf-8")

        assert "stage_candidates" in body
        assert "review_import" in body
        assert "commit_import" in body
        lowered = body.lower()
        # The commit step is an explicit, later, user-approved write — never automatic.
        assert "never call" in lowered and "commit_import" in body
        assert "automatically" in lowered

    def test_maintenance_reads_before_it_proposes_and_waits_for_approval(self) -> None:
        body = SKILL_PATH.read_text(encoding="utf-8")
        lowered = " ".join(body.lower().split())

        # The review is a read pass over the three bounded reads, ending in proposals.
        assert "get_consolidation_context" in body
        assert "get_person_timeline" in body
        assert "resolve_person" in body
        # A signal is evidence for a judgement, never an instruction to write.
        assert "not a verdict" in lowered
        assert "never an instruction to write" in lowered
        # Approval is explicit and per proposal, not implied by the request to review.
        assert "wait for explicit approval" in lowered
        assert "approval is per proposal" in lowered
        # Proposals are structured tool arguments by id, never shell-interpolated names.
        assert "never a shell command" in lowered

    def test_distinguishes_correction_from_temporal_supersession(self) -> None:
        body = SKILL_PATH.read_text(encoding="utf-8")
        lowered = " ".join(body.lower().split())

        assert "correct_record" in body and "supersede_fact" in body
        # The rule that keeps history: a value that was true is never overwritten in place.
        assert "the stored value was *wrong*" in lowered
        assert "never propose changing a historically correct fact's `value` in place" in lowered
        # The replacement inherits the original endpoint; a bounded claim is not widened.
        assert "inherits the old assertion's original end date" in lowered
        assert "stays open-ended" in lowered
        # A refused effective date is reported, not retried until it lands.
        assert "rather than nudging the date" in lowered

    def test_keeps_multiple_evidence_distinct_from_redundant_representation(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        # Three observations supporting one trait are evidence, not duplicates to collapse.
        assert "three pieces of evidence" in lowered
        assert "do not propose collapsing them to reduce row count" in lowered
        # Confidence is judgement, never a count of supporting rows.
        assert "not a count of it" in lowered
        # Genuine conflict is reported, not tidied away.
        assert "leave the conflict standing" in lowered

    def test_frames_disclosure_gates_as_expected_not_obstacles(self) -> None:
        body = SKILL_PATH.read_text(encoding="utf-8")

        # Elevated tools are named only to explain the gate, never as something to call
        # or to suggest enabling in order to widen disclosure.
        assert "get_sensitive_person_context" in body
        assert "export_data" in body
        lowered = body.lower()
        assert "work around" in lowered
        assert "call `get_sensitive_person_context`" not in lowered
        assert "call `export_data`" not in lowered

    def test_end_of_session_capture_proposes_without_committing(self) -> None:
        lowered = SKILL_PATH.read_text(encoding="utf-8").lower()

        assert "end of a session" in lowered or "wrapping up" in lowered
        # Capture is best-effort proposal-only; it must not promise a mechanical commit.
        assert "best-effort" in lowered
        assert "never call `commit_import`" in lowered


class TestQuickCaptureAndNameReads:
    def test_remember_is_for_direct_statements_only(self) -> None:
        body = SKILL_PATH.read_text(encoding="utf-8")
        lowered = " ".join(body.lower().split())

        assert "`remember` in a single call" in lowered
        assert "states directly" in lowered
        assert "never guesses between similar names" in lowered
        # Extraction still stages; the quick path does not replace review.
        assert "goes through the staged capture flow, never through a direct write" in lowered

    def test_elevated_records_are_framed_as_leaving_no_trace(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        # The ordinary bundle is the complete intended view, not a redacted one with a gap to probe.
        assert "leave no trace at all" in lowered
        assert "complete ordinary view" in lowered

    def test_a_lone_fuzzy_candidate_is_treated_like_ambiguity(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "read `match_reason`, not just the score" in lowered
        assert "spelling distance" in lowered
        # Matched hints raise the score, so the score alone cannot be the test.
        assert "a high score alone does not make a near-spelling into a match" in lowered

    def test_reads_accept_a_name_with_the_same_ambiguity_contract(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "accept `person` (the name as said)" in lowered
        assert "returns candidates instead of data" in lowered


class TestCvCapture:
    """The M22.2 workflow for a CV, biography, or page of background notes.

    The mechanism is M17 staging and M22.1 attribution; what M22.2 adds is the judgement that
    keeps a stored claim from reading as a verified one. These pin the parts an agent gets wrong
    on its own: promoting a self-description to a trait, inventing a day for a year-only degree,
    opening an affiliation that then reads as a current job, and trusting a legacy matcher to
    notice that two people share the name on the document.
    """

    def test_frames_a_document_as_asserting_rather_than_establishing(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "the document asserts, it does not establish" in lowered
        assert "attribution is not verification" in lowered

    def test_reports_unreadable_and_partial_documents_instead_of_guessing(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "never present a partial read as a complete cv" in lowered
        # The refusal example ends in nothing staged, not in a best guess.
        assert "stage nothing" in lowered

    def test_resolves_identity_before_staging_rather_than_trusting_the_matcher(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "an ambiguous match stays unresolved" in lowered
        assert "do not turn it into a new person" in lowered
        # A person/affiliation/fact batch is the released pre-M17 shape, whose matcher reports no
        # ambiguity at all — so the skill must not imply staging will catch it.
        assert "a unique handle binds the claim even when the name on the document" in lowered
        assert "fails the whole commit after the user has already reviewed it" in lowered

    def test_maps_employment_to_affiliations_and_background_to_facts(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "employment and education are `affiliation` candidates" in lowered
        assert "background are `fact` candidates" in lowered
        # A CV is a document, not something the agent witnessed.
        assert "a cv is not a meeting you sat in" in lowered

    def test_keeps_a_self_description_an_attributed_fact_rather_than_a_trait(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "`stated_by`" in lowered
        assert 'it never becomes a `trait` valued "analytical", because nobody observed it' in lowered

    def test_keeps_inexact_dates_in_the_claim_text(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "never invent a january 1, a month boundary, or a date taken from when you happened" in lowered
        assert "a recording date is not an event date" in lowered
        assert "months and days unknown" in lowered

    def test_refuses_to_open_an_affiliation_for_a_role_with_unknown_bounds(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "an affiliation with no `valid_to` reads as current everywhere" in lowered
        assert "silence about an end date is not a claim that the role continues" in lowered

    def test_treats_concurrent_roles_and_omissions_correctly(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "two roles at once are ordinary" in lowered
        assert "omission is not an ending" in lowered
        # Repetition across documents is not evidence strength.
        assert "is not thereby more likely to be true" in lowered

    def test_puts_sensitive_background_only_where_disclosure_is_enforceable(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "affiliations and relationships carry no sensitivity field, so they cannot protect anything" in lowered
        # Nor may it be copied sideways into a record that has no protection to give.
        assert "not into a `stated_by` string" in lowered

    def test_treats_a_source_receipt_as_processing_evidence_only(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "`content_digest` over the bytes you actually read" in lowered
        assert "never verification of the claims inside it" in lowered
        assert "no private source content, no filesystem path" in lowered

    def test_commits_only_what_was_explicitly_accepted_and_reads_back(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "commit only the candidates the user explicitly accepted" in lowered
        assert "report what committed and what stayed unresolved" in lowered

    def test_claims_neither_completeness_nor_semantic_deduplication(self) -> None:
        lowered = " ".join(SKILL_PATH.read_text(encoding="utf-8").lower().split())

        assert "neither pass claims to be complete" in lowered
        assert "not a survey of what is knowable about someone" in lowered
        assert "means the same thing as one already stored" in lowered
