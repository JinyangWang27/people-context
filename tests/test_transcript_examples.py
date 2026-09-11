"""Structural checks on the transcript examples and the attribution rubric (M26.2).

These assert that two documents say what M26.2 requires them to say. They are not evidence that
any agent reviews a transcript well: a document is not a recording, and matching a phrase is not
judging an attribution decision. Extraction quality is assessed by a person against the rubric in
`docs/evals.md`, which is why the last tests here pin the documents' own admission of that — and
pin the separation M26.2 names, between a hand-authored batch surviving the real lifecycle and a
model reliably producing that batch from an export.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_PATH = REPOSITORY_ROOT / "docs/transcript-review-examples.md"
EVALS_PATH = REPOSITORY_ROOT / "docs/evals.md"
GALLERY_INDEX_PATH = REPOSITORY_ROOT / "docs/use-cases/README.md"
IMPORT_DOC_PATH = REPOSITORY_ROOT / "docs/import.md"
PLUGIN_DOC_PATH = REPOSITORY_ROOT / "docs/claude-code-plugin.md"
LIFECYCLE_TEST_PATH = REPOSITORY_ROOT / "tests/adapters/importers/test_transcript_capture_workflow.py"

#: Relative Markdown links, excluding external URLs and pure in-page anchors.
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\((?!https?://|#)([^)\s]+)\)")

#: The situations M26.2 requires the cases to cover, each with a phrase only that scenario's prose
#: contains. A renamed heading is fine; a dropped scenario is not.
REQUIRED_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("two labels, one person", "diarization artefact"),
    ("one label, two people", "room mic together"),
    ("a partial read", "Silence about coverage reads as completeness"),
    ("an ambiguous name", "`ambiguous` result"),
    ("an unknown event date", "offsets into the audio"),
    ("a task nobody accepted", "asking, not Dana agreeing"),
    ("an unsupported reminder candidate", "there is no candidate type for it"),
    ("a sensitive claim", "carry no sensitivity field at all"),
    ("recording-local identity", "fresh diarization pass"),
)

#: The six criteria the rubric names for qualitative assessment.
REQUIRED_CRITERIA: tuple[str, ...] = (
    "Attribution discipline",
    "Label handling",
    "Identity restraint",
    "Supported staging",
    "Sensitivity placement",
    "Honest reporting",
)

#: What the specification requires a reviewer to write down for each assessed run.
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


def test_the_examples_link_the_delivered_skill_they_demonstrate() -> None:
    assert "](../skills/transcript-review/SKILL.md)" in _read(EXAMPLES_PATH)


def test_the_gallery_links_the_examples() -> None:
    """M26.2 asks for the examples to be reachable from the gallery, not to become a sixth recipe."""
    gallery = _read(GALLERY_INDEX_PATH)

    assert "](../transcript-review-examples.md)" in gallery
    # The five recipes pair one-to-one with the evaluation tasks and stay at five.
    assert "so they stay at five" in gallery


def test_the_import_and_plugin_docs_point_at_the_delivered_examples() -> None:
    """M26.1 left both saying the worked cases were not shipped yet."""
    for path in (IMPORT_DOC_PATH, PLUGIN_DOC_PATH):
        text = _read(path)
        assert "](transcript-review-examples.md)" in text, path.name
        assert "are M26.2 and are not yet shipped" not in text, path.name


@pytest.mark.parametrize(("situation", "phrase"), REQUIRED_SCENARIOS)
def test_every_required_situation_is_covered(situation: str, phrase: str) -> None:
    assert phrase in _read(EXAMPLES_PATH), situation


def test_there_are_eight_numbered_scenarios() -> None:
    numbered = re.findall(r"^## (\d+)\. ", _read(EXAMPLES_PATH), re.MULTILINE)

    assert numbered == [str(index) for index in range(1, 9)], numbered


def test_every_scenario_says_what_it_declined_to_stage() -> None:
    """The subset left out is the part of a review worth assessing, so every case names it."""
    sections = re.split(r"^## \d+\. ", _read(EXAMPLES_PATH), flags=re.MULTILINE)[1:]

    assert len(sections) == 8
    for section in sections:
        heading = section.splitlines()[0]
        assert "**Not staged.**" in section, heading
        assert "**Lesson.**" in section, heading


def test_the_examples_are_marked_fictional_and_illustrative() -> None:
    text = _read(EXAMPLES_PATH)

    assert "**Everything here is invented.**" in text
    assert "**The replies are illustrative.**" in text
    assert "not a promise about wording" in text


def test_the_seven_candidate_types_are_named_and_no_eighth_is_implied() -> None:
    """A reminder is the type a transcript review is most tempted to want, and there is none."""
    text = _flowed(EXAMPLES_PATH)

    assert "`person`, `interaction`, `affiliation`, `fact`, `observation`, `trait`, and `relationship`" in text
    assert "a batch that names an eighth is refused whole" in text
    assert "ordinary `set_reminder`" in text


def test_client_retention_is_documented_separately_from_local_storage() -> None:
    """The two boundaries are different, and collapsing them is the privacy error to avoid."""
    text = _flowed(EXAMPLES_PATH)

    assert "**The local server** is given no transcript, so it parses none and stores none of its own accord" in text
    assert "**The client** is a separate trust boundary with its own rules." in text
    assert "the server keeping nothing says nothing about what the client kept" in text


def test_raw_text_exclusion_is_stated_as_workflow_discipline_not_a_schema_guarantee() -> None:
    """The prose fields would accept a pasted line; claiming otherwise sells an unenforced guarantee."""
    text = _flowed(EXAMPLES_PATH)

    assert "**Keeping raw lines out is discipline, not a schema constraint.**" in text
    assert "the server would accept it" in text
    assert "not a guarantee the database enforces" in text
    # And the same limit is stated where the checks are described.
    assert "it does not show that the server would refuse one, because it would not" in text


def test_a_month_only_start_is_shown_staying_in_claim_text() -> None:
    """`valid_from` is a date, so a month-only start can only be stored by inventing a day."""
    text = _flowed(EXAMPLES_PATH)

    assert "**The day is what made the affiliation possible.**" in text
    assert "day unknown" in text
    assert "never invents a January 1 or a month boundary to fill a date field" in text


def test_the_examples_name_the_lifecycle_checks_and_their_limit() -> None:
    """M26.2 requires the hand-authored batches and the agent's judgement to stay separated."""
    text = _flowed(EXAMPLES_PATH)

    assert "tests/adapters/importers/test_transcript_capture_workflow.py" in text
    assert "Every batch in that file is hand-authored" in text
    assert "It proves nothing about whether a model, handed a real export, would produce that batch" in text
    assert "not evidence that an agent followed them" in text


