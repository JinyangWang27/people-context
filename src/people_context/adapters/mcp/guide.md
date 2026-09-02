# Using people-context

people-context is a local-first store of durable knowledge about the people in the
user's life: their names and aliases, how they relate to the user, their
organisations and roles, and relevant past interactions. These tools compose into a
few reliable patterns. Follow them instead of guessing.

## Resolve identity first

When the user names, nicknames, or partially references a person, call
`resolve_person` **before** reading context and before asking the user who they mean.

`resolve_person` returns an explainable result. When it reports an `ambiguous`
outcome with a candidate list, preserve that contract: present or narrow the
candidates and let the user choose. Never silently pick one candidate, and never
fabricate an identity the pipeline did not return.

Read `match_reason`, not just the score. A reason starting with `fuzzy` means no
stored name matches what was typed and the resolver fell back to spelling distance,
so a lone `fuzzy` candidate is a suggestion to confirm rather than an identification
— treat it like `ambiguous`. A matched hint appends `+hint:…` and raises the score,
so a high score alone does not make a near-spelling into a match. Use `search_people` for broader
browsing when the user is exploring rather than pointing at one person.

## Read context, then guidance

These answer two different questions:

- `get_person_context` answers **what is known** — a bounded, sensitivity-aware
  bundle of identity, relationships, affiliations, facts, and recent interactions.
  Its `truncated` flag says the item budget cut the list. Sensitive and restricted
  records leave no trace at all, by design: what you get back is the intended
  complete ordinary view, not a redacted one.
- `get_communication_guidance` answers **how to communicate** — tone and approach
  derived from the stored communication philosophy.

Resolve the person first, then call the tool that matches the question. When the user
wants help writing to or preparing for someone, `get_communication_guidance` is the
right tool; do not infer tone from raw context alone. Both reads, like `list_reminders`,
`get_person_timeline`, and `upcoming_dates`, also accept `person` (the name as said)
in place of `person_id`; a name that is ambiguous, or that only a near-spelling
matches, then returns candidates instead of data, so the resolution contract holds
either way. Surface those candidates and let the user choose, exactly as you would
for `resolve_person`.

## Preparing for a meeting or conversation

When the user is about to meet, call, or write to one or more people, build the brief
from resolved records rather than from memory or guesswork:

1. Resolve every attendee with `resolve_person`, one name at a time. When a name comes
   back `ambiguous`, present the candidates and let the user pick before you read
   anything; never guess which attendee was meant, and never invent one.
2. For each resolved person, call `get_person_context` for the bounded,
   sensitivity-aware view of who they are, how they relate to the user, and what
   happened recently.
3. Call `get_communication_guidance` for each of them too. Preparation always needs
   both reads: context says what is known, guidance says how to communicate, and the
   brief below promises the second. Do not skip it because the user did not use the
   word "tone", and do not infer tone from context alone.
4. Call `list_reminders` with that `person_id` to surface the open follow-ups and
   communication notes already recorded for them.
5. Compose one short brief per attendee: who they are, how they relate to the user,
   what happened last, open follow-ups, and how to communicate with them.

Preparation is a read-only flow. Do not stage or record anything the meeting has not
produced yet, and do not treat a thin brief as a reason to reach for elevated tools —
what `get_person_context` returns is the intended complete ordinary view. After the
meeting, the end-of-session capture rules below apply unchanged: propose with
`stage_candidates`, and leave the commit to the user.

## Capturing new knowledge: propose, review, then commit

There are two ways durable knowledge enters the store, and both keep the user in
control.

