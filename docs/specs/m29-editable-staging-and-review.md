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
are not audited, appear in no changelog, and reach no sync bundle: a bundle carries the durable outcomes of a commit,
never a pending row. Every M29 operation stays inside that boundary. Amending a candidate writes no audit entry and
mints no changelog row, for the same reason staging one never did — nothing has been asserted about anybody yet.

Editing is amendment in place on the existing row, not a new revision. The row keeps its id, its batch, its receipt,
and its position in the batch's staging order, so an id printed before an amendment still selects the same candidate
after one. There is no new table and no migration: `status` is already a `TEXT` column and gains one further value.

Four rules bound what an amendment may do.

| Rule | Reason |
|---|---|
| The patch is a shallow merge of fields into the stored candidate dict. | Deep merge makes removing a nested field unexpressible and its absence ambiguous. |
| The result re-validates through `STAGED_CANDIDATE_MODELS`, `extra="forbid"`, exactly as staging validated it. | One persisted shape gate. An amended row and a staged row are the same kind of object. |
| `type` cannot change. | A different type is a different candidate with different dependents; re-stage instead. |
| Only a `pending` row may be amended or withdrawn. | A committed row is a durable record and belongs to `correct_record`. |

A person candidate's match fields re-run the ambiguity-preserving matcher staging used, on the amended values. "It is
the Priya Sharma at Acme, not the other one" is therefore recorded as an amendment whose match result was recomputed,
not as a guess written over an ambiguity. An amendment that leaves the reference ambiguous leaves it ambiguous, and
its dependents stay unresolved at commit exactly as they do today.

Withdrawal is the second verb. A withdrawn row moves to status `rejected`, stays in its batch, keeps being listed by
review, and is never committed. It is not deleted: a reviewer who cannot see what they dropped cannot check that they
dropped the right thing. Rejected rows follow the same cleanup path as pending rows in
`adapters/sqlite/import_cleanup.py`, so hard forget erases them with the rest of a batch's retained staging.

## Planned PRs and compatibility

### M29.1 — Amend and withdraw staged candidates

Add `AmendStagedCandidate(batch_id, candidate_id, patch)` and `WithdrawStagedCandidates(batch_id, candidate_ids)`
beside `ReviewImport` and `CommitImport` in `app/imports/workflow.py`, with the merge, revalidation, type, and match
rules above. A non-`pending` target refuses with a new `ImportPipelineError` code `candidate_not_pending`; a target
outside the batch keeps the existing `candidate_not_in_batch` refusal. Neither use case touches audit or changelog.

`ImportStagingStore` gains `update_candidate(candidate_id, candidate)` and `mark_status(candidate_ids, status)`
alongside `stage_batch`, `list_batch`, and `mark_committed`. The SQLite adapter updates `candidate_json` and `status`
in place. `status` gains the value `rejected` with no migration, because the column is already `TEXT`.

`CommitImport` learns one rule: an accepted id whose row is `rejected` refuses the whole commit with
`candidate_withdrawn`. That matches the existing selection contract, where one unknown id refuses the whole selection
rather than committing the part that parsed — a reviewer who names a withdrawn candidate has a stale list, and the
safe response is to say so. `--all`, and "commit everything" over MCP, mean every `pending` row: withdrawn rows are
skipped silently because withdrawing them was the instruction. Withdrawing a person candidate leaves its dependents
`unresolved` at commit, unchanged from today.

MCP gains `amend_candidate` and `withdraw_candidates`, both annotated `_WRITE` and wrapped by `flag_refusals` like
every other tool in `adapters/mcp/tools/imports.py`. `review_import` reports `rejected` in the row status it already
returns. `commit_import` keeps its `(batch_id, accepted_ids)` shape.

CLI gains `pctx import amend BATCH CANDIDATE --patch JSON|-` and `pctx import reject BATCH CANDIDATE...`, both with
`--json`. Their `--json` output is the affected review row or rows inside the existing `people-context-import-review`
version-1 document — a one-row or n-row review document, not a new format. A consumer already parses that document,
an amendment produces no information a review row cannot carry, and a new format would earn a compatibility-table
entry for nothing. Refusals name the candidate index and the offending field and never echo the patch payload.

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
`--accept 2,01J...CANDIDATE`. An unknown ordinal, an out-of-range ordinal, or an unknown id still refuses the whole
selection. The parser is shared with the vCard step of `cli/onboarding.py`, so `pctx init` gets numbered selection
without a second implementation and without a second set of rules.

`pctx import review BATCH --interactive` steps through the pending candidates one at a time, offering
`[a]ccept [s]kip [w]ithdraw [e]dit [q]uit`. `e` prompts for a field patch and runs the M29.1 amend use case, so an
invalid edit refuses that candidate and leaves the loop where it was. At the end the accepted set is committed through
`CommitImport` and the commit result is printed. `q` exits having committed nothing; skipping leaves a row pending for
a later pass. It uses `input()` like the onboarding loop — the only interactive loop in the repository — and adds no
TUI dependency. `--interactive` and `--json` are mutually exclusive and refuse together.