def test_the_lifecycle_checks_exist_and_declare_the_same_limit() -> None:
    """A document claiming the checks run is only as good as the file it names."""
    assert LIFECYCLE_TEST_PATH.is_file()
    source = _read(LIFECYCLE_TEST_PATH)

    assert "The batches here are hand-authored." in source
    assert "docs/transcript-review-examples.md" in source


def test_the_rubric_names_every_criterion() -> None:
    text = _read(EVALS_PATH)

    assert "## Human review of transcript attribution" in text
    for criterion in REQUIRED_CRITERIA:
        assert f"- **{criterion}.**" in text, criterion


def test_the_rubric_requires_the_scenario_output_and_reasoning_to_be_recorded() -> None:
    text = _read(EVALS_PATH)
    section = text.split("## Human review of transcript attribution", 1)[1].split("\n## ", 1)[0]

    for field in REQUIRED_REVIEW_RECORD_FIELDS:
        assert f"- **{field}**" in section, field


def test_the_rubric_is_qualitative_rather_than_another_harness_score() -> None:
    """A totalled rubric would be the new evaluation framework M26.2 rules out."""
    section = _read(EVALS_PATH).split("## Human review of transcript attribution", 1)[1].split("\n## ", 1)[0]

    assert "assessed separately and never totalled" in section
    assert "No transcript review is recorded in this repository yet." in section


def test_the_harness_disclaims_transcript_attribution() -> None:
    """The claims list is where a reader checks what a score does not cover."""
    text = _read(EVALS_PATH)

    assert "It is not a claim about transcript attribution." in text
    assert "[Human review of transcript attribution](#human-review-of-transcript-attribution)" in text
