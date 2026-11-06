# Staged capture

Read this before calling `stage_candidates`, `amend_candidate`, `withdraw_candidates`, or `commit_import`, and before extracting from a transcript, note, or other unstructured source.

### The chat review loop: number it, correct it, then ask

A batch of ULIDs is not a review. Present the staged batch as a **numbered list**, one
short line per candidate, and keep the canonical id at the end of the line so the user
can still copy one. Then translate what the user says back into the two review verbs:

- "change the third one to Staff Engineer", "that date is a year out", "it is the Priya
  Sharma at Acme" → `amend_candidate` with just the fields that change.
- "drop the last two", "that one is wrong" → `withdraw_candidates`. A withdrawn
  candidate stays visible as `rejected`, so the user can see what was dropped.

Both return the revised batch. **Re-present it**, renumbered, and ask again.

**Possible duplicates.** A new-person row may carry `possible_matches`: existing people
who share its given name or a short form of it ("Alex Morgan" and a stored "Alex",
"Katherine Bennett" and a stored "Kate"). They are weak suggestions, never matches —
unrelated people share first names. Show them on that row and ask the user which applies:

- the same person → `amend_candidate` with `{"matched_person_id": "<that id>"}`;
- a different person → leave the row as is; committing it creates a new person;
- unsure → `withdraw_candidates`, or leave it out of the commit.

Never choose one yourself on the name alone.

**Email addresses are contact information, never names.** A person's `name` is what
people call them; their address goes in `aliases` as a `handle`, which is also what
identity matching and vCard export read. From a contact or tracker export (Linear,
a CRM, an address book) that lists `Jordan Lee <jordan@example.com>`, stage:

```json
{"type": "person", "ref": "p1", "name": "Jordan Lee",
 "aliases": [{"value": "jordan@example.com", "kind": "handle"}]}
```

When a source gives only an address, ask the user who it is rather than staging the
address as the name; staging, `remember_person`, and `remember` refuse a new person
named by an email (`name_is_email`). Looking someone up by their address is fine:
`resolve_person` and `remember` find the person who holds it as a handle. Do not store
an address as a fact either. A row from an older batch that still carries
`name_is_email: true` stays unresolved at commit until `name` is amended.

**Confirming an amendment is not acceptance of the batch.** "Yes, Staff Engineer is
right" approves one correction; it does not approve committing anything. Call
`commit_import` only when the user accepts the revised batch itself, and pass the
`batch_digest` from the review you showed them so a commit is refused if the batch
changed after they saw it.

### Use only the strict candidate vocabulary

`stage_candidates` accepts exactly nine candidate `type`s. Nothing else validates:

- `person` — `ref`, `name`; optional strict `aliases` (objects such as
  `{"value": "Al", "kind": "nickname"}`, never bare strings), `summary`, `message_id`, `date`.
- `interaction` — `summary`, `participant_refs` (batch-local person `ref`s), `date`;
  optional `channel`, `message_id`, `sensitivity`, `evidence_ref`.
- `affiliation` — `person_ref`, `org`, `role`; optional `valid_from`, `valid_to`,
  `confidence`, `stated_by`.
- `fact` — `person_ref`, `predicate`, `value`; optional `valid_from`, `valid_to`,
  `confidence`, `sensitivity`, `stated_by`.
- `observation` — `person_ref`, `text`; optional `observed_at`, `sensitivity`,
  `evidence_ref`. Omit `observed_at` when the source establishes no event time rather
  than guessing one.
- `trait` — `person_ref`, `category`, `value`, and — unlike a direct `record_trait`
  call — a **required** `evidence_note` and `confidence`; optional `evidence_refs` and
  `evidence_ids`.
- `relationship` — `from_ref`, `to_ref`, `relationship_type`; optional `confidence`.
- `group` — `ref`, `name`, `kind` (`class`, `cohort`, `team`, `department`, `club`,
  `household`, `community`, `other`); optional `organization_id`, `group_id`,
  `sensitivity`, `stated_by`.
