"""Structural checks on the shared-connection examples and their rubric (M28.3).

These assert that two documents say what M28.3 requires them to say. They are not evidence that
any agent captures a shared context well: a document is not a conversation, and matching a phrase
is not judging whether a year should have been recorded. Capture quality is assessed by a person
against the rubric in `docs/evals.md`, which is why the last tests here pin the documents' own
admission of that — and pin the separation M28.3 names, between a hand-authored batch surviving
the real lifecycle and a model reliably producing that batch from a conversation.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_PATH = REPOSITORY_ROOT / "docs/shared-connections-examples.md"
EVALS_PATH = REPOSITORY_ROOT / "docs/evals.md"
GALLERY_INDEX_PATH = REPOSITORY_ROOT / "docs/use-cases/README.md"
IMPORT_DOC_PATH = REPOSITORY_ROOT / "docs/import.md"
SKILL_PATH = REPOSITORY_ROOT / "skills/people-context-usage/SKILL.md"

#: The lifecycle checks the examples name. A document claiming they run is only as good as these.
LIFECYCLE_TEST_PATHS: tuple[Path, ...] = (
    REPOSITORY_ROOT / "tests/adapters/importers/test_group_staging.py",
    REPOSITORY_ROOT / "tests/adapters/importers/test_group_commit.py",
    REPOSITORY_ROOT / "tests/adapters/sqlite/test_bootstrap_group_candidates.py",
)

#: Relative Markdown links, excluding external URLs and pure in-page anchors.
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\((?!https?://|#)([^)\s]+)\)")

#: The situations the M28 acceptance scenarios require, each with a phrase only that scenario's
#: prose contains. A renamed heading is fine; a dropped scenario is not.
REQUIRED_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("same class with proven overlap", "prove a common day and both people held the student role"),
    ("same school, different classes", "the class is a different group"),
    ("same group, dates unknown", "a guessed year would make later answers look more certain"),
    ("known disjoint periods", "Dana left in 2020 and Marcus arrived in 2022"),
    ("teacher and pupil roles", "a teacher and a pupil sharing a room aren't peers"),
    ("cross-company team", "creates no employment, no department membership"),
    ("confirmed cohort continuity", "not a generator for a row per academic year"),
    ("two relationships through a common person", "two separate relationships, not a shared group"),
)

#: The six criteria the rubric names for qualitative assessment.
REQUIRED_CRITERIA: tuple[str, ...] = (
    "Group identity restraint",
    "Temporal honesty",
    "No extrapolation",
    "Evidence separation",
    "Explicit lookup",
    "Honest negatives",
)

#: What a reviewer must write down for each assessed run.
REQUIRED_REVIEW_RECORD_FIELDS: tuple[str, ...] = ("Scenario", "Output", "Reviewer reasoning")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flowed(path: Path) -> str:
    """Text with quote markers and line wrapping removed, so assertions survive rewrapping."""
    return " ".join(line.lstrip("> ") for line in _read(path).splitlines()).replace("  ", " ")


def test_the_examples_document_exists() -> None:
    assert EXAMPLES_PATH.is_file()


def test_relative_links_resolve() -> None:
    targets = _MARKDOWN_LINK.findall(_read(EXAMPLES_PATH))

    assert targets, "the examples link nothing"
    for target in targets:
        resolved = (EXAMPLES_PATH.parent / target.split("#", 1)[0]).resolve()
        assert resolved.exists(), f"broken relative link: {target}"


def test_the_examples_link_the_guidance_they_demonstrate() -> None:
    """M28.3 ships guidance in the shared usage skill rather than a new skill of its own."""
    assert "](../skills/people-context-usage/SKILL.md)" in _read(EXAMPLES_PATH)


def test_the_gallery_links_the_examples_without_becoming_a_sixth_recipe() -> None:
    gallery = _read(GALLERY_INDEX_PATH)

    assert "](../shared-connections-examples.md)" in gallery
    # The five recipes pair one-to-one with the evaluation tasks and stay at five.
    assert "so they stay at five" in gallery


def test_the_import_doc_points_at_the_delivered_examples() -> None:
    assert "](shared-connections-examples.md)" in _read(IMPORT_DOC_PATH)


@pytest.mark.parametrize(("situation", "phrase"), REQUIRED_SCENARIOS)
def test_every_required_situation_is_covered(situation: str, phrase: str) -> None:
    assert phrase in _flowed(EXAMPLES_PATH), situation


def test_there_are_eight_numbered_scenarios() -> None:
    numbered = re.findall(r"^## (\d+)\. ", _read(EXAMPLES_PATH), re.MULTILINE)

    assert numbered == [str(index) for index in range(1, 9)], numbered


def test_every_scenario_says_what_it_declined_to_record() -> None:
    """What an agent declines to invent is the part worth assessing, so every case names it."""
    sections = re.split(r"^## \d+\. ", _read(EXAMPLES_PATH), flags=re.MULTILINE)[1:]

    assert len(sections) == 8
    for section in sections:
        heading = section.splitlines()[0]
        assert "**Not done.**" in section, heading
        assert "**Lesson.**" in section, heading


def test_the_scenarios_span_school_work_and_social_contexts() -> None:
    """M28.3 asks for all three, because the rules are the same and the vocabulary is not."""
    text = _flowed(EXAMPLES_PATH)

    assert "`kind: class`" in text or "kind: class" in text
    assert "`kind: team`" in text
    assert "`kind: community`" in text
    assert "`kind: cohort`" in text


def test_the_examples_are_marked_fictional_and_illustrative() -> None:
    text = _read(EXAMPLES_PATH)

    assert "**Everything here is invented.**" in text
    assert "**The replies are illustrative.**" in text
    assert "not a promise about wording" in text


def test_the_inference_boundary_is_stated_up_front() -> None:
    """A group is a context, and the document must not let that slide even once."""
    text = _flowed(EXAMPLES_PATH)

    assert "**A group is a context, not proof that its members know one another.**" in text
    assert "**Missing dates stay missing.**" in text
    assert "**No roster extrapolation.**" in text


def test_a_negative_lookup_is_never_reported_as_proof_of_no_connection() -> None:
    text = _flowed(EXAMPLES_PATH)

    assert "no supporting shared context was found in what the agent may see" in text
    assert 'Reporting it as "they don\'t know each other" would state something the store never checked' in text


def test_client_retention_is_documented_separately_from_local_storage() -> None:
    """The two boundaries are different, and collapsing them is the privacy error to avoid."""
    text = _flowed(EXAMPLES_PATH)

    assert "**The local server** stores the groups and memberships the user accepted" in text
    assert "**The client** is a separate trust boundary with its own rules." in text
    assert "what the server kept says nothing about what the client kept" in text


def test_the_read_is_documented_as_writing_nothing() -> None:
    """No materialized deduction is the reason corrections need no cleanup job."""
    text = _flowed(EXAMPLES_PATH)

    assert "it writes no durable record, no audit entry, and no changelog row" in text
    assert "there was never a stored deduction to clean up" in text


def test_the_examples_name_the_lifecycle_checks_and_their_limit() -> None:
    """M28.3 requires the hand-authored batches and the agent's judgement to stay separated."""
    text = _flowed(EXAMPLES_PATH)

    for path in LIFECYCLE_TEST_PATHS:
        assert str(path.relative_to(REPOSITORY_ROOT)) in text, path.name
    assert "they prove nothing about the judgement that chose them" in text


