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

PLUGIN_DOC_PATH = REPOSITORY_ROOT / "docs/claude-code-plugin.md"

#: What the specification requires a reviewer to write down for each assessed run.
REQUIRED_REVIEW_RECORD_FIELDS: tuple[str, ...] = ("Scenario", "Output", "Reviewer reasoning")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _scenario_sections() -> dict[str, str]:
    """The eight numbered scenarios, keyed by their number."""
    parts = re.split(r"^## (\d+)\. ", _read(EXAMPLES_PATH), flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def _blockquote_blocks(section: str) -> list[list[str]]:
    """Consecutive runs of quoted lines, which is how the document shows a message or a draft."""
    blocks: list[list[str]] = []
    in_block = False
    for line in section.splitlines():
        if line.startswith(">"):
            if not in_block:
                blocks.append([])
            blocks[-1].append(line)
            in_block = True
        else:
            in_block = False
    return blocks


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


def test_every_scenario_shows_the_agent_output_rather_than_describing_it() -> None:
    """Regression: one scenario ended at a disclaimer and said coaching followed, without showing it.

    A reviewer cannot score usefulness, voice, or boundaries against a sentence promising that a
    reply existed. Every scenario therefore quotes at least two blocks — what the user said, and
    what the agent produced.
    """
    thin = {
        number: len(_blockquote_blocks(section))
        for number, section in _scenario_sections().items()
        if len(_blockquote_blocks(section)) < 2
    }

    assert not thin, f"scenarios with no quoted agent output: {thin}"


def test_no_scenario_proposes_a_date_the_user_did_not_supply() -> None:
    """Regression: the deadline draft promised dates that appeared nowhere in the user's account.

    Proposing a delivery date is a commitment, and inventing one is what the workflow forbids. The
    scenario now asks for the estimate and says where both dates came from.
    """
    section = _scenario_sections()["1"]

    assert "\u8fd8\u9700\u8981\u591a\u4e45" in section, "the agent no longer asks for the user's own estimate"
    assert "Every concrete thing in the draft below now traces to something the user said" in section
    assert "no delivery\ndate the agent picked itself" in section


def test_a_compound_capture_request_stays_behind_one_review_gate() -> None:
    """Regression: splitting a two-statement request committed half of it outside the batch.

    `skills/remember/SKILL.md` keeps any request carrying several separate statements on the staged
    path precisely so the whole of it stays refusable.
    """
    section = _scenario_sections()["8"]

    assert "a request carrying several separate statements stays on the" in section
    assert "One gate over both clauses keeps the\nwhole request refusable." in section
    assert "rejecting a batch cannot undo a write that happened outside it" in section


def test_the_plugin_doc_names_the_slash_invocation_each_skill_actually_has() -> None:
    """Regression: the doc claimed neither bundled skill was a slash command.

    A skill is invocable by the user and by the model unless its frontmatter says otherwise, and
    `disable-model-invocation` removes the automatic path rather than the typed one. Neither
    bundled skill sets `user-invocable: false`, so both have a namespaced command.
    """
    text = _read(PLUGIN_DOC_PATH)

    assert "/people-context:communication-coach" in text
    assert "/people-context:people-context-usage" in text
    assert "It does\nnot affect slash availability" in text


def test_neither_bundled_skill_opts_out_of_user_invocation() -> None:
    """The claim above is only true while the frontmatter keeps it true."""
    for name in ("communication-coach", "people-context-usage"):
        frontmatter = (REPOSITORY_ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8").split("---")[1]

        assert "user-invocable" not in frontmatter, name
        assert "disable-model-invocation" not in frontmatter, name


def test_a_chinese_scenario_never_switches_the_user_into_english() -> None:
    """Regression: the clarifying question in scenario 1 was English under a Chinese prompt.

    The workflow requires matching the user's language, and a question about their own work is as
    much a part of the reply as the draft is. Every quoted block in a scenario headed (Chinese)
    must therefore carry Chinese.
    """
    offenders: dict[str, list[str]] = {}
    for number, section in _scenario_sections().items():
        if not section.splitlines()[0].endswith("(Chinese)"):
            continue
        english_only = ["\n".join(block) for block in _blockquote_blocks(section) if not _CJK.search("".join(block))]
        if english_only:
            offenders[number] = english_only

    assert not offenders, f"English-only quoted blocks in a Chinese scenario: {offenders}"


def test_no_scenario_concedes_terms_the_user_did_not_clear() -> None:
    """Regression: the contract draft told the counterparty everything else was acceptable.

    The user had cleared two terms. A blanket acceptance concedes every term they have not read,
    which is an invented concession even though it reads as cooperative.
    """
    section = _scenario_sections()["6"]

    assert "Everything else I can live with" not in section
    assert "rather than agreement on everything else" in section
    assert "no concession beyond\nthe two terms the user actually named" in section


def test_no_draft_commits_the_user_to_something_they_did_not_offer() -> None:
    """Regression: three drafts volunteered the user's time, a plan, or a date.

    An offer inside a draft is a commitment the moment the draft is sent. Each of these is now
    either left out with the reason stated, or left as a slot for the user to fill.
    """
    text = _read(EXAMPLES_PATH)

    assert "Happy to spend an hour walking whoever picks them up" not in text
    assert "Are you free either of the next two weekends?" not in text
    assert "腊月二十八回来" not in text
    assert "I've left out a handover offer" in text
    assert "[what you want to do about it — your call]" in text


def test_unreachable_or_bounded_context_is_not_reported_as_an_empty_store() -> None:
    """Regression: a server it could not reach was described as a store holding nothing.

    Failing to read is not the same as there being nothing to read, and a bounded result is the
    ordinary view rather than the whole record.
    """
    text = _read(EXAMPLES_PATH)

    assert "I have nothing stored about Sam" not in text
    assert "That is not the same as there being nothing there" in text
    assert "That is the ordinary view,\n> not everything there is about him." in text


def test_staging_is_described_as_a_persisted_write_behind_a_promotion_gate() -> None:
    """Regression: the batch was announced as "nothing written yet", with an offer to drop a row.

    `stage_candidates` persists both candidates at once; acceptance gates promotion into the
    durable records. `commit_import` skips a candidate left out of `accepted_ids` and no ordinary
    tool deletes it, so it stays pending rather than disappearing.
    """
    text = _read(EXAMPLES_PATH)

    assert "nothing written yet" not in text
    assert "what acceptance gates is promotion into her actual record, not whether" in " ".join(text.split())
    assert "anything reached the disk" in " ".join(text.split())
    assert "stay\n> in the batch as a pending candidate" in text


def test_a_staged_interaction_has_a_date_the_user_established() -> None:
    """`InteractionCandidateInput.date` is mandatory and the workflow forbids guessing it."""
    section = _scenario_sections()["8"]

    assert "occurrence date is mandatory and\nmust not be guessed" in section
    assert "When did the skip-level actually happen" in section


def test_a_named_person_is_resolved_before_being_role_played() -> None:
    """Regression: the rehearsal voiced a named person without resolving or reading her."""
    section = _scenario_sections()["7"]

    assert "resolve_person" in section
    assert "I'll play a generic skip-level rather than her" in section


def test_the_capture_batch_carries_the_person_row_its_references_require() -> None:
    """Regression: the batch was described as two rows.

    An interaction's participants and a fact's subject are batch-local refs that must resolve to a
    `person` candidate staged in the same call; `stage_candidates` raises `unknown person reference`
    otherwise. The batch is therefore three rows, not two.
    """
    section = _scenario_sections()["8"]

    assert "The batch is three rows rather than two" in section
    assert "refused as an unknown person reference" in section


def test_review_runs_before_any_offer_to_commit() -> None:
    """Regression: the agent offered to commit in the same breath as staging.

    `skills/people-context-usage/SKILL.md` forbids exactly that, and the accepted ids a commit needs
    come from the review the scenario had skipped.
    """
    section = _scenario_sections()["8"]

    assert "Staging is a proposal, not a commit, and the two never happen in one breath." in section
    assert "review_import" in section
    assert "Nothing commits until you name the rows you want." in section


def test_no_deletion_of_a_pending_candidate_is_offered() -> None:
    """Regression: the agent offered to get an unaccepted candidate "gone from the store".

    No ordinary operation deletes an individual staged candidate, so the offer could not be kept.
    """
    text = _read(EXAMPLES_PATH)

    assert "gone from the store" not in text
    assert "no ordinary tool removes it on its own" in text


def test_the_document_states_that_draft_details_come_from_the_user() -> None:
    """The provenance rule the scenarios are written to, stated where a reader meets it."""
    text = _read(EXAMPLES_PATH)

    assert "**Every detail in a draft comes from the scenario.**" in text
    assert "it is a false statement they would be making" in text
