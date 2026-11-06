# Maintaining stored records

Read this before correcting, superseding, consolidating, or reviewing a newer CV against what is already stored.

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

Aliases are names, not records, so `correct_record` does not reach them. A wrong alias —
a misspelt nickname, a former name filed as a nickname — is fixed with
`update_alias(person_id, value, new_value?, kind?, lang?, script?)` or dropped with
`remove_alias(person_id, value)`. An email address is an alias only as a `handle`, which is
what identity matching reads; one stored with another kind is reported by `pctx doctor` as
`email_alias_not_handle`, and the fix is `update_alias` with `kind: "handle"`. Both match `value` by its normalized form and leave the
person and every other record intact; neither touches the canonical name, which is a rename.

A wrong canonical name is fixed with `rename_person(person_id, name)`. The old name stays
on as a `former_name` alias, or as a `handle` when it is an email address, so a person
stored as `contact@example.com` becomes "Jordan Lee" without losing the address. `pctx
doctor` reports such people as `email_canonical_name`, and addresses stored only as facts
as `email_fact`; for those, `add_alias` with `kind: "handle"` makes the address count.

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

### Reviewing a newer CV against what is stored

A second CV is more evidence about someone, not a replacement for what you already hold. The
difference shows up exactly where the two documents disagree, and the tidy-looking move there —
overwrite the old job title, close the role the newer document no longer mentions — destroys
knowledge that was correct when it was recorded and that nothing afterwards can recover.

1. **Resolve one identity.** An ambiguous name, or identity evidence that does not hang together,
   ends the review there. Do not create a replacement person, and do not merge two people in order
   to have somewhere to put the document.
2. **Read the document, then read the store.** Distil claims under the capture rules in `documents.md`, then
   read `get_person_context`, `get_person_timeline`, and `get_consolidation_context` — the last of
   which carries the stored affiliations a CV's employment and education sections have to be
   compared against. Say which sections you could not read, which evidence you could not see, and
   which collections came back truncated. Those are limits of the comparison. Follow-up reads help,
   but a bounded read never promises complete history, and a page that stopped is never evidence
   that a record is absent.
3. **Give every proposal exactly one outcome.** One incoming claim can yield more than one
   proposal — a new dated role is an *Add* while the question of whether the old role ended stays
   *Leave unresolved* — and an outcome can change when fresh evidence arrives, which is what
   reopening an original source does. What must never happen is one proposal carrying two outcomes,
   or a mutation going ahead under an outcome that no longer describes it.

   | Outcome | What it means, and what you propose |
   |---|---|
   | Add | The claim is supported and nothing in what you could read represents it. Stage it as an attributed candidate. Say so when incomplete reads weaken the conclusion. |
   | Already represented | A stored claim already says this. Name it, note any limits on how well you could check, and create nothing. A source repeating itself is not a reason to add a row or raise `confidence`. |
   | Correct an error | The evidence shows the stored data was wrong when it was written. Propose `correct_record` on the supported fields only, with the prior meaning and the correction both explicit. |
   | Record a supported temporal transition | The old assertion was historically correct and the world then changed. Use `supersede_fact` where it applies, on its own narrow terms. |
   | Leave unresolved | Identity, evidence, dates, readable coverage, or the available operations are not enough. Describe what is missing and change nothing about the stored records. Where the incoming claim is itself worth keeping, stage it as an attributed claim so it survives the conversation. |

   Every proposed mutation names the target record ids, what the record means before and after, the
   attribution and any source receipt behind it, how precise the dates are, and the exact tool and
   arguments you would call.
4. **Wait for explicit acceptance, and take it per action.** Being asked to read a CV is not
   approval to write, and discussing one proposal does not approve the others. New claims keep the
   ordinary stage → review → commit gate.
5. **Reread immediately before you apply.** Check that the target and the identity still match,
   including validity and whichever fields the proposal turns on. If the record changed, disappeared,
   or can no longer be read, stop that action and go back for renewed review. Never silently
   retarget an accepted mutation onto whatever is there now.
6. **Apply, reread, and report.** Report completed, failed, skipped, and unresolved actions
   separately, keeping the ids a reader needs in order to check the outcome for themselves.

### What a newer document does not license

- **Omission is never deletion, and never an ending.** A shorter CV is not evidence that the
  employment, study, membership, or relationship it leaves out stopped being true. Propose nothing
  for a record the new document is merely silent about.
- **Concurrent roles, several skills, and different traits are not contradictions.** Consolidation
  `signals` identify comparisons worth a reader's attention; they are not verdicts about which
  record to discard.
- **Repetition is not independent evidence.** Documents copy each other. Another source receipt does
  not raise `confidence`, and where two assertions genuinely conflict and the evidence cannot settle
  them, both are kept and you say so.