### M29.3 — Edit a batch in `$EDITOR`

`pctx import edit BATCH` writes the review document to a private temporary file through the shared atomic
private-file writer at mode 0600, opens it with `$VISUAL` and then `$EDITOR` resolved through `shlex.split` and run
with `subprocess.run` without a shell, and on exit diffs the edited document against the batch: a candidate that is
gone is withdrawn, a candidate whose fields changed is amended through the M29.1 use case, and an untouched candidate
is a no-op. It then prints the batch summary and asks `Commit N pending candidates? [y/N]` unless `--no-commit` was
given. The temporary file is deleted afterwards. The review document carries distilled personal data, so the existing
disclosure warning applies to the file the editor opens. With neither variable set, the command refuses with exit
status 2 and names both variables it checked.

`pctx import edit BATCH --from FILE|-` applies an already-edited review document without opening anything, so
`pctx import review BATCH --json > f; $EDITOR f; pctx import edit BATCH --from f` is the same workflow for a script,
or for an agent working through the CLI without MCP.

Applying a document is all-or-nothing in what it will accept. Rows are addressed by `ordinal` or `id` and must belong
to the named batch; a document naming another batch, an unknown id, a changed `type`, or an added row refuses the
whole apply and changes nothing. Refusals name the index and the field, never the payload — an edited file is
untrusted input, and a field name an editor invented is not safe to echo.

## Privacy and compatibility

No raw source text enters staging through amendment. A patch carries candidate fields and nothing else, and the
existing rule that raw import bodies never become staging metadata, logs, errors, or provenance receipts is unchanged.
Refusal reporting follows the established candidate-JSON rule throughout: index and field, never the rejected value.

`ordinal` is an additive field on the version-1 review document and the `review_import` response, under the existing
promise that new fields are additive and a consumer ignoring unknown fields keeps working. `candidate_not_pending` and
`candidate_withdrawn` are additive error codes, and a consumer should ignore a code it does not recognize. The
`rejected` status is an additive value of a field that already exists. `amend`, `reject`, `edit`, `--interactive`,
`--patch`, `--from`, and `--no-commit` are new commands and flags; every existing invocation keeps its meaning.

Sync bundles carry durable commit outcomes and no staging rows, so amendment and withdrawal are invisible to export,
restore, and peers by construction. Hard forget removes rejected rows exactly as it removes pending ones. Person merge
and forget behaviour over committed records is untouched by this milestone.

## Acceptance scenarios and verification

- An amendment adding an unknown field, changing `type`, or targeting a committed or rejected row is refused; the
  stored candidate is unchanged and the refusal names the field, not the patch.
- An ambiguous person candidate amended with disambiguating fields resolves and its dependents commit; one amended
  without them stays ambiguous and its dependents stay `unresolved`. Neither path guesses.
- A withdrawn candidate is skipped by `--all` and by "commit everything" over MCP; naming its id in `--accept` or in
  `accepted_ids` refuses the whole commit with `candidate_withdrawn` and commits nothing.
- Ordinals are unchanged by an intervening amendment or withdrawal, and a review taken before and after one selects
  the same candidates. A mixed `1,3-5,01J...` selection resolves; one unknown member refuses all of it.
- `--interactive` accepting some and quitting commits nothing on `q`, commits exactly the accepted set otherwise, and
  survives an invalid edit mid-loop without losing the accepted set. `--interactive --json` refuses.
- An editor round trip with one row removed, one row changed, one invalid edit, and a document naming a foreign batch
  produces, respectively, a withdrawal, an amendment, a refusal naming index and field, and a whole-apply refusal.
  With no editor configured the command exits 2 and says which variables were checked; the temp file never survives.
- Hard forget removes rejected staging rows with pending ones; bundle export and restore are byte-identical to a run
  where the same batch was reviewed without amendment or withdrawal.
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
- A revision token on staged rows, or compare-and-swap on commit, is deferred. Single-user staging has one reviewer,
  and M24 already establishes that rereading before applying is a workflow safeguard rather than an isolation promise.
- Reinstating a withdrawn candidate is out of scope: re-stage the material instead. So is appending candidates to an
  existing batch, which would break the batch's one-source-one-receipt meaning.
- A proposals lifecycle for `correct_record` and `supersede_fact` stays out. Those act on committed records and their
  review is conversational under M24.
- No TUI dependency and no YAML: `input()` and the review document's existing JSON are enough for both new loops.
- A browser review surface is delivered by [M30](m30-local-web-review.md), not here.
