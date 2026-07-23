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

    def test_non_unique_name_uses_handle_or_reports_limitation(self) -> None:
        # Regression: a staged person candidate has no id field, so a resolved
        # selection among duplicate normalized names cannot be represented; the
        # workflow must use a unique handle or report the limitation.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "no person" in lowered and "id field" in lowered
        assert "unique handle" in lowered
        assert "fail to commit" in lowered or "uncommittable" in lowered

    def test_direct_write_reports_duplicate_name_limitation(self) -> None:
        # Regression: remember_person has no person_id parameter, so a non-unique
        # canonical name cannot be targeted by re-picking; the direct write must use a
        # unique handle or report the limitation, mirroring the staging path.
        lowered = _skill_path("remember").read_text(encoding="utf-8").lower()

        assert "person_id" in lowered
        assert "identically-named" in lowered
        # Both the staging path and the direct write name the unique-handle escape hatch.
        assert lowered.count("unique handle") >= 2

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
