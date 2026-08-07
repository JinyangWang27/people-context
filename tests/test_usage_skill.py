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
        # Preparation records nothing; capture stays an after-the-fact user-approved flow.
        assert "read-only" in lowered

    def test_teaches_strict_candidate_vocabulary(self) -> None:
        body = SKILL_PATH.read_text(encoding="utf-8")

        for candidate_type in ("`person`", "`interaction`", "`affiliation`", "`fact`"):
            assert candidate_type in body
        # Concise fields only; raw source text is never copied into candidates.
        lowered = body.lower()
        assert "raw" in lowered and "transcript" in lowered
        assert "batch-local" in lowered

    def test_treats_staging_as_proposal_and_never_auto_commits(self) -> None:
        body = SKILL_PATH.read_text(encoding="utf-8")

        assert "stage_candidates" in body
        assert "review_import" in body
        assert "commit_import" in body
        lowered = body.lower()
        # The commit step is an explicit, later, user-approved write — never automatic.
        assert "never call" in lowered and "commit_import" in body
        assert "automatically" in lowered

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
