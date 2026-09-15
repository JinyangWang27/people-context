# M30 — Local web view, review, and edit

Status: Planned — not implemented.
See [roadmap](../roadmap.md#m30--local-web-view-review-and-edit) and
[PR checklist](pr-plan.md#m30--local-web-view-review-and-edit).

## Purpose and first principles

Give a local browser page for three tasks: looking at recorded people, reviewing a staged import batch, and
editing staged candidates before commit. The audience is users for whom the terminal and the chat client are
the obstacle, not the data: someone who can read a table and tick boxes but will not type `pctx import review
<batch-id> --json`, and someone whose agent conversation drifted away from a batch that still needs decisions.

The browser is a fourth client of the same use cases the CLI commands, the MCP tools, and the `$EDITOR`
round-trip already call. It is not a second write path. No use case, validation rule, resolution rule, audit
seam, or disclosure gate exists only for the browser, and nothing reachable from the page is reachable only
from the page. If a decision can be made in the page it can be made with `pctx`, and the two agree because
they call the same code.

It is not a service. It runs when the user starts it, serves one browser on the loopback interface, and exits.
There is no daemon, no background port, no account, no remote mode, and no path by which a second machine or a
second person reaches the data. The local-first promise and the loopback-only HTTP boundary stated in
[AGENTS.md](../../AGENTS.md) and [privacy and safety](../privacy-and-safety.md) are unchanged by this milestone,
and the page is built so that the same boundary holds against a hostile web page open in another browser tab.

Three PRs follow the verbs: view (M30.1), review (M30.2), edit (M30.3). Each is independently useful and
independently mergeable, and each later PR only adds views and endpoints to the process the first one starts.
This specification delivers no code.

## Shared foundation and security model

M30.1 owns the foundation below; M30.2 and M30.3 reuse it unchanged and add nothing to it.

`pctx browse [--open] [--port N]` starts a Starlette application under uvicorn, bound to `127.0.0.1` on an
ephemeral port unless `--port` names one, prints the URL carrying a per-launch token, optionally opens that URL
with the standard-library `webbrowser`, and exits on Ctrl-C or when the page posts that it is done. There is no
daemon, no configuration file, no PID file, and no state that survives the process. Starting it twice starts two
unrelated processes with two unrelated tokens.

The page is one HTML document with its CSS and JavaScript inline, served from Python string constants or package
data. Every view — people, person, import sources, and later one batch — is rendered by that document from JSON
endpoints, and navigation between views is client-side. There is no second document to link to, which is what lets
the token stay out of every URL after the first load: an ordinary link or form submission cannot carry a request
header, so a multi-document design would have had to copy the token into every `href`. No bundler, no Node
toolchain, no CDN, no external font, and no third-party script: everything the browser executes was reviewed in
this repository, and a page that fails to load a resource cannot exist because there is nothing external to load.
`Content-Security-Policy: default-src 'self'` is sent on every HTML response, with the inline script admitted by a
nonce generated per response — not by `'unsafe-inline'`, which would forfeit the protection the header is being
sent for.

The security model is identical in all three PRs:

- Loopback bind only. There is no `--host`; a request to bind anything other than `127.0.0.1` is refused rather
  than honoured. Remote access, LAN access, and tunnelling remain out of scope, as they are for the MCP server.
- A per-launch token from `secrets.token_urlsafe` is required on every request: as a query parameter on the
  first load, then as a request header the page sets on every subsequent request. It is compared with
  `secrets.compare_digest`. It is never written to the database, a file, a log line, or an error message; the
  printed URL on stdout is the only place it appears. uvicorn is started with `access_log=False` and a log
  configuration that never formats a request path, because its default access log writes the first
  `GET /?token=...` line before application code runs. A subprocess test exercises the launched server, not only
  a `TestClient`, and asserts that the token occurs on stderr and stdout exactly once, in the printed URL.
- `Host` and `Origin`, and `Sec-Fetch-Site` where the browser sends it, are checked against the bound address on
  every request. This defeats DNS rebinding and cross-tab request forgery from an ordinary web page, which can
  guess a loopback port but cannot read the token or forge these headers.
- A missing token, a wrong token, an unexpected `Host`, or a foreign `Origin` produces one generic refusal
  carrying no personal data, no batch id, and no statement of which check failed. The refusal is not logged with
  the token value.
- Every person, candidate, and source string rendered into a page is HTML-escaped. Recorded data is untrusted
  input here exactly as it is at the importer boundary: a name or note containing markup is displayed, not run.
- `Cache-Control: no-store` and `Referrer-Policy: no-referrer` on every response, so personal data is not left in
  the browser cache and no URL carrying a token or a batch id leaves in a referrer header.
- The review disclosure warning (`REVIEW_DISCLOSURE_WARNING`, already shown by `pctx import review`) is printed
  on stderr at startup and shown in the page wherever staged candidates are displayed.

Disclosure parity is part of the foundation, and it has two parts because the page shows two kinds of data. Reads of
durable person records apply the same sensitivity policy as ordinary MCP reads: no `sensitive` or `restricted` material,
no signal that a hidden record exists. Staged candidates are not durable records: `ReviewImport` returns them verbatim,
including a fact or interaction candidate marked `sensitive` or `restricted`, because the reviewer must see what they
are about to commit — exactly as `pctx import review` and `review_import` show it today. The batch view keeps that
parity and shows the review disclosure warning above every staged candidate, instead of filtering them, which would make
the review incomplete. Elevated durable material appears only when `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE` is set in the
environment of the `pctx browse` process itself, through the existing `process_elevation_enabled` rule, exactly as the
MCP server consults it. The page never offers a toggle, a checkbox, or a URL parameter for that: elevation is an
operator decision made before the process starts, and a decision the page could change would be a decision an open
browser tab could change.

`starlette`, `uvicorn`, `sse-starlette`, and `httpx` are already present in the resolved environment as
transitive dependencies of `mcp>=2,<3`, but they are not declared. The PR that first imports `starlette` and
`uvicorn` directly must declare both in `pyproject.toml` with ranges compatible with what `mcp` already pins, and
must show `uv lock --check` reporting no resolution change: the declaration records an existing fact, it does not
add a dependency. Whether tests may use `httpx` and `starlette.testclient` under the same reasoning must be
verified against the resolved environment at implementation time, and declared the same way if they are used.

## Planned PRs and compatibility

M30.1 depends on nothing in M29. M30.2 depends on M29.1 for withdrawal and M29.2 for review-row `ordinal`.
M30.3 depends on M29.1 for amendment. All three are independent of M28.3.

### M30.1 — Read-only local viewer

Deliver `pctx browse`, the foundation above, and three read-only views.

- People list: canonical name, aliases, and summary — exactly the columns `ListPersonIndex`, the read behind
  `pctx list --json`, already carries. No relationship-to-self or last-interaction column is added, because
  neither exists in that read and a composed read is not introduced here. Rows open the person view.
- Person view: the `brief` content — summary, affiliations, facts, interactions, reminders, and traits — from
  `ComposePersonBrief`, the use case behind `pctx brief`. That projection bounds facts and interactions at
  `BRIEF_CONTEXT_ITEMS` but does not copy `PersonContextResult.truncated` into `PersonBriefDocument`, so neither
  `pctx brief` nor this view could say a section was cut. M30.1 adds that flag as an additive top-level `truncated`
  on the version-1 brief document — the same name and meaning `get_person_context` uses — and `pctx brief` prints
  it in words. The view shows the same flag, so a bounded section is never presented as complete.
- Import sources: one page at a time of the receipts `ListImportSources` returns, with its existing limit and
  cursor, showing each receipt's kind, label, status, and batch id exactly as `pctx sources` does. Opening one
  shows only the `staged_total` and `staged_by_status` counts from `ShowImportSource`. Its `mappings` and
  `mappings_by_disposition` are not exposed: they name and count committed durable records, including a
  `sensitive` or `restricted` fact, without any disclosure filter, so returning them would signal hidden records.
  Staged counts describe review state, which the batch view shows verbatim anyway. The projection is the same with
  or without elevation, and committed outcomes stay with the operator-only `pctx source show`. The listing has no
  pending-status filter and no counts, and scanning every page to build a pending-only list would be unbounded, so
  the view does not claim to be one. A batch staged without a receipt is not listed; it is opened by batch id.

JSON endpoints wrap the existing read use cases and return what those use cases return. There is no new read
model, no cache, no search index, and no semantic search. Every view is read-only: no view carries a form, and
no endpoint in this PR writes, other than the additive brief flag above. A receipt with a batch opens M30.2's
batch view once that PR exists.

This realizes the post-roadmap candidate "read-only local web viewer (`pctx browse`)"; that candidate is retired
from the roadmap's deferred list by the PR that delivers this.

### M30.2 — Batch review in the browser

Depends on M29.1 and M29.2. Add one batch page and the endpoints it needs.

The page lists candidates as a table ordered by `ordinal`, the same order `pctx import review` prints, with a
checkbox on each pending row, a one-line summary of the candidate, and the match state for person candidates:
new, matches an existing person, or ambiguous with a stated number of candidates. Withdrawn and committed rows
appear with a status badge and no checkbox. A header carries the batch summary: source, staged time, and counts
by status. The actions are "Accept selected", "Withdraw selected", and "Commit accepted".

Endpoints wrap `ReviewImport`, `WithdrawStagedCandidates`, and `CommitImport`. A refusal is displayed as the use
case's own error code — `candidate_not_pending`, `candidate_withdrawn`, and the rest — and never echoes the
payload that was refused, in keeping with M29's rule that a refusal names the index and the field rather than
repeating the value.

Commit requires one explicit click on a confirmation naming the number of candidates being accepted. It does not
promise that number will be committed: `CommitImport` intentionally reports a pending row it cannot resolve — an
ambiguous person, or a fact whose person candidate was withdrawn — in `unresolved_ids`. After the commit the page
shows the committed, unresolved, and already-committed counts from `CommitImportResult`. There is no auto-commit,
no commit as a side effect of accepting, and no single control that selects everything and commits it. After every
mutation the page reloads its state from the server rather than patching a local copy.

Reloading alone cannot protect the commit. Another tab, a CLI invocation, or an MCP client may amend or commit a
row between that reload and the confirmation click, and the rows that decide the outcome are not only the accepted
ones: an accepted fact resolves through its person candidate even when that row is unselected. The commit request
therefore carries one digest of the whole batch as the page displayed it — every row's id, status, and candidate
content, in ordinal order. `CommitImport` gains an optional `expected_batch_digest` argument. When it is given,
`CommitImport` recomputes the digest from the stored rows inside its own transaction, and that transaction takes
the SQLite write lock before it reads — `SqliteUnitOfWork(immediate=True)`, the mode source receipts already use
because a deferred `BEGIN` lets another writer act between a read and the decision based on it. A mismatch refuses
the whole commit with `batch_changed` and writes nothing; the page reloads and the reviewer sees what changed. No
writer can change the batch between the comparison and the commit. Nothing is stored for the check, and callers
that omit the argument, including the CLI and `commit_import`, keep today's behaviour.

A withdrawal from a stale view has the same problem, and a worse outcome: withdrawing a row another client just
corrected rejects content the reviewer never saw, and M29 has no reinstatement. `WithdrawStagedCandidates` gains the
same optional `expected_batch_digest`, checked the same way inside its write-locked unit of work before any status
changes, and the "Withdraw selected" action always sends it. M30.3 gives `AmendStagedCandidate` the argument too, so
an edit made from a stale view cannot overwrite another client's amendment.

The size ceilings that bound `pctx import review` apply unchanged; a batch too large to review in the CLI is not
made reviewable by being viewed in a browser.

### M30.3 — Inline edit in the browser

Depends on M29.1. Add editing to the batch page from M30.2.

Each pending row expands into an edit form generated from the candidate type's field list. Scalar strings, dates, the
sensitivity enum, and confidence use the native input for each. List-valued fields have bounded controls. `aliases` is a
list of kind-and-value rows the reviewer can add to and remove from, up to the persisted model's bounds.
`participant_candidate_ids` and `evidence_candidate_ids` are multi-selects offering only rows of the same batch whose
type the batch-wide reference validation accepts, so the control cannot express a reference the use case would refuse.
`evidence_ids` names durable records outside the batch, so the form shows it read-only with the equivalent `pctx import
amend` command rather than inventing a record search. Saving posts a field patch to an endpoint wrapping
`AmendStagedCandidate`, which re-validates and re-resolves the candidate exactly as the CLI amendment path does.
Validation refusals are shown against the field they name, one message per field, without repeating the submitted value.

An ambiguous person candidate gets a picker listing the `match_candidates` M29.1 adds to the review row — the id
and canonical name of up to 10 colliding people — so the user chooses one or leaves it unresolved. When
`match_candidates_truncated` is set, the picker says more people share the name and accepts a person id the
reviewer found through `resolve_person` or `search_people`; `AmendStagedCandidate` validates it against the
matcher's full set either way. The choice is a patch setting `matched_person_id`, not a browser-specific
resolution step.

There is no free-text form for adding a candidate that the importer did not produce: the browser edits what was
staged, it does not author records. Committed and withdrawn rows are not editable.

## Privacy and compatibility

No new durable record, table, migration, or machine JSON format is introduced. The endpoints are HTTP projections of
existing use cases. Two changes are additive: a top-level `truncated` on the version-1 person brief, and an optional
`expected_batch_digest` argument on `CommitImport`, `WithdrawStagedCandidates`, and `AmendStagedCandidate` with its
`batch_changed` refusal, which callers that omit the argument never see. Reads write nothing; mutations flow through the
existing use cases and therefore through the existing transaction, audit, and changelog seam.

Nothing leaves the machine. The page loads no external resource, the process makes no outbound request, and the
ordinary-commands-never-touch-the-network rule holds for `pctx browse` as it does for every other command. A
browser page is a new surface on which recorded data is displayed, so the disclosure rules are the ones already
in force: ordinary reads of durable records, operator elevation from the process environment only, no signal that
withheld records exist, and staged candidates shown as `pctx import review` shows them, under the review disclosure
warning.

What is explicitly not promised: no authentication, no HTTPS, no multi-user separation, and no protection
against another process running as the same user on the same machine. The token and the origin checks defend
against a hostile web page in the browser, not against local code running with the user's own privileges — the
same trust boundary the loopback MCP transport already states.

## Acceptance scenarios and verification

- A bind to any address other than `127.0.0.1` is refused; the ephemeral default and an explicit `--port` both
  serve on loopback only.
- A request with no token, a wrong token, an unexpected `Host`, or a foreign `Origin` is refused with a generic
  response carrying no person, candidate, batch, or file data, and no indication of which check failed.
- The token appears in the printed URL and nowhere else: not in stderr, not in log records, not in refusal
  bodies, not in any file the process writes. This is asserted against the launched server's output, because
  a `TestClient` bypasses the server's own access logger.
- An inline script without the response's nonce is blocked by the Content-Security-Policy; the page's own script
  runs.
- A candidate whose value contains markup renders as text on the review page and in the edit form, and executes
  nothing.
- Sensitive and restricted durable records are absent from the people, person, and sources views without
  elevation and present with `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE` set for the `pctx browse` process; no page
  control changes that state.
- A staged `sensitive` fact candidate appears in the batch view without elevation, exactly as `pctx import review`
  shows it, under the review disclosure warning.
- The people list carries exactly the person-index columns, in the order `pctx list --json` returns them.
- A person with more than `BRIEF_CONTEXT_ITEMS` eligible facts and interactions shows `truncated` in the view,
  in `pctx brief --json`, and in `pctx brief`'s text; a person under the bound shows it unset in all three.
- Each import-sources page matches the same `pctx sources --limit N --cursor C` page, and a receipt's staged counts
  match `pctx source show` for it. A source that committed a `sensitive` fact exposes no mapping, entity id, or mapping
  count, and the response is byte-identical with and without elevation. A pending batch behind many committed receipts
  is reached by paging, not by a scan.
- The review page's row order matches `pctx import review` for the same batch, including withdrawn rows.
- Withdraw and commit refusals display the use-case error code and never the refused payload.
- The commit confirmation names the number of candidates accepted. Accepting an ambiguous person and its fact
  commits neither, and the page then reports both as unresolved from the commit result, with no refusal.
- Amending a checked row, amending an unchecked person row an accepted fact resolves through, or committing a
  checked row from another tab or `pctx import commit`, after display and before confirmation, refuses with
  `batch_changed` and commits nothing; the page then shows the current batch.
- A test that amends a row from a second connection while `CommitImport` holds its write lock observes the
  amendment wait for the commit, and the commit either matches the digest or refuses; there is no interleaving in
  which changed content is committed.
- Withdrawing or editing a row another client amended after display refuses with `batch_changed`, changes no status
  or content, and reloads the view.
- `pctx import commit`, `commit_import`, and the CLI and MCP amend and withdraw paths without
  `expected_batch_digest` behave exactly as before.
- After accept, withdraw, commit, and amend, the page state is refetched from the server and matches a fresh
  `pctx import review` of the same batch.
- An invalid edit is refused per field; a valid edit is visible through `pctx import review` afterwards.
- An interaction's participants, a person's aliases, and a trait's `evidence_candidate_ids` can each be corrected
  from the form; the participant and evidence selectors never offer a row of another batch or the wrong type.
- Choosing a match in the ambiguity picker records the resolution through amendment, visible to the CLI.
- `uv lock --check` reports no change after `starlette` and `uvicorn` are declared in `pyproject.toml`.
- Starlette `TestClient` tests cover every endpoint and every security header, including the refusal paths.
- Repository gates: `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`, and `uv build` for the packaging
  surface. All fixtures use fictional people such as Elena Marsh and Toma Ibarra.

## Alternatives and deferred work

- The Obsidian plugin already browses people for Obsidian users. It is read-only, requires Obsidian, and has no
  review feature. M30 does not replace it; the two shell out to and call the same reads.
- A TUI library would add a dependency and still not give form controls, date inputs, or a selection list that a
  non-terminal user can operate. The browser is the platform that already has those.
- An Electron or native desktop application adds a build pipeline, a packaging story, and a large runtime for a
  page that is three documents of inline HTML.
- Serving HTML routes from the existing MCP HTTP transport would mix an agent transport with a human interface on
  one port and one security policy. A separate short-lived process keeps the token and origin model simple and
  keeps the MCP surface exactly what it is today.
- Flask or FastAPI would be a new dependency for what `starlette`, already resolved beneath `mcp`, does.
- Out of scope, and not planned here: remote or LAN access, any authentication scheme, HTTPS, multi-user
  operation, a background daemon or tray application, a JavaScript framework or build step, editing durable
  records such as people, facts, and reminders from the browser, displaying sensitive records without the
  existing operator elevation, replacing the Obsidian plugin, search, and graphs or visualisations.
