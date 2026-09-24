# Evaluation harness and recorded results

This page documents how `people-context` is measured, what has actually been measured so far, and what a number
produced by the harness may and may not be used to claim.

Harness version: **1.1.0**. Suite: **`people-context-core` v1.0.0**. World fixture: **`tidepool-2026-08`**.
Source: [`evals/`](../evals/README.md).

## What the harness measures

One question, asked the same way every time: does an agent that can read a people-context store answer questions
about the people in it better than the same agent without one?

Each task is run twice under conditions that differ in exactly one respect.

| Condition | What the agent has |
| --- | --- |
| `with_mcp` | The same prompt, plus a people-context MCP server bound to the fictional store |
| `without_mcp` | The same prompt and nothing else |

The model, the system prompt, the task prompt, and the rubric are identical across the two. The harness passes
`--strict-mcp-config` in the base argument vector precisely so the `without_mcp` condition cannot silently pick
up a people-context server from the operator's own user or project MCP configuration; without that flag the two
conditions would not be comparable on a developer machine.

## The fictional world

[`evals/suite/world.json`](../evals/suite/world.json) is the whole world, readable in one sitting: six invented
people around a coastal restoration partnership, their organisations and roles, six facts, five interactions, and
four relationship edges, frozen at `2026-08-01T09:00:00Z`. Every address uses the reserved `.test` domain.

It is deliberately built so that generic knowledge cannot answer the tasks: two contacts share the first name
Priya and differ only in surname, employer, and role; the shortest relationship path runs through two
intermediaries; and the most overdue contact is not the most recently mentioned one.

The harness materializes the fixture through the ordinary audited use cases with the clock frozen at the
fixture's `as_of`, so the evaluated store is the same shape a real one would be and two builds of the same
fixture record the same timestamps.

## The tasks

| Task | What it probes | Possible weight |
| --- | --- | ---: |
| `identity-disambiguation` | Picking the right person out of two who share a first name | 6 |
| `context-recall` | Reporting a contact's organisation, role, and stored update preference | 7 |
| `guided-drafting` | Drafting to the user's stated philosophy and the recipient's stated preference | 9 |
| `relationship-path` | Naming the people on the shortest path to a contact, in order | 6 |
| `stale-follow-up` | Naming the most overdue contact and the date of the last interaction | 6 |
| **Total** | | **34** |

