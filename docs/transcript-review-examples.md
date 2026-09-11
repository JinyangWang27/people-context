# Transcript review examples

Eight worked scenarios for the attribution-review workflow delivered in
[`skills/transcript-review/SKILL.md`](../skills/transcript-review/SKILL.md) and mirrored into the shared usage
guidance served as `people-context://guide`. They exist so a human can judge whether the workflow extracts the
right subset of a recording, which is not something this repository's automated checks can decide — see
[Human review of transcript attribution](evals.md#human-review-of-transcript-attribution) for the rubric that goes
with these cases.

**Everything here is invented.** The people, the meetings, the recorder, the speaker labels, and the transcript
lines are fiction written for this document. No real recording, export, or conversation was used, quoted, or
paraphrased.

**People Context never reads the transcript.** There is no transcript parser, no source format for one, and no
field that holds a line of it. The agent reads the file with its own file capability, distils what the user
confirms, and stages that distillation through `stage_candidates` → `review_import` → `commit_import`, the same
lifecycle every other extraction uses. What reaches SQLite is the distillation and a non-content receipt.

**Partial is the normal outcome.** Every scenario below leaves something unresolved, and none of them treats that
as a failure. A transcript that yields three supported claims and six open questions has been reviewed correctly.
What would be a failure is a record that looks settled because the review had to produce something.

**The replies are illustrative.** They show the shape of a review that satisfies the workflow — attribution
settled in conversation, then a staged subset — not a promise about wording. A different model, or the same model
on a different day, will phrase these differently and still be right.

Tool names in `monospace` such as `resolve_person` are MCP tools the agent calls on the user's behalf; see
[mcp-interface.md](mcp-interface.md) for the inventory. Where a name appears in a non-Latin script it is given
natively with a romanization on first use, following [identity-resolution.md](identity-resolution.md).

## The recording these scenarios share

A fictional 50-minute planning call at a fictional company, exported by a recorder that numbers its speakers. The
export has timestamps and four labels, and the labels are all it knows:

> [00:01:02] Speaker 1: I've been staff engineer here since June, so I've watched this one the whole way.
> [00:03:10] Speaker 1: the Halden tier is the whole question. I'd rather sequence the migration behind it.
> [00:14:32] Speaker 2: honestly we should just drop the Halden tier and eat the churn.
> [00:14:41] Speaker 2: I can have the pricing writeup done by the end of next week.
> [00:22:05] Speaker 3: like I said, I'm off the afternoon rotation until the migration ships.
> [00:22:20] Speaker 3: I'm sorting out care for my father this quarter, so afternoons are hard.
> [00:31:18] Speaker 1: Dana, can you take the migration doc?
> [00:44:02] Speaker 4: — [inaudible] —

Four labels are not four people, and the document does not know how many people there are. Working that out is the
review.

## 1. Two labels, one person

**Situation.** The user wants what the call says about the people in it.

> Here's the export from Friday's planning call. Pull out anything worth keeping about who was in it.

The agent reads the file, then brings back paraphrases anchored to the timestamps the export already carries. Two
of them look like the same voice:

> Two things at 00:03:10 and 00:22:05 read like one person to me — sequencing the migration behind the Halden
> tier, and being off the afternoon rotation until it ships. The recorder has them as Speaker 1 and Speaker 3.
> Is that one person, or two people who agree?

> That's both Priya. The recorder splits her every time she dials back in.

Now the claims join up. The agent calls `resolve_person` for Priya Raghunathan, gets a single confident match, and
stages the role she stated at 00:01:02 and the rotation she stated at 00:22:05 against that one person — one from
each label, both attributed to her because she asserted them herself on the record.

**Staged.** An `affiliation` for the staff-engineer role, dated from June because she said June, and a `fact` for
the rotation, each with `stated_by` naming her as the source. `stated_by` exists on those two candidate types and
nowhere else, and it records who asserted the claim — not that the claim is true.

**Not staged.** No alias. "Speaker 1" and "Speaker 3" are observations about one recorder's output, not names
Priya answers to, and adding either as an alias would make a future import match on a label. No second person
record either: the split was a diarization artefact, and merging two people afterwards is worse than never
creating the duplicate.

**Lesson.** A confirmed split collapses into one person and leaves no trace of having been split. The mapping was
review scaffolding; it does not survive the review.

## 2. One label, two people

**Situation.** Speaker 2 says two things seven seconds apart, and they do not sound like the same person.

> At 00:14:32 someone wants to drop the Halden tier entirely, and at 00:14:41 someone commits to a pricing
> writeup by the end of next week. Both are Speaker 2. Was that one person?

> No — Marcus and Tomas were on the room mic together.

That answer resolves nothing on its own, and the agent says so rather than picking:

> Then I can't assign Speaker 2 as a block. Which of them took the pricing writeup at 00:14:41?

> That was Marcus.

> Got it. I'll leave 00:14:32 unattributed — confirming one line doesn't tell me who said the one before it.

**Staged.** An `observation` on Marcus that he took the pricing writeup and named a date. That is what happened on
this call, which is what an observation is for.

**Not staged.** The churn remark at 00:14:32. It is an opinion with no owner, and there is no candidate type for a
statement whose speaker is unknown. It is not staged against Marcus because he was nearby, not staged against
Tomas because he might have said it, and not staged as an observation about "the room".

No trait either, in either direction. "Attaches a date when he takes something on" would be one call generalised
into a temperament, and "argues for cutting scope" would be a temperament derived from a sentence nobody owns — a
`trait` needs a person, and unattributed speech has none. Staging refuses the second outright: a candidate whose
`person_ref` names nobody in the batch is rejected before review.

**Lesson.** A shared microphone cannot be assigned whole in either direction. Confirmation is per statement or per
range the user actually confirms, and it does not spread to its neighbours.

## 3. A partial read, reported as partial

**Situation.** The export is long and the user asked for one section of it.

> Just the pricing part — roughly the first twenty minutes.

The agent reads to 00:20:00 and stops, and its report says where it stopped:

> I covered 00:00 to 00:20:00 as asked. The migration-doc discussion at 00:31 and everything after it I haven't
> read, so I can't tell you whether anything there changes what's below.

The same sentence is written for a file that would not open, a truncated export, or a section that turned out to
be unreadable. Silence about coverage reads as completeness, and a user who believes a transcript was fully
reviewed will not go back to it.

**Staged.** Only claims from the range actually read.

**Not staged.** Nothing from the unread section, and no summary phrased as though it covered the call. An
`interaction` summarising "the planning call" after reading forty percent of it is a claim about the whole
conversation.

**Lesson.** Coverage is part of the result, not a caveat to append if asked.

## 4. A name two people answer to

**Situation.** Someone is referred to by first name only.

> Sam said he's moving to platform after the migration. Keep that.

The agent calls `resolve_person` for "Sam" and gets an `ambiguous` result: two people carry that nickname —
Sam Ekwueme and 魏晓 (Wei Xiao), who goes by Sam at work. The tool returned a candidate list, which is a question,
not a shortlist to choose from:

> Two people in your store answer to Sam — Sam Ekwueme and 魏晓. Which one was on this call? I'd rather ask than
> attach a team change to the wrong person.

If the user knows, the claim proceeds against the person they name. If they do not, it stays in the conversation.

**Staged.** Nothing, until the user says which Sam. Other supported claims from the same recording still proceed:
one unresolved identity does not hold the rest of the batch.

**Not staged.** A new person called "Sam". Creating one to hold the claim would turn a question into a duplicate,
and the next review would inherit three people where there are two.

**Lesson.** `ambiguous` is a question with a list attached. The list exists so the user can answer it, not so the
agent can pick from it.

## 5. A recording with no date

**Situation.** The export has timestamps within the recording and nothing that says which day it was.

> No idea when this one was. Sometime last month?

Timestamps like 00:14:32 are offsets into the audio, not a date, and a filename or a modification time is a guess
about the same thing. So the agent asks, and when the answer is genuinely unknown it splits the claims in two:

> Without a date I can't stage the call itself — an interaction has to carry when it happened, and using today's
> date would record the meeting as happening the moment I imported it. What Priya said about her rotation doesn't
> depend on the date, so that one I can still keep. Here's a summary of the call for now, in the conversation.

**Staged.** The date-independent claims.

**Not staged.** The `interaction`. It requires an event date, and staging is what refuses it — a candidate without
one does not reach review. Nothing invents a date to satisfy the field.

**Lesson.** An unknown date removes one record type from the outcome, not the whole review.

## 6. A task nobody accepted

**Situation.** At 00:31:18, Priya asks Dana to take the migration doc. Dana's only appearance in the export is
inaudible at 00:44:02.

> Dana was there. Put down that she's doing the migration doc.

> You've confirmed she was in the room, so she goes in the participant list. But the export has her saying nothing
> except an inaudible stretch at 00:44, and there's no answer to the migration-doc question anywhere in it. So what
> I've got is Priya asking, not Dana agreeing. Do you know whether she took it?

> I'll check with her.

**Staged.** Dana as a participant on the `interaction`, which is the only thing her attendance supports — and note
that the attendance itself came from the user, not from the recording: a label that never says a name establishes
nobody.

**Not staged.** Any promise. Nothing on Dana's record says she owns the migration doc, and no `observation`
launders the question into a commitment. Attendance is not speaking, speaking is not being the subject, and being
asked is not accepting.

**And no reminder.** "Chase Dana next week" is a sensible thing to do and there is no candidate type for it. The
seven are `person`, `interaction`, `affiliation`, `fact`, `observation`, `trait`, and `relationship`; a batch that
names an eighth is refused whole, which is the behaviour that keeps a follow-up from being disguised as an
observation. If the user asks for the reminder specifically, and the person and the date are settled, it is an
ordinary `set_reminder` — a separate tool, asked for separately.

**Lesson.** The four roles — participant, speaker, subject, commitment owner — are four different records or
none, and a question on the transcript establishes only the first.

## 7. Something private, said in passing

**Situation.** At 00:22:20 Priya mentions she is arranging care for a parent this quarter. It explains the
rotation change and it is not planning-call material.

> That's worth knowing so I don't schedule her into afternoons. But it's not something I want showing up when I
> pull her up in front of someone.

**Staged.** A `fact` at `sensitive`, which is withheld from the ordinary context read and returned only through
the elevated one the operator has to enable in the server's own environment.

**Not staged.** The same detail anywhere it could not be protected. A person `summary`, an `affiliation`, and a
`relationship` carry no sensitivity field at all, so a caring-for-a-parent relationship is not staged at a lower
level — it is left out, and the protected `fact` carries the knowledge instead. Downgrading it to make the graph
tidier is the leak.

**Lesson.** Sensitivity is enforceable only where the record type enforces it. Choosing a record type is choosing
who can see the claim.

## 8. The next recording in the series

**Situation.** A week later, the same recorder, the same weekly call.

> Follow-up call. Speaker 1 again, same meeting series.

> Same series, but a new export — "Speaker 1" here is a fresh label from a fresh diarization pass. Last week's
> Speaker 1 was Priya; this one might be anyone who happened to talk first. Who is it?

> Oh — that's Ines this time.

**Staged.** Claims against Ines Duarte, resolved by name.

**Not staged.** Anything that assumes continuity. The store holds no speaker map from last week to apply, by
design: the mapping lived in that conversation and ended with it. Re-confirming a label each time is cheap; a
silently reused map writes one person's statements onto another's record.

**Lesson.** A label indexes one recording's audio streams. Two exports of the same meeting series share a
numbering scheme and nothing else.

## What the checks cover, and what they do not

`tests/adapters/importers/test_transcript_capture_workflow.py` runs a fictional version of this call through the
real stores: a confirmed split commits one person, a mixed label contributes only its confirmed statement, an
undated call stages no interaction, an ambiguous name commits nothing, a trait with no owner is refused, a partial
acceptance commits exactly the accepted subset, a sensitive fact is withheld from the ordinary read, and no marker
text, speaker label, or working map reaches a staged row, a committed record, or a receipt.

Every batch in that file is hand-authored. It proves the server accepts the batch the workflow describes and
refuses the ones it forbids. It proves nothing about whether a model, handed a real export, would produce that
batch — the decisions above are the actual subject, and they are assessed by a person against the rubric in
[evals.md](evals.md#human-review-of-transcript-attribution).

The instruction-text tests are the same kind of evidence. `tests/test_transcript_review_skill.py` and
`tests/test_transcript_examples.py` assert that the skill and this document say what they are supposed to say.
That the instructions are correct is not evidence that an agent followed them.

## Where this material goes

Two retention boundaries apply to a transcript review, and they are not the same boundary.

**The local server** stores no part of the recording. It is given no transcript, so it parses none, and it has no
field that could hold a line of one: what lands in SQLite is the distilled candidates the user accepted, plus a
receipt that records the processing with a content digest the agent computed and a non-content source label.
Speaker labels, working maps, unresolved ownership, and the review conversation itself reach none of it — not
staging metadata, not a person `summary`, not an `observation`, not the audit log, which covers mutations rather
than disclosures.

**The client** is a separate trust boundary with its own rules. When the agent runs in a cloud-hosted assistant,
the whole review reaches that provider as ordinary prompt content: the transcript the agent read, every paraphrase
it quoted back, and whatever the tool calls returned. The provider's retention terms govern all of it, and this
repository's guarantees do not reach that far. Do not describe a transcript or a review as ephemeral or local-only
because the server kept nothing — the server keeping nothing says nothing about what the client kept.

The practical consequence: what a user is willing to hand to a review is a decision about their client, not about
this server. See [privacy-and-safety.md](privacy-and-safety.md) for the disclosure model and
[use-cases/README.md](use-cases/README.md) for the same split stated for the CLI and agent recipes.

## Related reading

- [Human review of transcript attribution](evals.md#human-review-of-transcript-attribution) — the rubric these
  cases are written to be assessed against, and why no automated check in this repository grades them.
- [import.md](import.md#transcript-attribution-review-m261) — the delivered workflow and the lifecycle these
  candidates go through.
- [claude-code-plugin.md](claude-code-plugin.md) — how the transcript-review skill is discovered and invoked.
- [communication-coaching-examples.md](communication-coaching-examples.md) — the other workflow assessed this way.
