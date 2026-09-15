# M29 — Editable staging and ergonomic review

Status: Planned — not implemented.
See [roadmap](../roadmap.md#m29--editable-staging-and-ergonomic-review) and
[PR checklist](pr-plan.md#m29--editable-staging-and-ergonomic-review).

## Purpose and first principles

Staging already separates what an agent extracted from what the user accepted. The gate works; reaching it does not.
The reviewer's only two verbs are accept and ignore, and both are expensive to express. Over MCP the agent re-renders
the batch in prose and the user approves by naming 26-character ULIDs back. At a terminal, `pctx import review` prints
a flat id-led line per candidate with no numbering, no batch summary, and no loop. Nothing anywhere edits a candidate.

The consequence is a lossy gate. A candidate that is almost right — the role is stale, the date is a year out, the
person reference is ambiguous — cannot be corrected at the point of review. It is accepted as written or abandoned,
and abandoning it means asking the agent to extract the same material again in the hope that the second attempt is
closer. Review that only says yes or no pushes the reviewer toward yes.

M29 gives review two more verbs, amend and withdraw, and makes the surfaces that carry them usable: numbers instead of
ULIDs, a summary before the list, an interactive loop, and a whole batch editable in `$EDITOR`. What the gate *means*
does not move. Committing is still the only durable act, `--all` and `--accept` are still the approval, and an agent
still never commits on its own.

Correcting a candidate before it is committed is not a new kind of write; it is the reviewer finishing the extraction.
Direct-write tools (`remember`, `record_*`, `correct_record`, `supersede_fact`) are unchanged by this milestone, and
M24-style maintenance proposals over already-committed records stay conversational. This spec delivers no code.

## Staging model and boundaries

`import_staging(id, batch_id, source, candidate_json, status, created_at)` is review state, not domain state. Its rows
are not audited and appear in no changelog. A sync bundle does carry them: since bundle version 2,
`SqliteBundleReader._incomplete_staging()` exports every staging row of every receipt that is still reviewable,
filtered by receipt status and not by row status, and the strict `BundleStagingRow.status` admits exactly `pending`
and `committed`. A withdrawn row therefore cannot simply be left out: its pending dependents keep referencing it, and
bundle validation refuses a reference outside the exported batch. M29 advances the bundle contract to the next free
version at implementation time, whose staging row admits `rejected`, and restore writes rejected rows back as
rejected. Withdrawn rows and their dependents travel intact and survive a bootstrap restore; older versions stay
readable and emit no `rejected` row. An amended row travels as its current content under the same
`check_staged_candidate` gate restore already applies. Amending a candidate writes no audit entry and mints no
changelog row, for the same reason staging one never did — nothing has been asserted about anybody yet.

Editing is amendment in place on the existing row, not a new revision. The row keeps its id, its batch, its receipt,
and its position in the batch's staging order, so an id printed before an amendment still selects the same candidate
after one. There is no new table and no migration: `status` is already a `TEXT` column and gains one further value.

Six rules bound what an amendment may do.

| Rule | Reason |
|---|---|
| The patch is a shallow merge of fields into the stored candidate dict. | Deep merge makes removing a nested field unexpressible and its absence ambiguous. |
| The result re-validates through `STAGED_CANDIDATE_MODELS`, `extra="forbid"`, and through every input rule staging applied to the candidate: the `CANDIDATE_MODELS` normalization and length limits, such as a normalizable, bounded `relationship_type`, and distinct relationship endpoints. | The persisted shape is deliberately looser than staging input. A patched `relationship_type` of `---` or a relationship from a person candidate to itself passes that shape, then raises in `SetRelationship` or sits unresolved forever. |
| `type` cannot change. | A different type is a different candidate with different dependents; re-stage instead. |
| Only a `pending` row may be amended or withdrawn. | A committed row is a durable record and belongs to `correct_record`. |
| The amended batch re-runs every batch-wide rule `CandidateStager` applies at staging: `_validate`'s reference checks, so every `person_candidate_id`, participant, relationship, and evidence reference names a row of the same batch with the right type, and `_require_tracking_for_evidence`, so a receiptless batch still cannot gain a same-batch `evidence_candidate_ids` citation. The stager's rules are factored so they run over a persisted batch; amendment adds no copy of them. | Shape validation is per row. A dangling or wrong-type reference passes it, and a citation added to a receiptless batch strands its trait when the evidence commits separately, because only a source-tracked batch records the candidate mapping that resolves it. |
| A patch is bounded like a `stage-candidates` request — at most 1 MiB of patch JSON and 8 KiB per string — and the batch is re-measured against the `PreflightImportBatch` ceiling with the amended row substituted, before anything is written. | An amendment that grows a batch past the review ceiling makes `review` and `commit` refuse it, and none of the bounded commands could repair it. |

A person candidate's `matched_person_id` and `match_disposition` are computed fields. `match_person_candidate` unions
every active person whose normalized name or handle matches the candidate's tokens and reports a count, not the
colliding ids. Amending the name or handles re-runs that matcher on the amended values, and an amendment that leaves
the reference ambiguous leaves it ambiguous, its dependents unresolved at commit exactly as today.

"It is the Priya Sharma at Acme, not the other one" cannot be expressed through those tokens: adding a unique handle
while keeping the colliding name still unions both people. Choosing needs a second, explicit mechanism. A patch may set
`matched_person_id` to one active person id, and the use case accepts it only when that id is in the set the matcher
computes for the candidate's current tokens; any other id refuses with `person_not_a_match`. An accepted choice stores
`match_disposition` `matched` with that id, so dependents commit against the chosen person. This is the only way a patch
may touch either field: the choice is a reviewer's decision recorded on the row, not a guess written over an ambiguity.
So that a reviewer can make it, `review_import` and `pctx import review` list, for an ambiguous person row, the id and
canonical name of colliding active people in an additive `match_candidates` field, computed at read time and never
stored — the stored row keeps its count. Each projected canonical name is cut to at most 256 characters with an additive
`name_truncated` flag, because stored names have no length bound, and the list is capped at 10 per row, ordered by
canonical name then id, with an additive `match_candidates_truncated` flag, because a common name can collide with any
number of people and the review read must stay bounded by the staged payload it already measures. The projection's
worst-case size — ten entries of an id and a 256-character name per ambiguous row — is charged to the same review
ceiling `PreflightImportBatch` measures, before any name is read, so an ambiguous batch that would exceed it is refused
like any oversized batch rather than rendered. Capping the output does not bound the read beneath it:
`PersonReader.find_by_normalized_name()` loads every matching `Person`, and `match_person_candidate()` unions that whole
result, so thousands of people sharing a name would materialize thousands of records per ambiguous row. M29.1 replaces
that with three bounded queries on the person reader, run once over all of a candidate's tokens: a distinct count, a
page of at most ten distinct ids and canonical names ordered by canonical name then id, and an existence check for one
person id against those tokens. `match_person_candidate()` is rebuilt on the count and a one-id page, so staging,
commit, review, and amendment stay bounded, and a `matched_person_id` choice is validated by the existence check without
reading the collision set. The cap limits display, not choice: a patch naming any person in the matcher's full set is
accepted, and a reviewer finds one beyond the cap through the bounded `resolve_person` and `search_people` reads. M30's
ambiguity picker uses exactly this field, this flag, and this patch.

Withdrawal is the second verb. A withdrawn row moves to status `rejected`, stays in its batch, keeps being listed by
review, and is never committed. It is not deleted: a reviewer who cannot see what they dropped cannot check that they
dropped the right thing. Rejected rows follow the same cleanup path as pending rows in
`adapters/sqlite/import_cleanup.py`, so hard forget erases them with the rest of a batch's retained staging. They
travel in a sync bundle under the advanced version while their receipt is still reviewable, so a restored batch keeps
its withdrawals and its numbering.

A withdrawal changes what the receipt means, so it recomputes the receipt in the same transaction as the row status.
A source session is primary replicated state, so when the status changes the update goes through `audit_mutation` with
the whole row as its after-image, exactly as `CommitImport._record_provenance()` records the transitions it makes, and
`sync-log` shows it. The staging-row changes themselves stay unaudited, and an unchanged status writes nothing.
Today `workflow._session_status()` treats every row that is not `committed` as reviewable, while duplicate detection
looks for `pending` rows; with `rejected` added, both use one rule: only a `pending` row is reviewable. The receipt
status follows. Rows still pending leave it `staged` or `partially_committed` as today. No pending rows and at least
one committed row make it `committed`, exactly as a final commit does, and export then carries its mappings and no
staging rows, as for any committed receipt. No pending rows and nothing committed — every candidate withdrawn — make
it a new terminal status `withdrawn`: nothing is left to review and nothing was recorded. A `withdrawn` receipt keeps
its staging rows locally for review and forget, exports as a receipt with no staging rows and no mappings, and is
accepted by restore in that shape. Staging the same source again reports the withdrawn batch as having nothing left to
review, as it does for a committed one, and `--force` stages it afresh. Commit recomputes the receipt by the same rule,
so `sync_bundle` validation's "reviewable row" check counts `pending` rows only.

## Planned PRs and compatibility

### M29.1 — Amend and withdraw staged candidates

Add `AmendStagedCandidate(batch_id, candidate_id, patch)` and `WithdrawStagedCandidates(batch_id, candidate_ids)` beside
`ReviewImport` and `CommitImport` in `app/imports/workflow.py`, with the merge, revalidation, type, and match rules
above. A non-`pending` target refuses with a new `ImportPipelineError` code `candidate_not_pending`; a target outside
the batch keeps the existing `candidate_not_in_batch` refusal; a reference the amended batch cannot resolve refuses with
`candidate_reference_invalid`; a candidate failing a staging input rule refuses with the stager's own validation
refusal, as does a same-batch evidence citation added to a receiptless batch; a patch or resulting batch over its
ceiling refuses with the existing size refusals. Every refusal is atomic and writes nothing. Neither use case audits the
staging rows it changes. The one durable state a withdrawal can change, the source receipt, is journalled as it is at
commit: see below.

Every review surface has a gap between showing a batch and acting on it, and another CLI or MCP client may amend,
withdraw, or commit in that gap. `ReviewImport` therefore returns an additive `batch_digest`: a hash over every row's
id, status, and stored candidate content in staging order, computed at read time and never stored. Read-time
`match_candidates` are not part of it. `AmendStagedCandidate`, `WithdrawStagedCandidates`, and `CommitImport` each
accept an optional `expected_batch_digest`. When it is given, the use case recomputes the digest from the stored rows
inside a unit of work that takes the SQLite write lock before reading — `SqliteUnitOfWork(immediate=True)`, the mode
source receipts already use because a deferred `BEGIN` lets another writer act between a read and the decision based
on it — and a mismatch refuses with `batch_changed` and writes nothing. Callers that omit it, including plain
`pctx import commit` and `commit_import`, keep today's behaviour. The interactive loop, the editor round trip, and
M30's browser page always pass it.

`ImportStagingStore` gains `update_candidate(candidate_id, candidate)` and `mark_status(candidate_ids, status)`
alongside `stage_batch`, `list_batch`, and `mark_committed`. The SQLite adapter updates `candidate_json` and `status`
in place. `status` gains the value `rejected` with no migration, because the column is already `TEXT`.

`CommitImport` learns one rule: an accepted id whose row is `rejected` refuses the whole commit with
`candidate_withdrawn`. That matches the existing selection contract, where one unknown id refuses the whole selection
rather than committing the part that parsed — a reviewer who names a withdrawn candidate has a stale list, and the
safe response is to say so. `--all` today submits every review row, and `CommitImport` reports rows committed by an
earlier invocation in the version-1 commit document's `skipped_ids`. M29 keeps that: `--all`, and "commit
everything" over MCP, submit every row except `rejected` ones, so a rerun on a partially committed batch still
reports its already-committed rows. Withdrawn rows are left out silently because withdrawing them was the
instruction. Withdrawing a person candidate leaves its dependents
`unresolved` at commit. That needs a second change: `CommitImport._existing_resolution()` today adds a person row's
stored `matched_person_id` to the batch resolution before it looks at the row's status, so a withdrawn person row
that matched an existing person would still resolve its dependents. Rejected rows are skipped before any stored
match or mapping is consulted.

MCP gains `amend_candidate` and `withdraw_candidates`, both annotated `_WRITE` and wrapped by `flag_refusals` like
every other tool in `adapters/mcp/tools/imports.py`. `review_import` reports `rejected` in the row status it already
returns. `commit_import` keeps its `(batch_id, accepted_ids)` shape.

CLI gains `pctx import amend BATCH CANDIDATE --patch JSON|-` and `pctx import reject BATCH CANDIDATE...`, both with
`--json`. Their `--json` output is the refreshed full `people-context-import-review` version-1 document for the batch,
exactly what `pctx import review BATCH --json` would print next. That document defines `candidates` as every candidate
in the batch, so emitting only the affected rows under the same format would repurpose an absent row from "not in the
batch" to "omitted". Returning the whole document keeps that meaning, needs no new format, and gives a caller the
updated ordinals and summary in one read. `amend_candidate` and `withdraw_candidates` return the same refreshed review.
Refusals name the candidate index, and a field only when the candidate models declare it; an undeclared key is shown as
`(redacted)`, because its name is untrusted input that may carry private text. They never echo the patch payload.

Guidance gains the chat review loop: `skills/people-context-usage/SKILL.md`, the MCP prompts `remember` and
`end_of_session_capture`, and the packaged `people-context://guide` resource all describe presenting a staged batch as
a numbered list, translating "change X" and "drop Y" into `amend_candidate` and `withdraw_candidates`, re-presenting
the revised batch, and calling `commit_import` only on explicit acceptance of it. Confirming an amendment is not
acceptance of the batch — the one sentence an agent is most likely to get wrong, and therefore the one the parity
tests in `tests/test_usage_skill.py` and `tests/adapters/mcp/test_prompts_and_resources.py` must assert.

### M29.2 — Numbered review and commit by number

`ImportReviewRow` gains an additive `ordinal`: 1-based, assigned over the batch's deterministic staging order, derived
at read time rather than stored. `ReviewImport` states that order explicitly — `created_at`, then id as tie-breaker —
because an ordinal is only usable if it is the same on the next read. Withdrawn rows stay listed and keep their
ordinal, so amending or withdrawing a candidate never renumbers the ones after it. The field is additive on the
`people-context-import-review` version-1 document and on the `review_import` MCP response; neither version advances.

`print_import_review` in `cli/rendering.py` leads each line with `#n`, then status, type, and a one-line summary, with
the canonical id demoted to a trailing column so it stays copyable. Above the list it prints a batch summary: how many
people are new, how many match an existing person, how many are ambiguous, counts per candidate type, and the number
withdrawn. The summary is what tells a reviewer whether the list is worth reading line by line.

`parse_candidate_selection` accepts ordinals, ranges, and ids, mixed in one selection — `--accept 1 3-5`,
`--accept 2,01J...CANDIDATE`. A token that exactly equals a candidate id of the batch is that id, before any shorthand
parsing: staging ids are format-opaque in the released bundle contract, so a restored batch may hold an id spelled
`1` or `3-5`, and an existing invocation naming it keeps its meaning. Shorthand applies only to a token that is not a
known id. An unknown ordinal, an out-of-range ordinal, or an unknown id still refuses the whole selection. The
parser is shared with the vCard step of `cli/onboarding.py`, so `pctx init` gets numbered selection without a second
implementation and without a second set of rules.

`pctx import review BATCH --interactive` steps through the pending candidates one at a time, offering `[a]ccept [s]kip
[w]ithdraw [e]dit [q]uit`. `e` prompts for a field patch and runs the M29.1 amend use case, so an invalid edit refuses
that candidate and leaves the loop where it was. The loop keeps the `batch_digest` of the review it is showing and
passes it to every amendment, withdrawal, and the final commit; after each of its own writes it rereads the batch and
continues from the new digest. If another client changed the batch, the action refuses with `batch_changed`, and the
loop discards every acceptance it has collected, says so, and restarts from the first pending candidate of the reread
batch. Keeping earlier acceptances would let the final commit match the new digest while committing an earlier row whose
new content the reviewer never saw. At the end the accepted set is committed through `CommitImport` and the commit
result is printed. `q` exits having committed nothing; skipping leaves a row pending for a later pass. It uses `input()`
like the onboarding loop — the only interactive loop in the repository — and adds no TUI dependency. `--interactive` and
`--json` are mutually exclusive and refuse together.

### M29.3 — Edit a batch in `$EDITOR`

`pctx import edit BATCH` writes the review document to a private temporary file through the shared atomic private-file
writer at mode 0600, opens it with `$VISUAL` and then `$EDITOR` resolved through `shlex.split` and run with
`subprocess.run` without a shell, and on exit diffs the edited document against the batch: a candidate that is gone is
withdrawn, a candidate whose fields changed is amended through the M29.1 use case, and an untouched candidate is a
no-op. Only a row's `candidate` object is editable. Every other field the document renders — the document's `format`,
`version`, `batch_id`, and `batch_digest`, and each row's `id`, `ordinal`, `source`, `status`, `match_candidates`, and
truncation flags — must match the rendered document exactly; any change refuses the whole apply with
`review_field_changed`, naming the row index and the declared field. Deleting a row is the only way to withdraw it, so
changing `status` to `rejected` cannot be mistaken for a no-op that the commit prompt then commits. The editor's exit
status is checked first: `subprocess.run` does not raise on a nonzero status, so a crashed editor, or one that could not
save, would otherwise lead straight to a commit prompt over an unreviewed batch. A nonzero status applies nothing, asks
nothing, and exits nonzero naming the status. After a successful edit the command prints the batch summary and asks
`Commit N pending candidates? [y/N]` unless `--no-commit` was given. The prompt reads the controlling terminal, never a
pipe. The temporary file is deleted afterwards in every case. The review document carries distilled personal data, so
the existing disclosure warning applies to the file the editor opens. With neither variable set, the command refuses
with exit status 2 and names both variables it checked.

`pctx import edit BATCH --from FILE|-` applies an already-edited review document without opening anything and never
prompts: `--from -` consumes stdin, so no confirmation could be read from it, and a script has nobody to ask. It applies
the edits, prints the summary, and leaves committing to `pctx import commit`. `--no-commit` is therefore meaningless
with `--from` and is refused together with it. So `pctx import review BATCH --json > f; $EDITOR f; pctx import edit
BATCH --from f` is the same workflow for a script, or for an agent working through the CLI without MCP. Both paths read
the edited document under a bound derived from the batch itself, not from the 1 MiB `stage-candidates` request bound or
a fixed per-row allowance: the byte length of the review document the command renders for the batch at apply time, with
every field it emits — restored format-opaque ids and read-time `match_candidates` names included — plus the headroom
left under the staged-payload ceiling multiplied by the renderer's worst-case expansion. The review document is
indented, so a value added to it costs more rendered bytes than persisted bytes; because the strict candidate schema
fixes the maximum nesting depth and the renderer fixes its indentation, the largest ratio of rendered to compact bytes
for any added token is a constant, computed once from those two facts and pinned by a test. An unchanged document
therefore always fits, whatever its ids and names; a batch grown to the ceiling by the smallest list entries, such as
one-character aliases, is accepted back; and growth the ceiling could never admit is refused before parsing. The
resulting batch is then re-measured against the ceiling like any amendment. The 1 MiB bound still applies to a single
`pctx import amend --patch`.

Applying a document is all-or-nothing. Rows are addressed by `ordinal` or `id` and must belong to the named batch; a
document naming another batch, an unknown id, a changed `type`, or an added row refuses the whole apply and changes
nothing. Calling the M29.1 use cases row by row cannot keep that promise, because each would commit its own transaction
before a later row's refusal is found. M29.3 therefore adds one `ApplyReviewEdits(batch_id, amendments, withdrawals)`
use case: it checks the document's `batch_digest` against the stored batch under the write lock, refusing with
`batch_changed` if another client changed it since the document was rendered, then validates every amendment and
withdrawal against the batch as it would stand after all of them — shape, references, match choices, and ceilings — and
then writes every row inside one unit of work from the staging store that takes the write lock before it reads, so a
refusal anywhere writes nothing and no other writer changes the batch between validation and write.
`AmendStagedCandidate` and `WithdrawStagedCandidates` share its validation; a multi-row withdrawal is likewise one
transaction. Refusals name the index and, when the models declare it, the field; never the payload and never an
undeclared key, which is shown as `(redacted)` — an edited file is untrusted input, and a field name an editor invented
is not safe to echo.

## Privacy and compatibility

No raw source text enters staging through amendment. A patch carries candidate fields and nothing else, and the
existing rule that raw import bodies never become staging metadata, logs, errors, or provenance receipts is unchanged.
Refusal reporting follows the established candidate-JSON rule throughout: the index, a location part only when the
models declare it and otherwise `(redacted)`, a message only when the schema derived it, and never the rejected value.

`ordinal` is an additive field on the version-1 review document and the `review_import` response, under the existing
promise that new fields are additive and a consumer ignoring unknown fields keeps working. `candidate_not_pending` and
`candidate_withdrawn` are additive error codes, and a consumer should ignore a code it does not recognize. The
`rejected` status is an additive value of a field that already exists, as is the `withdrawn` receipt status that
`pctx sources` and `pctx source show` may now report. `amend`, `reject`, `edit`, `--interactive`,
`--patch`, `--from`, and `--no-commit` are new commands and flags; every existing invocation keeps its meaning.

The sync bundle advances one version so that its staging row admits `rejected` and its source session admits
`withdrawn`. Both models are shared by every released version today, so the new version gets its own staging-row and
source-session models rather than widening the shared literals. The bundle is deliberately not additively extensible
within a version, so this follows the established rule: emission moves to the new version, every released version stays
readable, and a document of an older version is refused if it carries a `rejected` row or a `withdrawn` receipt. A
bundle taken after an amendment carries the amended content, which restore validates exactly as it validates a row
staged that way. Hard forget removes rejected rows exactly as it removes pending ones. Person merge and forget behaviour
over committed records is untouched by this milestone.

## Acceptance scenarios and verification

- An amendment adding an unknown field, changing `type`, or targeting a committed or rejected row is refused; the stored
  candidate is unchanged and the refusal names a declared field, not the patch. A patch whose unknown key is itself
  private text refuses with that key shown as `(redacted)`.
- A patched `relationship_type` of `---`, a relationship whose two endpoints become the same person candidate, and a
  same-batch `evidence_candidate_ids` citation added in a batch without a receipt each refuse with the stager's
  refusal and store nothing; the same citation in a source-tracked batch is accepted.
- An amendment pointing `person_candidate_id`, a participant, a relationship end, or an evidence reference at a
  missing, foreign-batch, or wrong-type row refuses with `candidate_reference_invalid`. A patch over 1 MiB, a string
  over 8 KiB, or an amendment that would push the batch past the review ceiling refuses before any write, and the
  batch stays reviewable and committable afterwards.
- An ambiguous person row lists its `match_candidates`. Amended with a `matched_person_id` the matcher computes, it
  resolves and its dependents commit; naming a person outside that set refuses with `person_not_a_match`; amended
  without a choice it stays ambiguous and its dependents stay `unresolved`. Neither path guesses.
- With 5,000 active people sharing a name, reviewing a batch of ambiguous rows for it loads no `Person` records for the
  projection, and choosing the 5,000th person is validated by a single existence check.
- A name colliding with 25 people lists 10 `match_candidates` in canonical-name, id order with
  `match_candidates_truncated` set, and a `matched_person_id` naming the 25th is still accepted.
- A withdrawn candidate is skipped by `--all` and by "commit everything" over MCP; naming its id in `--accept` or in
  `accepted_ids` refuses the whole commit with `candidate_withdrawn` and commits nothing. Rerunning `--all` on a
  partially committed batch that also has a withdrawn row reports the earlier rows in `skipped_ids`, as today.
- A withdrawn person candidate whose row carries a live `matched_person_id` resolves nothing: `--all` leaves its
  pending facts, interactions, and relationships `unresolved` rather than committing them to the matched person.
- Ordinals are unchanged by an intervening amendment or withdrawal, and a review taken before and after one selects
  the same candidates. A mixed `1,3-5,01J...` selection resolves; one unknown member refuses all of it. In a batch
  whose restored candidate ids include the literal `1`, `--accept 1` selects that id, not the first ordinal.
- `--interactive` accepting some and quitting commits nothing on `q`, commits exactly the accepted set otherwise, and
  survives an invalid edit mid-loop without losing the accepted set. `--interactive --json` refuses.
- An editor round trip with one row removed, one row changed, one invalid edit, and a document naming a foreign batch
  produces, respectively, a withdrawal, an amendment, a refusal naming index and field, and a whole-apply refusal. A
  document with a valid change on row 1 and an invalid change on row 5 refuses and leaves row 1 unchanged.
  With no editor configured the command exits 2 and says which variables were checked; the temp file never survives.
- A batch within a few bytes of the staged-payload ceiling, grown there through `edit` by many one-character aliases,
  applies back and then passes re-measurement; one more alias is refused by re-measurement, not by the read bound.
- An unchanged review document applies back through `edit --from` for a batch over 1 MiB, one with long restored
  candidate ids, and one whose `match_candidates` carry long canonical names.
- A row amended by another client after `pctx import edit` rendered its document, or after `review --json` wrote the
  file `--from` reads, makes the apply refuse with `batch_changed` and keeps the newer amendment; so does a change
  between the editor apply and the commit prompt. In `--interactive`, a row amended elsewhere mid-loop refuses the next
  action with `batch_changed` instead of overwriting or committing it, and an earlier row that was already accepted and
  then amended elsewhere is not committed until the restarted loop shows it and the reviewer accepts it again.
- Changing a row's `status` from `pending` to `rejected`, or its `source` or `ordinal`, without touching `candidate`
  refuses the whole apply with `review_field_changed` and never reaches the commit prompt.
- An editor exiting nonzero applies nothing and never shows the commit prompt. `--from -` applies its edits and
  exits without reading a confirmation; `--from` with `--no-commit` refuses.
- Withdrawing the last pending rows of a source-tracked batch makes its receipt `committed` when something was
  committed and `withdrawn` when nothing was, in the same transaction; `pctx sources` then reports no unfinished work,
  a bundle round trip restores either receipt, and restaging the source reports nothing left to review until
  `--force`.
- Hard forget removes rejected staging rows with pending ones. After withdrawing a person candidate whose facts stay
  pending, `pctx sync push` succeeds and restore reproduces the rejected row, its pending dependents, and their
  ordinals; a bundle exported after an amendment restores the amended content. An older-version bundle carrying a
  `rejected` row or a `withdrawn` receipt is refused, and every released version still restores.
- MCP prompt, packaged guide, and usage-skill parity tests assert the chat review loop wording, including that
  confirming an amendment is not acceptance of the batch.
- Implementing PRs add fake-port and real-SQLite tests for the new use cases and store methods, in-memory tests for
  the two new MCP tools, and CLI tests for `amend`, `reject`, `--interactive`, and `edit`, then run the repository
  gates: `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`, and `uv build` for the public-surface PRs.

## Alternatives and deferred work

- A separate proposals table for edits was rejected: staging already is the proposal table, and a second one would
  need its own lifecycle, cleanup, and forget integration to express what one status value and an in-place update do.
- Storing amendment history, or an audit trail over staging, would make review state durable state. Staging is
  discarded at commit; what survives is the committed record, whose history the changelog already owns.
- No stored revision token on staged rows. The read-time `batch_digest` covers the gap between display and action
  without one. Plain `pctx import commit` and `commit_import` keep today's behaviour: M24 establishes that rereading
  before applying is a workflow safeguard rather than an isolation promise, and those commands display nothing before
  they act.
- Leaving rejected rows out of the bundle was rejected: a withdrawn person row's pending dependents keep referencing
  it, so omitting the row breaks bundle validation, and rewriting or dropping dependents on export would change what
  the reviewer left pending.
- Reinstating a withdrawn candidate is out of scope: re-stage the material instead. So is appending candidates to an
  existing batch, which would break the batch's one-source-one-receipt meaning.
- A proposals lifecycle for `correct_record` and `supersede_fact` stays out. Those act on committed records and their
  review is conversational under M24.
- No TUI dependency and no YAML: `input()` and the review document's existing JSON are enough for both new loops.
- A browser review surface is delivered by [M30](m30-local-web-review.md), not here.