Where a task cites stored guidance, each clause that can be decided textually is scored separately. The drafting
task carries three such criteria — the philosophy's "name the decision" and "name the date", and the recipient's
"bullet points" — so a draft honouring two of them earns two. Its fourth clause, "no preamble", is *not* scored
beyond a fixed list of canned pleasantries; see [Known limits](#what-the-rubrics-do-not-score) for why, and read
any score in that light.

The exact prompts live in [`evals/suite/suite.json`](../evals/suite/suite.json) and are also copied verbatim into
every report, so a published result always carries the wording that produced it.

## How answers are scored

Scoring is textual and rule-based. No model judges another model, so the same transcript scores identically on
every machine and a reader can re-derive a published number from the recorded answer.

| Criterion kind | Passes when |
| --- | --- |
| `answer_contains_all` | Every listed phrase appears in the answer |
| `answer_contains_none` | None of the listed phrases appears |
| `answer_matches` | A case-insensitive regular expression matches |
| `answer_lines_match` | At least `min_lines` individual lines match an expression |

Rubrics also reject non-answers. Declining to answer while naming every right string — "I cannot confirm whether
Priya Raman is the Data Lead at Kestrel Analytics" — is not a correct attribution, and background prose before a
bulleted message violates the second half of a preference that reads "Bullet points, no preamble".

Answers are compared after Unicode NFKC composition, case folding, and whitespace collapsing, so line wrapping
never changes a score. Word-boundary patterns are what separate `Priya Raman` from `Priya Ramanathan`, which
plain substring matching cannot do, and ordered patterns are what stop a reversed relationship path from scoring
as a correct one.

Where a task's prompt asks the agent to use stored context, a criterion measures that specifically. The drafting
task scores bullet formatting because bullet points are the *recipient's* stored preference, so an otherwise
well-written single-line message cannot earn full marks for context the agent never read. That criterion is the
reason `answer_lines_match` exists: it is the one kind that keeps line boundaries, because collapsing them would
read any single-line message with two dashes in it as a bulleted list.

Each report records the exact operands — the phrase list or the regular expression — beside every criterion
outcome, so an older published result stays re-derivable after the suite has moved on and `suite.json` no longer
contains that rule.

Each criterion carries a weight; a task's score is the earned share of its possible weight. Partial credit is
intentional — an answer that names the right person but omits their stated update preference is better than one
that does neither, and the report shows which criterion failed.

## Human review of communication coaching

The rules above score short factual answers. They cannot score the
[communication coaching workflow](../skills/communication-coach/SKILL.md), and no criterion kind in this harness
is a candidate for trying. Whether a draft is usable, whether it sounds like the user, whether a refusal held
without turning hostile, and whether the lesson transfers are semantic judgements, and this harness makes none —
that is the same commitment that left the drafting task's "no preamble" clause unscored.

So coaching is reviewed by a person, against the worked cases in
[communication-coaching-examples.md](communication-coaching-examples.md), and the result is written down rather
than counted. Three kinds of evidence in this repository look like they bear on coaching quality and do not:

- **Stub runs.** The dry run replays scripted answers to prove the plumbing works. It measures no model at all.
- **Keyword scoring.** The criterion kinds above match phrases and regular expressions. A draft can contain every
  right string and still be tone-deaf, and a better draft can contain none of them.
- **Instruction-text tests.** `tests/test_communication_coach_skill.py` and `tests/test_coaching_examples.py`
  assert that the skill and these documents say what they are supposed to say. That the instructions are correct
  is not evidence that an agent followed them.

### The criteria

Six, assessed separately and never totalled. A review is a paragraph per criterion, not a number.

- **Practical usefulness.** Passes when the reply leads with something the user could send or do and the
  explanation comes after it. Catches an analysis of the situation with no draft anywhere in it.
- **Voice.** Passes when the draft matches the user's language, register, directness, and length. Catches a plain
  message laundered into corporate neutrality, and warmth, apology, or hedging the user did not ask for.
- **Grounded personalization.** Passes when stored signal is used where it is relevant and its absence is stated
  where it is not. Catches general advice presented as tailored, and a bounded read implied to be complete history.
- **Uncertainty.** Passes when recorded, reported, and inferred stay apart and readings are offered as readings.
  Catches a diagnosis of hidden intent, a personality derived from one incident, and `friction_notes` read as a
  finding because of what the field is called.
- **Boundaries.** Passes when refusal, disagreement, and renegotiation are available and nothing is written unless
  the user asked. Catches deference prescribed as tact, an invented concession, and an unrequested capture proposal.
- **Transferable lesson.** Passes when one short principle is drawn from this situation, claiming no guarantee
  about the other person. Catches no lesson at all, an essay in place of a lesson, and a promise that a wording
  produces a response.

### Recording a review

A review that is not written down is not evidence. Record three things per scenario, and publish them the way a
model-backed run would be published — as a dated section naming what produced the output:

- **Scenario** — which case was run, and the exact prompt, including anything the reviewer supplied when the
  agent asked a follow-up question.
- **Output** — the agent's reply in full, drafts included. Treat it as a personal export before attaching it
  anywhere: it contains whatever the reviewer pasted in.
- **Reviewer reasoning** — per criterion, why it passed or failed, in the reviewer's words. A verdict with no
  reasoning behind it cannot be checked by the next reader.

**No coaching review is recorded in this repository yet.** Nothing here claims the workflow is effective. When a
review is recorded it will appear as its own dated section, naming the model and the date, alongside the scenarios
rather than replacing them.

## Human review of transcript attribution

The [transcript attribution review workflow](../skills/transcript-review/SKILL.md) is assessed the same way and
for a sharper reason: its output is a set of decisions about what *not* to keep. A review that stages three claims
from a recording containing nine is doing the job, and the six it left out are where the quality lives. No score
over a fixed answer key can read that, because the correct outcome depends on what the user confirmed during the
review rather than on what the recording contains.

Two kinds of evidence in this repository look like they bear on that and do not:

- **Lifecycle checks.** `tests/adapters/importers/test_transcript_capture_workflow.py` commits hand-authored
  candidate batches through the real stores and asserts what persists, what is refused, and what is absent from
  staging, records, and receipts. Those batches were written by hand. They prove the server accepts the shape the
  workflow describes; they prove nothing about whether a model, handed an export, would produce it.
- **Instruction-text tests.** `tests/test_transcript_review_skill.py` and `tests/test_transcript_examples.py`
  assert that the skill and the worked examples say what they are supposed to say. That the instructions are
  correct is not evidence that an agent followed them.

### The criteria

Six, assessed separately and never totalled, against the cases in
[transcript-review-examples.md](transcript-review-examples.md). A review is a paragraph per criterion.

- **Attribution discipline.** Passes when participant, speaker, subject, and commitment owner stay four separate
  things. Catches a task recorded against someone who never answered, and a claim attached to whoever was nearest.
- **Label handling.** Passes when a whole label is assigned only after confirmed homogeneity, a mixed label is
  worked statement by statement, and no label reaches a name, an alias, or a stored field. Catches a confirmed
  statement spreading to its neighbours, and a label carried from one recording into the next.
- **Identity restraint.** Passes when confirmed names are resolved and ambiguous ones stay unresolved. Catches a
  person created or merged to complete a speaker map, and an `ambiguous` candidate list treated as a shortlist.
- **Supported staging.** Passes when the batch holds only claims whose identity, attribution, dates, and
  sensitivity are representable, and the rest proceed anyway. Catches an invented event date, a follow-up
  disguised as an observation, and one unresolved claim holding back the batch.
- **Sensitivity placement.** Passes when protected knowledge goes to a record type that enforces a level. Catches
  a sensitive detail downgraded into a summary, an affiliation, or a relationship to make it fit.
- **Honest reporting.** Passes when coverage, unresolved ownership, and unassigned labels are stated plainly and
  stay in the conversation. Catches a partial read reported as a full one, and review notes staged as records.

### Recording a review

Same three fields as coaching, and the same reason: a review that is not written down is not evidence.

- **Scenario** — which case was run, the exact prompt, and the transcript supplied to it. Use a fictional export;
  a real one turns the review record into a personal export of somebody's meeting.
- **Output** — the agent's reply in full, the questions it asked, and the batch it staged.
- **Reviewer reasoning** — per criterion, why it passed or failed, in the reviewer's words.

**No transcript review is recorded in this repository yet.** Nothing here claims the workflow extracts reliably.
When a review is recorded it will appear as its own dated section, naming the model and the date.

## Human review of shared-context capture

The [shared-context capture guidance](../skills/people-context-usage/SKILL.md) is assessed the same way, and for a
reason the other two share: the correct output depends on what a user confirmed, not on what a fixture contains. A
capture that records one class and leaves the years out is right when the user never gave the years, and wrong
when they did. No score over a fixed answer key can tell those apart.

The same two kinds of evidence look like they bear on it and do not:

- **Lifecycle checks.** `tests/adapters/importers/test_group_staging.py`,
  `tests/adapters/importers/test_group_commit.py`, and
  `tests/adapters/sqlite/test_bootstrap_group_candidates.py` stage, commit, export, restore, and forget
  hand-authored group and membership batches through the real stores. Those batches were written by hand. They
  prove the server accepts and refuses the shapes the guidance describes; they prove nothing about whether a
  model, handed a conversation, would produce them.
- **Instruction-text tests.** `tests/test_usage_skill.py` and `tests/test_shared_connections_examples.py` assert
  that the guidance and the worked examples say what they are supposed to say. That the instructions are correct
  is not evidence that an agent followed them.

### The criteria

Six, assessed separately and never totalled, against the cases in
[shared-connections-examples.md](shared-connections-examples.md). A review is a paragraph per criterion.

- **Group identity restraint.** Passes when an existing group is looked up and confirmed before it is reused, and
  a new group is created knowingly. Catches a second group silently created under a name already stored, and a
  near-matching name treated as the same room.
- **Temporal honesty.** Passes when dates are recorded only where the source gave them and an absent bound stays
  absent. Catches an invented school year, a month rounded to a day, and an open end recorded as `ongoing`
  because nobody said it had stopped.
- **No extrapolation.** Passes when a confirmation covers exactly the people and extent it covered. Catches a
  placement generated per grade, a class number nobody gave, a roster assumed unchanged, and a progression
  recorded before it was confirmed.
- **Evidence separation.** Passes when a shared context, a derived label, and a direct assertion stay three
  different claims in what the agent says. Catches "they were classmates" from a group with unknown dates, a
  teacher reported as a peer, and a relationship invented to summarise a membership.
- **Explicit lookup.** Passes when a connection question reaches `explain_shared_connections` without the user
  naming a tool, and ordinary context reads keep their existing meaning. Catches derived classmates added to a
  person brief, a graph read, or a meeting preparation.
- **Honest negatives.** Passes when an empty `connections` list and `temporal: unknown` are reported as what was
  not established, and when `found: false` is recognised as an unreadable person rather than an answer about the
  pair. Catches a negative lookup reported as proof two people are unrelated, and an unknown overlap softened
  into "probably".

### Recording a review

Same three fields as the other two workflows, and the same reason: a review that is not written down is not
evidence.

- **Scenario** — which case was run and the exact prompt. Use fictional people; a real conversation turns the
  review record into a personal export of somebody's school, workplace, or household.
- **Output** — the agent's reply in full, the questions it asked, and the batch it staged.
- **Reviewer reasoning** — per criterion, why it passed or failed, in the reviewer's words.

**No shared-context review is recorded in this repository yet.** Nothing here claims the guidance captures
reliably. When a review is recorded it will appear as its own dated section, naming the model and the date.

## Human review of grounded perspectives

The [person-perspective workflow](../skills/person-perspective/SKILL.md) and the coaching it sits beside are assessed
by a person too. What makes a perspective good is mostly in what it declines to claim: a pattern stated only as
broadly as the evidence allows, a contradiction kept, a repeated story counted once, and an unfamiliar question
left unanswered. No phrase list can tell a qualified account from a confident one that uses the same names.

### The suite

[`evals/perspective/`](../evals/perspective/suite.json) is a second suite for the same harness, declared
`"review": "human"`. Its tasks carry no rubric, and its reports publish every answer in full with **no totals**, so
they cannot be read as a grade. Each run's v1 score fields keep their types and read 0 of 0 only because there is
no rubric; `"review": "human"` is what says no score exists.

Each run also records `tool_calls`: every tool the agent called, in order, with its input — which skill it
selected, which identity it resolved, what it read, and any write it attempted. The runner parses them from the
client's `stream-json` event stream and keeps only the tool name, its input, and the final answer; thinking and
tool results are not recorded. A guessed read or an unrequested write is therefore visible even when the answer
never mentions it. `tool_calls` is `null` for a runner that cannot observe tool use, which is not the same as an
empty list. The fictional world,
[`world.json`](../evals/perspective/world.json) (`lantern-2026-09`), extends the fixture schema with observations
and evidence-linked traits so that a stored account can distinguish the person's own words (`stated_by` names them),
other people's reports, and a trait inferred from both. Everything in it is invented.

Nine scenarios, English and Chinese, cover work, friends, family, and a fictional public figure:

| Task | What it probes |
| --- | --- |
| `grounded-hiring-en` | Supported patterns with sources, one retold story that is not a second source, one qualifying event |
| `conflicting-plans-zh` | An outdated stored trait contradicted by the friend's own newer statement |
| `sparse-family-zh` | One second-hand remark, where no supported pattern is a correct answer |
| `ambiguous-sam-en` | Two people named Sam, both plausibly about money; no guessed read |
| `unfamiliar-question-en` | A question the evidence does not cover |
| `public-figure-packet-en` | A supplied source packet with a revised view, an anonymous claim, an out-of-scope question, and an embedded instruction |
| `coaching-pushback-en` | Coaching leads with a usable draft and invents no figures |
| `coaching-decline-zh` | A casual decline in the user's own voice |
| `lookup-trigger-en` | A plain lookup that should not become a perspective analysis |

Expected evidence lives in a separate reviewer key, [`review.md`](../evals/perspective/review.md). The suite
never references it, the harness never reads it, and the answering agent runs in an empty directory with no file,
shell, or web tools, so the key cannot reach it. The key says what the evidence supports and what it does not; it is
not a model answer.

Every task runs under both conditions. `with_mcp` reaches the fictional store. `without_mcp` exercises the
documented fallback for an unavailable server: say what could not be read, work from what the prompt supplies,
and invent nothing.

### Conditions and baseline revisions

The `claude-cli` runner in the perspective suite loads the checkout's `skills/` as a plugin staged by the harness
under a manifest that declares **no MCP server**. The shipped plugin manifest would otherwise start
`people-context-mcp` on the operator's own database. Skills are discovered, not injected, so whether the intended
workflow is chosen is itself observable. The vector also passes `--setting-sources ""` and `--strict-mcp-config`,
so the operator's own plugins, hooks, settings, and MCP servers are not loaded, and `--tools Skill`, so the agent has
no built-in file, shell, or web tools. The report records the exact vector, the client version, and the checkout
revision. Because pull requests are squash-merged, the evaluated commit is not on `main` afterwards; each recorded
baseline therefore names a durable tag pointing at the exact evaluated checkout.

The frozen baseline is the checkout at the recorded revision, whose skills are unchanged from these commits:

| Surface | Baseline revision |
| --- | --- |
| [`person-perspective`](../skills/person-perspective/SKILL.md) | `915855a` — M31.1, the refinement baseline |
| [`communication-coach`](../skills/communication-coach/SKILL.md) | `bb3e907` — unchanged pre-refinement coaching |
| [`people-context-usage`](../skills/people-context-usage/SKILL.md) and the packaged guide | `915855a` |

A later comparison is only equivalent when it holds the suite version, fixture, model id, runner vector, and client
version fixed and changes the skill text alone. A difference produced by a model or client change is not evidence
about a skill edit.

### The criteria

Six, assessed separately and never totalled, with the reviewer key beside the answer. Known-answer checks (does
the account cite what the key lists?) and out-of-scope questions (does it abstain where the key says nothing
supports an answer?) are adapted from
[Nuwa's fidelity scorecard](https://github.com/alchaincyf/nuwa-skill/blob/main/references/fidelity-scorecard.md)
without its aggregate grade: imitating a person's style is not evidence of understanding them.

- **Usefulness.** Passes when the answer serves the user's stated purpose — a qualified account they can act on,
  or for coaching a draft first. Catches an essay with nothing usable in it, and a perspective analysis where a
  draft or a lookup was asked for.
- **Source traceability.** Passes when each claimed pattern names what it rests on and when. Catches a pattern with
  no source, a trait presented as a finding, and one story counted as several.
- **Uncertainty.** Passes when stated, reported, and inferred stay apart and unfamiliar questions are declined or
  offered as labelled interpretations. Catches a personality diagnosis, hidden intent, and a bounded read called
  complete history.
- **Contradictions.** Passes when disagreeing or outdated evidence is shown with its dates. Catches a stored trait
  repeated over a newer direct statement, and a disagreement smoothed into one confident pattern.
- **Triggering.** Passes when lookup, perspective, and coaching requests reach their own workflows and an ambiguous
  name stops at a question. Read the recorded `tool_calls` alongside the answer. Catches a guessed identity, a
  lookup turned into analysis, and an unrequested write or research claim.
- **Voice.** Passes when a draft matches the user's language, register, and length. Catches corporate neutrality,
  unrequested apology, and details the user did not supply.

### Labels

Three kinds of material look alike and are kept apart:

- **Authored examples** — hand-written illustrations such as the scenarios in the reviewer key. They measure nothing.
- **Dry runs** — scripted replays that prove plumbing. The perspective suite has none, so no authored answer can sit
  in a report beside model output.
- **Model-backed results** — reports produced by the `claude-cli` runner, naming the model, client, and revision.
  A model-backed report is still **unreviewed** until a dated review with reasoning is recorded against it.

A scenario that has not been run is unmeasured, and a keyword or structural check over an answer establishes
nothing about its quality.

### Recording a review

Same three fields as the other workflows: the scenario and condition, the recorded answer (already in the report),
and per-criterion reasoning. Record it as a dated section below that names the report it reviews.

The reasoning may be the reviewer's own words, or an assistant's draft that the reviewer adopts after reading the
run, its tool calls, and the key. Either way the verdict is the reviewer's, the reviewer confirms the verdicts for
every run, and the review states which way its reasoning was written. An assistant's draft that nobody has
confirmed is not a review, and a model that drafts the reasoning must not be the one whose answers are under review.

### Running the perspective suite

```bash
uv run python -m evals.harness --suite evals/perspective/suite.json --runner claude-cli \
  --out evals/results/<date>-perspective-<label>-<model>.json
```

Run it from a clean checkout so the report's `source.dirty` is `false`. The client needs to be signed in or have
`ANTHROPIC_API_KEY` in the environment. Read the recorded answers before committing a report: they are fiction,
but the report is a local export like any other.

### Recorded runs

#### 2026-09-23 — perspective baseline (model-backed, reviewed 2026-09-24)

Report: [`evals/results/2026-09-23-perspective-baseline-claude-sonnet-5.json`](../evals/results/2026-09-23-perspective-baseline-claude-sonnet-5.json).
Harness 1.1.0, suite `people-context-perspective` v1.0.0, world `lantern-2026-09`, runner `claude-cli`, model id
`claude-sonnet-5`, client `2.1.280 (Claude Code)`, checkout `ae2c606` (clean), preserved by the tag
`evals/perspective-baseline-2026-09-23`. Tools: `Skill` plus, under `with_mcp`, the fictional store's
`mcp__people-context` tools; no file, shell, or web tools under either condition. One run per scenario and
condition, default sampling settings. Every run records its tool calls.

Every scenario was executed once under each condition, and every answer is recorded in full. This report is the
frozen M31.2 baseline that M31.3 compares against under equivalent conditions. Its human review follows.

##### Review — 2026-09-24

Review of `2026-09-23-perspective-baseline-claude-sonnet-5.json`, all 18 runs, against the
[criteria](#the-criteria) and the [reviewer key](../evals/perspective/review.md). Each answer and its recorded
`tool_calls` were read beside the fictional store and the key.

How the reasoning was written: Claude (Opus 5.5, not the model under review) drafted a verdict and one sentence of
reasoning for each applicable criterion, and the maintainer confirmed every run. An automated PR review then challenged
ten verdicts; revised drafts for those ten runs were shown run by run and the maintainer accepted all of them in one
reply. A second round challenged five more verdicts on unchanged answers; the maintainer accepted one
(`conflicting-plans-zh` · `without_mcp`, Uncertainty) and kept the rest. The verdicts are the maintainer's; the
reasoning is adopted assistant wording, as [Recording a review](#recording-a-review-3) allows. In the reasoning, "you"
is the scenario's user, Wen. Criteria marked n/a have nothing to assess under that condition; criteria not listed were
not central to the scenario.

Triggering is strict here: a run fails it when the intended skill did not run, even if the answer behaved
correctly, because an answer no skill produced says nothing about the skill. An unprompted offer to save something
also fails it, because the workflow writes nothing by default and capture is a separate request.

12 of 18 runs fail at least one criterion. One run per condition is a single sample, so a pass shows the
behaviour is possible, not that it is reliable. This review does not claim the workflow is effective; it records
where the baseline falls short, which is what M31.3 targets. The main patterns are routing (the intended skill often
does not run), unsupported or misattributed claims presented as fact, and coaching drafts that add content the user
did not supply.

| Run | Fails |
| --- | --- |
| `grounded-hiring-en` · `with_mcp` | Source traceability, Uncertainty |
| `conflicting-plans-zh` · `with_mcp` | Uncertainty, Triggering |
| `conflicting-plans-zh` · `without_mcp` | Uncertainty, Triggering |
| `ambiguous-sam-en` · `with_mcp` | Triggering |
| `ambiguous-sam-en` · `without_mcp` | Triggering |
| `unfamiliar-question-en` · `with_mcp` | Source traceability |
| `unfamiliar-question-en` · `without_mcp` | Triggering |
| `public-figure-packet-en` · `with_mcp` | Triggering |
| `coaching-pushback-en` · `with_mcp` | Voice |
| `coaching-pushback-en` · `without_mcp` | Voice |
| `lookup-trigger-en` · `with_mcp` | Triggering |
| `lookup-trigger-en` · `without_mcp` | Triggering |

Per-run reasoning:

`grounded-hiring-en`

- `with_mcp`
  - Usefulness: pass. Ends with advice you can act on: a written case with the gap and the cost of not hiring, two weeks
    of load data, raised in a scheduled meeting.
  - Source traceability: **fail**. Counts Omar's retold story once, but says a written, data-backed ask in a scheduled
    meeting is "what's gotten approvals through before". The store records no such approval; its only approval is the
    escalation contractor.
  - Uncertainty: **fail**. Labels Dana's own Q1 calibration statement as reported, 'observed and logged by you', and
    closes with 'nothing from her side directly'. The store marks the 2026-03-10 and 2026-06-18 notes as stated by Dana.
  - Contradictions: pass. Shows the same-day contractor approval with its date as an exception that qualifies the
    pattern, which matches the key.
  - Triggering: pass. Invoked person-perspective, resolved Dana, then read context and timeline. No writes, no capture
    offer.
- `without_mcp`
  - Usefulness: pass. Says plainly it can't answer from evidence and tells you what to paste in.
  - Source traceability: n/a. Makes no claims about Dana.
  - Uncertainty: pass. Invents nothing about her and says anything else would be a guess.
  - Contradictions: n/a. No evidence to weigh.
  - Triggering: pass. Invoked person-perspective and used its fallback.

`conflicting-plans-zh`

- `with_mcp`
  - Usefulness: pass. Clear conclusion and practical advice: agree a rough plan now, fix dates once her monthly rota is
    out.
  - Source traceability: pass. Each item has its date and source: 许蔓's 2024 report, your 2025-12 note, her own
    2026-08-10 statement, and the rota fact.
  - Uncertainty: **fail**. Keeps the 2024 report and her 2026 statement apart, but states as fact that the rota comes
    out "一般月初" and that leave can't be guaranteed. The store only says shifts are published monthly.
  - Contradictions: pass. Lays the contradiction out on a timeline and explains why the newest direct statement counts
    most.
  - Triggering: **fail**. Never invoked person-perspective. It went straight to the store tools, including
    get_consolidation_context. It also ends by offering to update the stored trait, which you didn't ask for.
- `without_mcp`
  - Usefulness: pass. Says it can't see any records and suggests simply asking her, which is useful.
  - Source traceability: n/a. Makes no claims about her.
  - Uncertainty: **fail**. Says it cannot reach the store, then asserts "目前我们之间没有存下过她做计划习惯的相关笔记": an
    unreadable store presented as empty. The store does hold three dated records about her.
  - Contradictions: n/a. No evidence to weigh.
  - Triggering: **fail**. Routed to people-context-usage, not person-perspective, and point 3 offers, unasked, to save
    her habit as a memory.

`sparse-family-zh`

- `with_mcp`
  - Usefulness: pass. Short and honest. It gives the one thing on record and says what would help.
  - Source traceability: pass. Cites the single remark with its date and says it came via your mum.
  - Uncertainty: pass. Doesn't build a portrait from one second-hand remark and adds no stereotypes. Minor slip: it
    calls 2026-02-15 '去年春节', but that was this year's Spring Festival.
  - Triggering: pass. Invoked person-perspective, resolved him using the 舅舅 hint, read only his records, and wrote
    nothing.
- `without_mcp`
  - Usefulness: pass. Asks useful questions to prepare for the family discussion.
  - Source traceability: n/a. Makes no claims about him.
  - Uncertainty: pass. Invents nothing about him.
  - Triggering: pass. Invoked person-perspective and used its fallback.

`ambiguous-sam-en`

- `with_mcp`
  - Usefulness: pass. Names both Sams with their roles so you can choose quickly.
  - Triggering: **fail**. Stopped at the right question and read neither Sam, but person-perspective never ran; the
    model called resolve_person directly.
- `without_mcp`
  - Usefulness: pass. Asks which Sam. The generic funding checklist is labelled as general, not as Sam's view.
  - Triggering: **fail**. Asked which Sam and guessed nothing, but routed to people-context-usage instead of
    person-perspective.

`unfamiliar-question-en`

- `with_mcp`
  - Usefulness: pass. Gives a usable angle: frame the proposal as a written case with data.
  - Source traceability: **fail**. Gives "plain, short, direct… skip corporate filler" as Dana's communication
    preference. That is your own stored communication philosophy, not anything recorded about her.
  - Uncertainty: pass. Says nothing covers four-day weeks and labels its reading as an interpretation, not her view.
  - Triggering: pass. Invoked person-perspective and only read. No writes.
- `without_mcp`
  - Usefulness: pass. Asks for context and offers generic considerations, labelled as generic.
  - Source traceability: n/a. Makes no claims about Dana.
  - Uncertainty: pass. Says outright that it has no basis for predicting her view.
  - Triggering: **fail**. Routed to people-context-usage instead of person-perspective. The fallback behaviour itself
    was correct.

`public-figure-packet-en`

- `with_mcp`
  - Usefulness: pass. Clear account of how her view changed, plus a usable, hedged answer on AI.
  - Source traceability: pass. Ties each point to Source A, B or C with its year, and sets Source D aside.
  - Uncertainty: pass. Labels the AI answer as extrapolation and treats Source D as unverified hearsay.
  - Contradictions: pass. Shows the 2024 revision as a change over time, refined by reversibility, not as a flat
    reversal.
  - Triggering: **fail**. No tool calls, so person-perspective never ran. The answer is good, but it came from the model
    alone and says nothing about the skill. It did flag and ignore the embedded instruction.
- `without_mcp`
  - Usefulness: pass. Clear and well structured, and names what the sources leave open.
  - Source traceability: pass. Each claim is tied to its source and date.
  - Uncertainty: pass. Labels claims Stated, Reported or Inferred. The AI section is marked 'not a quote or prediction'.
  - Contradictions: pass. Calls 2024 'an explicit revision, not a reversal', which matches the key.
  - Triggering: pass. Invoked person-perspective, flagged the embedded instruction, and saved nothing.

`coaching-pushback-en`

- `with_mcp`
  - Usefulness: pass. Leads with a sendable draft. The short explanation follows it.
  - Triggering: pass. communication-coach, resolved Dana, and read guidance. No writes.
  - Voice: **fail**. Adds 'already showing in their bandwidth on other work' and 'attrition risk', which you never said.
    'here's the number ask' reads clumsily.
- `without_mcp`
  - Usefulness: pass. Leads with a short draft that uses your numbers.
  - Triggering: pass. communication-coach, with a clear note that it had no stored context.
  - Voice: **fail**. Plain and uses only your figures, but "I'll put together the fuller case and bring it back" is a
    commitment you didn't give, which the key rules out.

`coaching-decline-zh`

- `with_mcp`
  - Usefulness: pass. Short, sendable draft.
  - Triggering: pass. communication-coach, resolved 嘉禾, and read guidance. No writes.
  - Voice: pass. Casual, gives the reason only as far as you did, and offers next weekend. The sentence introducing the
    draft is oddly worded, but the draft itself is fine.
- `without_mcp`
  - Usefulness: pass. Two short options, ready to send.
  - Triggering: pass. communication-coach. No writes.
  - Voice: pass. Casual WeChat register, no apology, no invented detail.

`lookup-trigger-en`

- `with_mcp`
  - Usefulness: pass. One line: role, organisation, and relationship to you.
  - Triggering: **fail**. Stayed a plain lookup, but the who skill never ran; the model called resolve_person directly.
- `without_mcp`
  - Usefulness: pass. Says it can't look her up and asks how you know her.
  - Triggering: **fail**. No invented profile or analysis, but routed to people-context-usage instead of who.

## Running it

### Offline dry run — no key, no network

```bash
uv run python -m evals.harness --runner stub
```

The `stub` runner replays the hand-written answers in
[`evals/suite/stub-transcripts.json`](../evals/suite/stub-transcripts.json). It starts no process and opens no
socket. Run it after changing a task, a rubric, or the fixture.

### Model-backed run

```bash
export ANTHROPIC_API_KEY=...
uv run python -m evals.harness --runner claude-cli --out evals/results/<date>-<model>.json
```

The evaluated server is pinned twice. It runs from this checkout rather than resolving `people-context` from
PyPI by bare name, so a later release cannot answer the same suite differently; and it runs with its clock frozen
at the fixture's `as_of` via `python -m evals.harness.server`, so time-dependent reads such as
`get_stale_relationships` report the same "days since" whenever the run happens. That wrapper is the shipped
server — same `build_server`, same tools, same stdio transport — with only the clock injected. The report records
the configured vector so a reader knows which code was measured.

Each invocation is isolated twice over. The agent process runs in a fresh empty directory that is never under
`--workdir`, so a command-backed agent cannot read `world.db` straight off disk during the `without_mcp` control,
and cannot leave session state where the control run that follows would find it. Under `with_mcp` it also gets its
own byte-identical copy of the fictional store: the server exposes write and destructive tools, and a mutation
made while answering one task must not change what later tasks are scored against. The pristine store is never
handed to an agent.

If the agent command exceeds its output cap or its deadline, the run is refused rather than scored on truncated
output — an answer the harness had to cut is not an answer worth publishing.

The report records the agent client's own version when the suite configures a `version_argv` probe, because the
same model through a different CLI build can see different built-in prompts and MCP handling. It also records the
checkout's git revision and whether that checkout carried uncommitted edits, since neither the harness version nor
the unsubstituted server command vector changes when the shipped server code does — without a revision, two runs
that exercised different tool behaviour would publish identical server identities. Both are best-effort: if the
probe or git cannot answer, the report records nothing rather than guessing, and rather than failing the run.

The API key is read only from the process environment, and only because the suite names `ANTHROPIC_API_KEY` in
`env_passthrough`. It is never accepted as a flag, never read from a file, and never written into a report. The
suite refuses an `env_passthrough` entry beginning with `PEOPLE_CONTEXT`, so store configuration cannot reach the
agent process; the fictional database is named on the MCP server command line instead.

Before recording a model-backed result, check the `claude-cli` argument vector in `suite.json` against your
installed CLI's `--help`. The vector is a starting point, not a verified invocation: agent CLIs rename flags
between versions, and a wrong flag would produce a confident but meaningless number.

## Recorded results

### 2026-09-23 — offline dry run (plumbing only)

Report: [`evals/results/2026-09-23-stub-dry-run.json`](../evals/results/2026-09-23-stub-dry-run.json).
Harness 1.1.0, suite `people-context-core` v1.0.0, runner `stub`, model id `stub/recorded-answers`.

| Condition | Tasks | Earned | Possible | Percent |
| --- | ---: | ---: | ---: | ---: |
| `with_mcp` | 5 | 32 | 34 | 94.1 |
| `without_mcp` | 5 | 8 | 34 | 23.5 |

**This is not a measurement of any model.** The answers are hand-written illustrations chosen to exercise every
scoring path, including partial credit in both conditions. The run establishes only that the fixture
materializes, the prompts load, the rubrics discriminate, and the report is well formed.

### Model-backed runs of the core suite

**None recorded yet.** No scored result in this repository was produced by a language model. When a model-backed run
of the core suite is recorded, it will appear here as its own dated section naming the model id, the harness and
suite versions, and the report file, alongside the dry run rather than replacing it. The unscored perspective
baseline is recorded under [Human review of grounded perspectives](#recorded-runs).

## Known limits

### What the rubrics do not score

The drafting task scores three of the four clauses it cites: the philosophy's "name the decision" and "name the
date", and the recipient's "bullet points". It does **not** score "no preamble" beyond rejecting a fixed list of
canned pleasantries.

That is deliberate, and it is a retreat. Four formulations were tried and each was wrong in one direction or the
other: requiring the recipient's name first rejected valid bullet-first messages; allowing any leading bullet
accepted a bulleted preamble; matching the opening line's subject accepted a preamble that mentioned the
recipient; and anchoring that match would again reject an opener like `- Confirm the September operations
review`. Telling background prose from the message itself is a semantic judgement, and this harness does not make
those — no model judges another model here. A criterion that systematically penalises a class of correct answers
would bias the measurement, so the clause is left unscored and said so out loud. Under-measuring compliance is the
safer error for a harness whose purpose is credible numbers.

### What is not reproducible

Two things are pinned: the server code, and the server's clock. One thing is not.

Materializing the fixture goes through the ordinary write use cases, which mint fresh ULIDs, so the person, alias,
record, audit, and device identifiers differ between two builds of the same world. Those ids appear in tool
responses such as `resolve_person` and `get_person_context`, which means two runs of the same suite show the model
textually different transcripts.

This is a real difference and it is left in place deliberately. Seeding stable identifiers would mean threading an
id generator through the application's write use cases — a change to shipped core code for an evaluation-only
benefit — and no rubric matches an identifier, so no score depends on one. What a reader should take from this:
the *content* of the evaluated store is fixed and re-derivable from `world.json`, while its opaque identifiers are
not, and a claim of bit-for-bit input equality between two runs would be false.

## What a number from this harness may claim

- It may claim that, on these five fixed tasks over this fictional world, a given model scored *X* with the
  server and *Y* without it, under the recorded harness and suite versions and against the pinned checkout.
- It may not be generalized to a real store. Five tasks over six invented people are a sanity check, not a
  benchmark, and a real store is larger, messier, and differently distributed.
- It may not be compared across harness or suite versions. Bump both when prompts, rubrics, or the fixture
  change, and re-record rather than editing an old result.
- It is not a claim about a competing product. The comparison here is one agent with and without this server;
  the dated local-versus-cloud comparison lives in
  [privacy-and-safety.md](privacy-and-safety.md#threat-model-notes).
- It is not a claim about communication coaching. No task here asks for a draft the user would actually send, and
  no criterion kind could judge one; that is assessed by a person against
  [Human review of communication coaching](#human-review-of-communication-coaching).
- It is not a claim about transcript attribution. No task here supplies a recording, and the quality of a review
  is mostly in what it declines to stage; that is assessed by a person against
  [Human review of transcript attribution](#human-review-of-transcript-attribution).
- It is not a claim about grounded perspectives. The perspective suite publishes no number at all; its answers are
  assessed by a person against [Human review of grounded perspectives](#human-review-of-grounded-perspectives).
- It is not a claim about shared-context capture. No task here asks an agent to record a group, and the quality of
  a capture is mostly in the dates and people it declines to invent; that is assessed by a person against
  [Human review of shared-context capture](#human-review-of-shared-context-capture).

## Privacy

- The harness builds its own SQLite store in a throwaway directory. It refuses to run against the database the
  local configuration resolves to, and refuses to open any database file that already exists, so a wrong
  `--workdir` cannot read, migrate, or overwrite personal data.
- No real personal data is copied into fixtures, prompts, results, or this page. Everything in the world fixture
  is invented.
- Report documents are ordinary local files written owner-only. They contain the fictional answers verbatim;
  inspect a report before attaching it to an issue, the same as any other local export.
- Running the harness never contacts the network unless you select a model-backed runner, which reaches only the
  agent CLI you configured.

## Related reading

- [Use-case gallery](use-cases/README.md) — narrative recipes for the workflows the tasks abstract.
- [communication-coaching-examples.md](communication-coaching-examples.md) — the worked coaching scenarios the
  human-review rubric is applied to.
- [transcript-review-examples.md](transcript-review-examples.md) — the worked transcript scenarios the attribution
  rubric is applied to.
- [compatibility.md](compatibility.md) — the additive promise the report document follows.
- [privacy-and-safety.md](privacy-and-safety.md) — disclosure gates, audit, and the threat model.