- **Disagreement is not proof of error.** A newer document contradicting a stored value shows that
  two sources disagree, not that the stored one was wrong when it was written. Nothing in the store
  can settle it for you: no raw source material is kept, and a receipt records only that an artifact
  was processed, never what it said. Correcting on the newer document's say-so silently discards a
  rival claim. Reopen the original source and confirm, or leave which one is right unresolved.
- **Keeping a conflict means recording the incoming half of it.** Leaving the question unresolved
  changes nothing, and the document is gone when the conversation ends, so doing only that keeps the
  stored claim and loses the one that disagreed with it. If the newer claim is worth keeping, stage
  it the way capture would — an attributed claim about what the document said, `Revised CV gives the
  Northbridge start as 3 April 2024`, carrying `stated_by` — and say plainly that which of the two
  is correct is still open. That preserves both sides without either overwriting the other or
  pretending the disagreement was settled.
- **A new row records you, not the document.** `record_fact`, `set_affiliation`, and the replacement
  a `supersede_fact` opens all take no `stated_by`, so their provenance names the calling agent. A
  supersession keeps the *old* row's attribution untouched and gives the replacement yours. Where
  naming the source matters, say it inside the claim text as capture does, or stage an attributed
  candidate through `stage_candidates`, which does carry `stated_by`. Do not tell the user a
  transition recorded who asserted the new value when it did not.
- **A correction leaves attribution alone.** `correct_record` writes only the fields you name, and
  provenance is not one of them, so the repaired row keeps the attribution it was written with and
  your part is recorded in the audit trail instead. That is the point of a correction: the same
  source still asserts the claim, and only the value it was written down as was wrong. Do not report
  the original attribution as lost, and do not pad the corrected value with source text to make up
  for a loss that did not happen.
- **Never invent an effective date.** Exact dates belong in the validity fields and approximate ones
  in the claim text, as in capture. Do not manufacture a transition boundary out of a year, out of
  the newer CV's own date, or out of the day you read it. `supersede_fact` needs a real, known
  transition date; without one the transition stays unresolved.
- **There is no generic affiliation or relationship supersession.** A changed role at the same
  organisation has no supported transition, and `correct_record` is not a substitute — that operation
  is for data that was wrong, never for a value that was right and then stopped being current.
  Leave the transition unresolved. You may still propose a separately supported new claim, as long
  as you do not present it as closing or replacing the old affiliation.
- **Separate tool calls are not one transaction.** `supersede_fact` is atomic inside its own call;
  a review made of staging, commits, corrections, and supersessions has no collective rollback. If
  something fails partway, report exactly what did and did not happen, reread before proposing a
  retry, and never replay a successful action or attempt an unapproved compensating edit.
- **Rereading is a safeguard, not a lock.** It catches a target that moved between review and
  writing. It is not compare-and-swap, and nothing here introduces a concurrency token or a batch
  transaction.

Nadia Okonkwo's CV arrives again eighteen months after the capture example in `documents.md`, and one pass over
it produces all five outcomes:

- the new CV gives her Northbridge Analytics start date as 3 April **2024**; the stored affiliation
  says 2023. On the reads alone that is a conflict and nothing more, so it starts as
  **unresolved**. It becomes **correct an error** only because the user still has the first CV,
  reopens it, and confirms it also said 2024 — then `correct_record` on that affiliation's
  `valid_from` is repairing a mis-keyed distillation rather than overwriting a rival claim. Had the
  first CV been unavailable, the affiliation would have kept 2023 and the newer claim would have
  been staged as an attributed fact recording what the revised CV said, with which one is right left
  open;
- the CV says she relocated to Leeds on 1 September 2026 and the stored `city` fact says Bristol
  from an exact date — a **supported temporal transition**, so `supersede_fact` with
  `effective_from` 2026-09-01, which keeps the Bristol row, its dates and its attribution, and
  closes it on 31 August. The tool takes no `stated_by`, so apply the rule above rather than losing
  the source: the new value carries it, `Leeds; per revised CV`, and you tell the user the row's own
  provenance names you and not the document;
- the same 2017–2019 study appears again, already held as a fact whose text carries that
  imprecision — **already represented**, so nothing is written and no confidence moves;
- a certification the store does not hold is **added**, staged as an attributed candidate for review;
- the CV now calls her Principal Data Engineer at Northbridge Analytics from 2 March 2026. The date
  is exact, so the new role is **added** as an affiliation like any other. What stays **unresolved**
  is only the *transition*: whether the Senior Data Engineer role ended, and when. Both affiliations
  stand, and the older one gains no end date. Had the CV said only "March 2026", the day would be
  missing and the same capture rule would apply — an attributed fact whose text keeps the month,
  never an affiliation starting on a 1 March nobody wrote down;
- and her Harbour Data Trust board role is not mentioned anywhere in the new document. That is
  silence, not an ending: no proposal, and the affiliation stands exactly as it is.