- An explicit, well-formed person assertion ("remember my colleague Dana Okafor,
  dana@example.com") fits `remember_person` directly.
- One durable thing the user **states directly** about one person — "Dana from Acme
  prefers short emails", "Bob is my manager", "I had coffee with Dana today" — fits
  `remember` in a single call: it resolves the name, creates the person only when
  nobody matches, and records the note, affiliation, or relationship in one audited
  transaction. It never guesses between similar names: `status: ambiguous` or
  `unconfirmed` returns candidates and records nothing, so ask and call again with the
  exact name. Set `sensitivity` to `sensitive` or `restricted` for private matters, as a
  `note`; an elevated level with `org` or `relationship` is refused, because those rows
  carry no sensitivity and every ordinary read discloses them.
- Everything extracted from notes, prior conversation, or other agent-visible text —
  facts, affiliations, interactions, and newly mentioned people — goes through the
  staged capture flow, never through a direct write.

The staged flow has three distinct steps. Keep them distinct:

1. `stage_candidates` is a **proposal**. It validates and atomically stages
   candidates for later review. It does not persist durable records.
2. `review_import` is **inspection**. It returns the staged candidates and their
   statuses for a batch so the user can see exactly what would be written.
3. `commit_import` is an **explicit, later write**. Call it only after the user has
   reviewed a batch and explicitly accepted specific candidates. Never call
   `commit_import` automatically, speculatively, or in the same breath as staging.

### Use only the strict candidate vocabulary

`stage_candidates` accepts exactly seven candidate `type`s. Nothing else validates:

- `person` — `ref`, `name`, and strict `aliases`; optional `summary`, `message_id`,
  `date`.
- `interaction` — `summary`, `participant_refs` (batch-local person `ref`s), `date`;
  optional `channel`, `message_id`, `sensitivity`, `evidence_ref`.
- `affiliation` — `person_ref`, `org`, `role`; optional `valid_from`, `valid_to`,
  `confidence`.
- `fact` — `person_ref`, `predicate`, `value`; optional `valid_from`, `valid_to`,
  `confidence`, `sensitivity`.
- `observation` — `person_ref`, `text`; optional `observed_at`, `sensitivity`,
  `evidence_ref`. Omit `observed_at` when the source establishes no event time rather
  than guessing one.
- `trait` — `person_ref`, `category`, `value`, and — unlike a direct `record_trait`
  call — a **required** `evidence_note` and `confidence`; optional `evidence_refs` and
  `evidence_ids`.
- `relationship` — `from_ref`, `to_ref`, `relationship_type`; optional `confidence`.

References are **batch-local**: an `interaction`, `affiliation`, `fact`, `observation`,
`trait`, or `relationship` points at a `person` candidate's `ref` within the same
`stage_candidates` call. Extract concise, structured field values only. Never copy raw
conversation, transcript, note, or email body text into any candidate field; summarise
it into the strict fields above.

### Extracting from a transcript, note, or other unstructured source

When the user points you at a meeting transcript, a call note, an interview, or a
conversation log, you are the semantic layer. people-context does not read prose: it
never parses, stores, or sees the source. You read it with your own file capability,
distil it, and stage only the distillation.

Keep three levels of knowledge distinct instead of flattening everything into facts:

- **fact** — something explicitly and durably asserted. "I joined Acme in January."
- **observation** — something that happened in *this* source. "Asked repeatedly for
  quantitative evidence before agreeing."
- **trait** — a generalisation you *inferred* from evidence. "Responds better to
  proposals supported by quantitative evidence."

The further a claim sits from what was explicitly said, the more it must carry. A trait
therefore requires an explicit `confidence` and a concise `evidence_note` — a short
derivation in your own words ("derived from the 24 Aug planning meeting"), never a
quoted passage. One observed behaviour is not a high-confidence personality claim.

Where the inference rests on records you are staging anyway, say so by id as well as in
words. Give each supporting `observation` or `interaction` an `evidence_ref` — any short
label of your own — and list those labels in the trait's `evidence_refs`; use
`evidence_ids` for records already in the store. Staging rewrites your labels to real
candidate ids, so you never need to know one. Three rules to work with rather than
around: `evidence_refs` needs a `source_kind` on the same request (that receipt is what
lets a later commit still resolve the citation); evidence must be about the trait's own
person (an observation about someone else, or an interaction they were not in, leaves
the trait uncommitted); and at most 32 references and ids combined. This does not replace `evidence_note`, and a trait drawn from material
that produced no durable record is still a perfectly good trait — cite nothing there
rather than inventing an observation to point at.

Relationships stated in the source ("Sarah manages Bob", "Alice is John's sister") are
`relationship` candidates rather than facts, so they reach the graph with its normal
vocabulary and inverse semantics. Stage a `person` candidate for **every** participant
you reference, even one who almost certainly already exists; matching will find them,
and an ambiguous identity is reported as ambiguous rather than silently becoming a new
duplicate person.

Do not extract:

- speculative health, political, religious, sexual, or demographic inference;
- psychiatric or personality diagnosis;
- gossip promoted to fact, or a passing mood promoted to temperament;
- relationships you are guessing at rather than reading;
- **any sensitive or restricted relationship**, even when the source states it plainly.
  Relationship candidates are ordinary-disclosure only, because the graph has no
  sensitivity field to enforce anything stronger. Leave such an edge out entirely
  rather than downgrading it. Sensitive information that *is* enforceable still belongs
  in a `fact`, `observation`, `trait`, or `interaction` at the right sensitivity.

Then stage, and stop. The user reviews and commits, exactly as below.

If you are working without MCP access and have only a shell, the same use case is a
command:

```bash
pctx import stage-candidates --source "2026-08-27 planning sync" --input candidates.json
pctx import stage-candidates --source "2026-08-27 planning sync" --input -   # or stdin
```

Its `--input` is candidate JSON — never the transcript. It stages only; `pctx import
review BATCH_ID` and `pctx import commit BATCH_ID --accept ID` are the same gate.

## Maintaining what is already stored: propose, wait, then write

Long-lived stores accumulate. The same fact gets recorded twice, a role changes, an
early weak inference sits beside a better-supported one. When the user asks you to
review, tidy, reconcile, or check what is stored about someone, work the review as a
read-only pass that ends in **proposals**, not writes:

1. Resolve one person with `resolve_person`. An `ambiguous` result stops the review —
   maintenance on the wrong identity is worse than none.
2. Read `get_person_context`, `get_person_timeline`, and `get_consolidation_context`.
   The timeline says what happened and when; consolidation context says what the store
   now holds and where it may say the same thing twice.
3. Read the `signals`. Each names two records that share a predicate or category and
   says how they stand: `duplicate_fact`, `restated_fact`, `contradictory_fact`,
   `succeeding_fact`, `duplicate_trait`, `divergent_trait`. They compare normalized
   values and validity periods only — they are evidence for your judgement, not a
   verdict, and a signal is never an instruction to write.
4. Explain each proposal in the user's terms: which records, which evidence and
   provenance support it, and what would change.
5. Propose structured actions by stable id — the tool and its arguments, never a shell
   command and never a name interpolated into text.
6. **Wait for explicit approval.** Do not call a mutation tool because a signal looked
   clear-cut, because the user asked you to "review" or "clean up", or because you
   proposed it in the same turn. Approval is per proposal, not a blanket yes.
7. After approved writes, re-read the person and report the resulting state.

### Correction and supersession are different events

This is the distinction the maintenance flow exists to get right:

- **`correct_record`** — the stored value was *wrong*. A typo, a misheard employer, a
  mis-keyed date. It updates the row in place.
- **`supersede_fact`** — the stored value was *right, and then the world changed*. Alice
  really did work at Acme; in July she moved to Globex.

Never propose changing a historically correct fact's `value` in place merely because a
newer value is now true — that erases the fact that the old value was ever the case.
`supersede_fact(fact_id, new_value, effective_from, confidence?, sensitivity?)` closes
the old row the day before `effective_from`, keeping its value and provenance, and opens
a replacement that **inherits the old assertion's original end date**: a claim bounded to
2026 stays bounded to 2026, and an open-ended one stays open-ended. Both rows commit
together or not at all. It cannot change the person, the predicate, or the replacement's
end date; anything else erroneous is a `correct_record`.

`effective_from` must be a real transition date inside the stretch the old fact still
held — after its `valid_from` and not after its `valid_to`. A refusal comes back with a
`reason`; report it and ask the user for the right date rather than nudging the date
until it is accepted.

### Redundant representation is not the same as multiple evidence

Three observations that each support one trait are three pieces of evidence, not two
duplicates and an original. `cited_by_trait_ids` on an observation and `evidence` on a
trait are there so you can tell the two apart. Do not propose collapsing them to reduce
row count, and do not raise a trait's `confidence` because more rows point at it —
confidence is judgement about the strength of evidence, not a count of it. A changed
confidence is a proposal like any other.

Where the source material genuinely conflicts, leave the conflict standing and say so.
Rewriting history to make the current state look tidy loses the thing the store is for.
Merging people is a proposal only when identity is independently established, never
because two records look similar.

## Disclosure gates are expected, not obstacles

The ordinary tool surface deliberately omits `get_sensitive_person_context` and
`export_data`. Their absence is a process-level privacy gate the operator controls,
not a bug to work around. Do not attempt to reach sensitive or restricted records, do
not suggest enabling those tools to get around a boundary, and treat what
`get_person_context` returns as the intended, complete ordinary view.

## Near the end of a session: review learnings, propose capture

When a session is naturally wrapping up, briefly review what you genuinely learned
about people during it — a durable fact, a role change, a meaningful interaction — and
consider proposing it with `stage_candidates` so the user can review it later.

This is a best-effort review, not a guaranteed mechanical step, and it stays inside
the same rules:

- propose with `stage_candidates` only; never call `commit_import`;
- stage concise structured candidates, never raw transcript text;
- skip it entirely when nothing durable was learned — an empty proposal is worse than
  none.