def test_the_named_lifecycle_checks_exist() -> None:
    for path in LIFECYCLE_TEST_PATHS:
        assert path.is_file(), path.name


def test_the_rubric_names_every_criterion() -> None:
    text = _read(EVALS_PATH)

    assert "## Human review of shared-context capture" in text
    for criterion in REQUIRED_CRITERIA:
        assert f"- **{criterion}.**" in text, criterion


def test_the_rubric_requires_the_scenario_output_and_reasoning_to_be_recorded() -> None:
    section = _read(EVALS_PATH).split("## Human review of shared-context capture", 1)[1].split("\n## ", 1)[0]

    for field in REQUIRED_REVIEW_RECORD_FIELDS:
        assert f"- **{field}**" in section, field


def test_the_rubric_is_qualitative_rather_than_another_harness_score() -> None:
    """A totalled rubric would be the new review framework M28.3 rules out."""
    section = _read(EVALS_PATH).split("## Human review of shared-context capture", 1)[1].split("\n## ", 1)[0]

    assert "assessed separately and never totalled" in section
    assert "No shared-context review is recorded in this repository yet." in section


def test_the_harness_disclaims_shared_context_capture() -> None:
    """The claims list is where a reader checks what a score does not cover."""
    text = _read(EVALS_PATH)

    assert "It is not a claim about shared-context capture." in text
    assert "[Human review of shared-context capture](#human-review-of-shared-context-capture)" in text


def test_the_guidance_requires_explicit_group_resolution_before_capture() -> None:
    """The one rule that cannot be recovered from afterwards: M28 offers no group merge."""
    text = _flowed(SKILL_PATH)

    assert "**Never guess a group.**" in text
    assert "there is no merge to undo a wrong one" in text
    assert "call `find_groups` first" in text


def test_the_guidance_forbids_filled_dates_and_extrapolated_rosters() -> None:
    text = _flowed(SKILL_PATH)

    assert "**Never fill in a date.**" in text
    assert 'Absent means unknown, not "still going"' in text
    assert "**Never extrapolate a roster or a year.**" in text
    assert "not a placement per grade" in text


def test_the_guidance_keeps_the_lookup_explicit_and_context_unexpanded() -> None:
    text = _flowed(SKILL_PATH)

    assert "without waiting for the user to name a tool" in text
    assert "Do not add derived classmates or colleagues to ordinary `get_person_context` results" in text
    assert "Nothing here writes anything." in text


def test_the_guidance_keeps_context_label_and_assertion_distinct() -> None:
    text = _flowed(SKILL_PATH)

    assert "It is not evidence they know each other, are friends, or ever met." in text
    assert "A teacher and a pupil of one class are not classmates." in text
    assert "Report `unknown` as unknown rather than as \"probably\"" in text
