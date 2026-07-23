"""Contract tests for the user-invocable workflow skills (M10.2).

The Claude Code marketplace validator checks skill structure and frontmatter but
not body content, so these tests pin the location, frontmatter, and behavioural
invariants the M10 agent-utilization spec makes binding for the three workflows:

- ``/people-context:who`` resolves identity first and reads context only on a single
  unambiguous match;
- ``/people-context:remember`` distinguishes an explicit person assertion
  (``remember_person``) from extracted knowledge (``stage_candidates``) and never
  auto-commits;
- ``/people-context:reminders`` resolves an optional person before filtering and
  surfaces ambiguity instead of silently dropping the filter.

None of the workflows call or suggest the elevated ``get_sensitive_person_context``
or ``export_data`` tools.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPOSITORY_ROOT / "skills"
WORKFLOW_NAMES = ("who", "remember", "reminders")


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


def _skill_path(name: str) -> Path:
    return SKILLS_ROOT / name / "SKILL.md"


class TestWorkflowSkillContract:
    """Structural and frontmatter invariants shared by all three workflows."""

    @pytest.mark.parametrize("name", WORKFLOW_NAMES)
    def test_skill_lives_at_plugin_root(self, name: str) -> None:
        # Claude Code discovers skills under ``<plugin-root>/skills``; the marketplace
        # plugin source is the repository root, so each workflow must live at repo-root
        # ``skills/<name>/`` and never inside the ``.claude-plugin/`` manifest directory.
        assert _skill_path(name).is_file()
        assert not (REPOSITORY_ROOT / ".claude-plugin" / "skills").exists()

    @pytest.mark.parametrize("name", WORKFLOW_NAMES)
    def test_frontmatter_name_matches_directory(self, name: str) -> None:
        path = _skill_path(name)
        fields, _ = _split_frontmatter(path.read_text(encoding="utf-8"))

        # The invocation form ``/people-context:<name>`` derives from the skill name,
        # which must equal its directory name.
        assert fields["name"] == name
        assert fields["name"] == path.parent.name
        assert fields["description"]

    @pytest.mark.parametrize("name", WORKFLOW_NAMES)
    def test_is_user_invocable_only(self, name: str) -> None:
        # These are explicit user workflows, not model-triggered guidance: model
        # invocation is disabled so the write-capable /remember flow never auto-fires.
        fields, _ = _split_frontmatter(_skill_path(name).read_text(encoding="utf-8"))

        assert fields.get("disable-model-invocation") == "true"

    @pytest.mark.parametrize("name", WORKFLOW_NAMES)
    def test_consumes_arguments(self, name: str) -> None:
        body = _skill_path(name).read_text(encoding="utf-8")

        # Workflows act on the invocation argument, passed through as $ARGUMENTS.
        assert "$ARGUMENTS" in body

    @pytest.mark.parametrize("name", WORKFLOW_NAMES)
    def test_never_calls_or_suggests_elevated_tools(self, name: str) -> None:
        lowered = _skill_path(name).read_text(encoding="utf-8").lower()

        # Elevated tools may only be named to explain the privacy gate, never as a
        # step to perform or to suggest enabling in order to widen disclosure.
        assert "call `get_sensitive_person_context`" not in lowered
        assert "call `export_data`" not in lowered
        assert "enable" not in lowered or "never" in lowered

    @pytest.mark.parametrize("name", WORKFLOW_NAMES)
    def test_passes_distinguishing_context_via_hints(self, name: str) -> None:
        # Regression: resolve_person's hints (org/role/relationship) re-rank same-named
        # candidates; the name index has no org, so distinguishing context must be
        # parsed into hints in every resolving workflow.
        lowered = _skill_path(name).read_text(encoding="utf-8").lower()

        assert "hints" in lowered
        assert "org" in lowered


class TestWhoWorkflow:
    """``/people-context:who`` — resolve first, read only on one unambiguous match."""

    def test_resolves_before_reading_context(self) -> None:
        body = _skill_path("who").read_text(encoding="utf-8")

        assert "resolve_person" in body
        assert "get_person_context" in body
        # Resolution must be introduced before the context read.
        assert body.index("resolve_person") < body.index("get_person_context")

    def test_gates_on_ambiguous_flag_not_candidate_count(self) -> None:
        # Regression for the resolver contract: `ambiguous: false` can accompany
        # multiple ranked candidates, so a confident resolution must be read via the
        # `ambiguous` flag and the ranked top candidate, never by candidate count.
        lowered = _skill_path("who").read_text(encoding="utf-8").lower()

        assert "ambiguous" in lowered
        assert "not on candidate count" in lowered
        assert "`ambiguous: false`" in lowered
        assert "candidates[0]" in lowered
        assert "never silently pick" in lowered or "do not guess" in lowered or "never guess" in lowered

    def test_hints_not_relied_on_to_break_same_name_ties(self) -> None:
        # Regression: exact-name matches stay tied at 1.0 and a single hint's boost is
        # below the ambiguity gap, so hints cannot resolve a same-name tie; prefer a
        # unique alias and keep surfacing candidates when still ambiguous.
        lowered = _skill_path("who").read_text(encoding="utf-8").lower()

        assert "reliably break a tie" in lowered
        assert "do not loop on hints" in lowered
        assert "unique alias" in lowered

    def test_ambiguous_and_empty_perform_no_second_read(self) -> None:
        lowered = _skill_path("who").read_text(encoding="utf-8").lower()

        assert "candidate list" in lowered
        assert "empty candidate list" in lowered


class TestRememberWorkflow:
    """``/people-context:remember`` — assertion vs. extraction, never auto-commit."""

    def test_distinguishes_assertion_from_extraction(self) -> None:
        body = _skill_path("remember").read_text(encoding="utf-8")
        lowered = body.lower()

        assert "remember_person" in body
        assert "stage_candidates" in body
        assert "person assertion" in lowered
        # Extracted facts/affiliations/interactions route through staging.
        for candidate_type in ("`person`", "`interaction`", "`affiliation`", "`fact`"):
            assert candidate_type in body

    def test_never_auto_commits_and_never_stages_raw_text(self) -> None:
        body = _skill_path("remember").read_text(encoding="utf-8")
        lowered = body.lower()

        assert "commit_import" in body
        assert "do not" in lowered and "commit_import" in body
        assert "automatically" in lowered
        # Concise structured candidates only — never raw conversation/transcript text.
        assert "raw" in lowered and "transcript" in lowered

    def test_explicit_prior_context_capture_allowed_but_not_automatic(self) -> None:
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        # The spec routes explicitly-requested prior-context capture through this
        # workflow; only *automatic*/unprompted extraction is out of scope.
        assert "explicitly" in lowered
        assert "earlier conversation" in lowered or "prior context" in lowered
        assert "automatic" in lowered
        assert "do not trawl" in lowered or "unrelated" in lowered

    def test_resolves_direct_assertion_before_writing(self) -> None:
        # Regression: remember_person's lookup matches only the exact normalized name
        # (not aliases), so a partial-name assertion must resolve to the canonical
        # identity first or it creates a duplicate person.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "resolve the person first" in lowered
        assert "canonical name" in lowered
        assert "not the supplied aliases" in lowered or "exact normalized `name`" in lowered

    def test_uses_fixed_non_content_source_label(self) -> None:
        # Regression: stage_candidates persists `source` as durable provenance, so the
        # workflow must pass a fixed non-content label and never the raw description.
        body = _skill_path("remember").read_text(encoding="utf-8")
        lowered = body.lower()

        assert "claude-code-remember" in lowered
        assert "$ARGUMENTS" in body
        assert "provenance" in lowered
        assert "never" in lowered and "source" in lowered

    def test_self_already_exists_retries_to_record_alias(self) -> None:
        # Regression: "I also go by John" hits self_already_exists; the workflow must
        # retry against the existing self identity to record the alias, not drop it.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "self_already_exists" in lowered
        assert "retry" in lowered
        assert "alias" in lowered
        assert "nothing was recorded" in lowered

    def test_staging_duplicate_name_dependent_only_commit(self) -> None:
        # Regression: accepting a duplicate-canonical-name person candidate fails
        # (commit re-derives by canonical name -> ambiguous_person), but a dependent
        # record commits when the person is bound via any unique alias and its own row
        # is left unaccepted (CommitImport._existing_resolution uses matched_person_id).
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "ambiguous_person" in lowered
        assert "matched_person_id" in lowered
        assert "accept only the dependent" in lowered
        assert "unaccepted" in lowered
        assert "verified unique nickname, former name, or other non-handle alias" in lowered
        assert "person candidate's `name`" in lowered
        assert 'aliases` when marked `kind: "handle"`' in lowered
        assert "only when no alias resolves uniquely" in lowered

    def test_person_candidate_requires_aliases_field(self) -> None:
        # Regression: PersonCandidateInput.aliases is mandatory (no default), so a
        # {type, ref, name} row without it is rejected as invalid_candidates.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "aliases: []" in lowered
        assert "invalid_candidates" in lowered

    def test_staged_aliases_are_objects_with_handle_kind(self) -> None:
        # Regression: CandidateAlias requires objects {value, kind?, lang?, script?}; a
        # bare string list fails, and handle-based binding needs kind: "handle".
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "{value, kind?, lang?, script?}" in lowered
        assert "bare string list" in lowered
        assert '"handle"' in lowered

    def test_preserves_sensitivity_for_private_records(self) -> None:
        # Regression: fact/interaction candidates default sensitivity to personal, which
        # ordinary get_person_context/who disclose; private content needs a higher tier.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "sensitivity" in lowered
        assert "health" in lowered
        assert "sensitive" in lowered

    def test_private_affiliation_like_content_is_not_staged_ungated(self) -> None:
        # Regression: affiliations have no sensitivity field and ordinary context
        # returns them all, so private affiliation-like details need a gated fact
        # representation or must be reported unsupported.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "never stage private affiliation-like content as an `affiliation`" in lowered
        assert "have no `sensitivity` field" in lowered
        assert "returns every active affiliation without sensitivity filtering" in lowered
        assert "stage a `fact`" in lowered
        assert "cannot be safely staged" in lowered
        assert "do not emit an ungated affiliation" in lowered

    def test_self_assertion_guards_unrelated_contact(self) -> None:
        # Regression: with no self record, "I am Jane" resolving to an existing non-self
        # contact would mark that contact as the user; the workflow must not guess.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "unrelated contact" in lowered
        assert "is_self=true" in lowered
        assert "corrupt" in lowered

    def test_person_only_shared_name_update_reported_unsupported(self) -> None:
        # Regression: a person-only staged update to a shared-canonical-name person has
        # no dependent row to accept alone, and accepting the person row raises
        # ambiguous_person, so the case must be reported unsupported.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "person-only" in lowered
        assert "no dependent" in lowered
        assert "unsupported" in lowered

    def test_combined_shared_name_identity_update_is_not_silently_discarded(self) -> None:
        # Regression: dependent-only commits use the unaccepted person row only for
        # matched_person_id, so any new alias or summary on that row is not applied.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "combined identity-and-structured request" in lowered
        assert "record the structured portion only" in lowered
        assert "do not discard identity updates in a dependent-only commit" in lowered
        assert "must already belong to the matched person" in lowered
        assert "identity fields are not recorded" in lowered
        assert "identity portion is unsupported and was not recorded" in lowered
        assert "do not present the whole proposal as committable" in lowered

    def test_prior_context_derived_content_always_stages(self) -> None:
        # Regression: the M10 contract keeps prior-context-derived content behind the
        # review gate, so even an extracted identity detail must stage, not direct-write.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "prior context always stages" in lowered
        assert "pending review" in lowered
        assert "directly in this invocation" in lowered

    def test_direct_write_reports_duplicate_name_limitation(self) -> None:
        # Regression: remember_person has no person_id parameter, but its name lookup
        # matches ANY alias kind, so a non-unique canonical name is targeted via any
        # unique alias; only when none resolves uniquely is the limitation reported.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "identically-named" in lowered
        assert "any unique alias" in lowered

    def test_interaction_requires_occurrence_date(self) -> None:
        # Regression: InteractionCandidateInput.date is mandatory; the workflow must
        # obtain the date or report it cannot stage, never substitute the current time.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "occurrence date" in lowered
        assert "mandatory" in lowered
        assert "current time" in lowered

    def test_first_person_not_staged_as_new_person(self) -> None:
        # Regression: resolve_person has no special handling for "I", so staging a
        # first-person reference would create a duplicate self person (candidates have
        # no is_self field); self participants must be omitted.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "first-person" in lowered
        assert "is_self" in lowered
        assert "omit the self" in lowered
        assert "not a new person" in lowered
        # Self as a fact/affiliation subject needs a person_ref bound to the self record.
        assert "fact or affiliation subject" in lowered
        assert "canonical name or any verified unique alias" in lowered
        assert "unique non-handle alias" in lowered

    def test_summary_fast_path_excludes_structured_records(self) -> None:
        # Regression: a request carrying an affiliation/fact/interaction must route the
        # structured content through staging, not fold it into a remember_person
        # summary (which would bypass review and hide the structured record).
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "precedence" in lowered
        assert "pure identity assertion" in lowered
        assert "bypass review" in lowered
        assert "engineer at acme" in lowered

    def test_resolves_referenced_people_before_staging(self) -> None:
        # Regression: the stager matches only exact normalized names/handles, so a
        # partial reference must be resolved to its canonical identity before staging
        # or it is committed as a duplicate person.
        body = _skill_path("remember").read_text(encoding="utf-8")
        lowered = body.lower()

        assert "resolve_person" in body
        assert "resolve every referenced person" in lowered
        assert "duplicate" in lowered
        # Resolution is introduced before the staging call.
        assert body.index("resolve_person") < body.index("stage_candidates")

    def test_reports_relationship_limitation_instead_of_mis_staging(self) -> None:
        # Regression: staging has no relationship candidate type, so a relationship
        # must be reported as unsupported, never forced into the staging schema.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "relationship" in lowered
        assert "fits neither" in lowered
        assert "not captured here" in lowered
        # It must not add a relationship write path (out of scope for this workflow).
        assert "set_relationship" not in lowered


class TestRemindersWorkflow:
    """``/people-context:reminders`` — resolve optional person, surface ambiguity."""

    def test_lists_all_when_no_person_given(self) -> None:
        lowered = _skill_path("reminders").read_text(encoding="utf-8").lower()

        assert "list_reminders" in lowered
        assert "no person" in lowered or "empty" in lowered

    def test_resolves_optional_person_before_filtering(self) -> None:
        body = _skill_path("reminders").read_text(encoding="utf-8")

        assert "resolve_person" in body
        assert "list_reminders" in body
        # Resolution precedes the filtered listing.
        assert body.index("resolve_person") < body.rindex("list_reminders")

    def test_surfaces_ambiguity_without_dropping_filter(self) -> None:
        lowered = _skill_path("reminders").read_text(encoding="utf-8").lower()

        assert "ambiguous" in lowered
        assert "silently drop" in lowered or "never silently" in lowered

    def test_gates_on_ambiguous_flag_not_candidate_count(self) -> None:
        # Regression mirroring /who: filter by the resolved top candidate on
        # `ambiguous: false`, never gate on candidate count.
        lowered = _skill_path("reminders").read_text(encoding="utf-8").lower()

        assert "not on candidate count" in lowered
        assert "candidates[0]" in lowered