- `group_membership` — `person_ref`, `group_ref`; optional `role` (`member`, `student`,
  `teacher`, `participant`, `leader`, `staff`, `other`), `valid_from`, `valid_to`,
  `temporal_basis`, `confidence`, `sensitivity`, `stated_by`. Requires `source_kind` on
  the request; see below.

`stated_by` on a `fact` or `affiliation` records **who asserted the claim**, in at most 256
characters — a person, a document, or a role. It is not `source`, which names the process that
wrote the row. Attribution is not verification: a claim someone makes about themselves stays a
fact whose value says so and whose `stated_by` names them, never an inferred `trait`. Omit it
when the attribution is unknown rather than guessing at a speaker.

References are **batch-local**: an `interaction`, `affiliation`, `fact`, `observation`,
`trait`, `relationship`, or `group_membership` points at a `person` candidate's `ref` within the
same `stage_candidates` call, and a `group_membership`'s `group_ref` points at a `group`
candidate's `ref` in that same call. Extract concise, structured field values only. Never copy raw
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

### Reviewing attribution when the source is a transcript

A transcript adds one problem before extraction can start: the speaker labels do not
identify people. Settle attribution in conversation first, then stage only what survived.

Keep four roles apart. **Participation** means they were in the conversation; the
**speaker** said this particular thing; the **subject** is who a claim is about; the
**commitment owner** accepted a task in their own words. Attendance establishes none of
the other three, and a task nobody answered is a suggestion rather than a promise.

Treat a label as an observation about one recording — never a name, an alias, identity
proof, or a person record. Two mismatches both occur and one mapping cannot fix both:
diarization can split one person across several labels, and a shared room microphone can
put several people under one. Assign a whole label only after the user confirms it is
homogeneous; otherwise confirm individual statements or a range, remembering that
confirming one statement resolves neither its label nor its neighbours. Labels never
transfer between recordings.

Bring the user concise paraphrases anchored to the timestamps the transcript already
carries, or to line ranges when it carries none, and never invent an event time. Ask
focused attribution questions only for claims worth keeping; identifying every speaker is
not a precondition for being useful. Resolve confirmed names with `resolve_person`, leave
`ambiguous` and lone `fuzzy` matches unresolved, and never create or merge a person to make
a speaker map fit. `stated_by` is for established attribution on a `fact` or `affiliation`
only, never for a guessed speaker and never for a label.

Stage a claim only when identity, attribution, dates, and sensitivity are all faithfully
representable; anything resting on an unknown speaker or owner waits, while supported
independent claims proceed. Confirming who spoke is not approval to commit what they said —
the `stage_candidates` → `review_import` → explicit acceptance → `commit_import` gate is
unchanged. A neutral interaction summary needs confirmed participation **and** an
established event date; with either missing, summarise conversationally rather than
inventing a participant or using the import time as the meeting time. There is no reminder
candidate type, so a follow-up stays in your reply or becomes an explicitly requested
`set_reminder`, never a candidate wearing another type's name.

Report what you staged and what you could not, and keep that report in the conversation.
Uncertain ownership, working speaker maps, review notes, and raw marker text belong in no
staged field, no person record, and no source receipt.

### Receipt fields and request limits

`source_kind` optionally records an import receipt for the batch. It is a machine category such as
`meeting_transcript`, at most 128 characters of letters, digits, `.`, `_`, `-`, or `/` — never a person, a
title, or a description; put any human wording in `label` instead. `label`, `content_digest`,
`extraction_fingerprint`, and `external_source_id` are refused without `source_kind`. If you can compute a
SHA-256 over the exact source artifact, pass it as `content_digest` (64 lowercase hex characters) so
re-importing that same source can be detected; without one, no duplicate detection is promised. Omit
`extraction_fingerprint` unless you have explicit, bounded configuration semantics for it. None of these fields
may carry source text.

`stated_by` is separate from `source`, which names the process that wrote the row, and from `source_kind`,
which receipts the artifact read. A trait cites at most 32 `evidence_refs` and `evidence_ids` combined, each
at most 256 characters, and its evidence must be about the trait's own person. A request using `observation`,
`trait`, or `relationship` is bounded to 500 candidates, a 128-character `source`, 1 MiB of candidate JSON,
and 8 KiB per string.
