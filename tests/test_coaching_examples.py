"""Structural checks on the coaching examples and the human-review rubric.

These assert that two documents say what M25.2 requires them to say. They are not evidence that
any agent coaches well: a document is not a transcript, and matching a phrase is not judging a
draft. Coaching quality is assessed by a person against the rubric in `docs/evals.md`, which is
why the last test here pins the documents' own admission of that.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_PATH = REPOSITORY_ROOT / "docs/communication-coaching-examples.md"
EVALS_PATH = REPOSITORY_ROOT / "docs/evals.md"
GALLERY_INDEX_PATH = REPOSITORY_ROOT / "docs/use-cases/README.md"

#: Relative Markdown links, excluding external URLs and pure in-page anchors.
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\((?!https?://|#)([^)\s]+)\)")

#: Any CJK ideograph, used to prove the bilingual requirement rather than assuming it.
_CJK = re.compile(r"[\u4e00-\u9fff]")

#: The eight situations M25.2 requires the examples to cover, each with a phrase only that
#: scenario's prose contains. A renamed heading is fine; a dropped scenario is not.
REQUIRED_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("workplace hierarchy", "manager"),
    ("disagreement or refusal", "this is a no rather than a not-yet"),
    ("friendship repair", "apology"),
    ("family boundaries", "boundary"),
    ("uncertain intent", "hidden intent"),
    ("missing context or identity", "ambiguous"),
    ("requested practice and debrief", "rehearsal"),
    ("optional capture", "commit_import"),
)

#: The six criteria the checklist names for qualitative assessment.
REQUIRED_CRITERIA: tuple[str, ...] = (
    "Practical usefulness",
    "Voice",
    "Grounded personalization",
    "Uncertainty",
    "Boundaries",
    "Transferable lesson",
)

#: What the specification requires a reviewer to write down for each assessed run.
REQUIRED_REVIEW_RECORD_FIELDS: tuple[str, ...] = ("Scenario", "Output", "Reviewer reasoning")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_examples_document_exists() -> None:
    assert EXAMPLES_PATH.is_file()


@pytest.mark.parametrize("relative", ["docs/communication-coaching-examples.md"])
def test_relative_links_resolve(relative: str) -> None:
    source = REPOSITORY_ROOT / relative
    targets = _MARKDOWN_LINK.findall(_read(source))

    assert targets, f"{relative} links nothing"
    for target in targets:
        resolved = (source.parent / target.split("#", 1)[0]).resolve()
        assert resolved.exists(), f"broken relative link in {relative}: {target}"


def test_the_gallery_links_the_examples() -> None:
    """The checklist asks for the examples to be reachable from the gallery, not to become a recipe."""
    assert "](../communication-coaching-examples.md)" in _read(GALLERY_INDEX_PATH)


def test_the_examples_link_the_delivered_skill_they_demonstrate() -> None:
    assert "](../skills/communication-coach/SKILL.md)" in _read(EXAMPLES_PATH)


@pytest.mark.parametrize(("situation", "phrase"), REQUIRED_SCENARIOS)
def test_every_required_situation_is_covered(situation: str, phrase: str) -> None:
    assert phrase.lower() in _read(EXAMPLES_PATH).lower(), situation


def test_there_are_eight_numbered_scenarios() -> None:
    numbered = re.findall(r"^## (\d+)\. ", _read(EXAMPLES_PATH), re.MULTILINE)

    assert numbered == [str(index) for index in range(1, 9)], numbered


def test_both_languages_appear_in_the_examples() -> None:
    """A document that says it is bilingual and holds no Chinese is not bilingual."""
    text = _read(EXAMPLES_PATH)

    assert len(_CJK.findall(text)) > 100, "the Chinese scenarios are missing or reduced to a gloss"
    assert "Illustrative draft:" in text


def test_the_drafts_are_quoted_rather_than_described() -> None:
    """The rubric grades usefulness and voice, which needs text on the page to grade."""
    chinese_quoted = [
        line for line in _read(EXAMPLES_PATH).splitlines() if line.startswith("> ") and _CJK.search(line)
    ]

    assert chinese_quoted, "no Chinese draft is quoted"


def test_the_examples_are_marked_fictional_and_illustrative() -> None:
    text = _read(EXAMPLES_PATH)

    assert "**Everything here is invented.**" in text
    assert "**The drafts are illustrative.**" in text
    assert "not a promise about wording" in text


def test_client_retention_is_documented_separately_from_local_storage() -> None:
    """The two boundaries are different, and collapsing them is the privacy error to avoid."""
    text = _read(EXAMPLES_PATH)

    assert "**The local server** stores no part of it by default." in text
    assert "**The client** is a separate trust boundary with its own rules." in text
    assert "the server keeping nothing says nothing about what the client kept" in text


def test_the_rubric_names_every_criterion() -> None:
    text = _read(EVALS_PATH)

    assert "## Human review of communication coaching" in text
    for criterion in REQUIRED_CRITERIA:
        assert f"- **{criterion}.**" in text, criterion


def test_the_rubric_requires_the_scenario_output_and_reasoning_to_be_recorded() -> None:
    text = _read(EVALS_PATH)

    for field in REQUIRED_REVIEW_RECORD_FIELDS:
        assert f"- **{field}**" in text, field


def test_the_rubric_is_qualitative_rather_than_another_harness_score() -> None:
    """A totalled rubric would be the new evaluation framework M25.2 rules out."""
    text = _read(EVALS_PATH)

    assert "assessed separately and never totalled" in text
    assert "A review is a paragraph per criterion, not a number." in text


def test_no_effectiveness_claim_is_published_without_a_recorded_review() -> None:
    text = _read(EVALS_PATH)

    assert "**No coaching review is recorded in this repository yet.**" in text
    assert "Nothing here claims the workflow is effective." in text


def test_the_documents_disclaim_the_evidence_these_very_checks_provide() -> None:
    """Regression: the honesty is the deliverable, so it is the thing most worth pinning.

    Stub runs, keyword scoring, and instruction-text assertions are each named in `docs/evals.md`
    as things that do not establish coaching quality — including this file by name, so a later
    reader cannot mistake a green test run for a measurement.
    """
    text = _read(EVALS_PATH)

    assert "**Stub runs.**" in text
    assert "**Keyword scoring.**" in text
    assert "**Instruction-text tests.**" in text
    assert "tests/test_coaching_examples.py" in text
    assert "is not evidence that an agent followed them" in text
