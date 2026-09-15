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
independently mergeable, and each later PR only adds pages and endpoints to the process the first one starts.
This specification delivers no code.

## Shared foundation and security model

M30.1 owns the foundation below; M30.2 and M30.3 reuse it unchanged and add nothing to it.

`pctx browse [--open] [--port N]` starts a Starlette application under uvicorn, bound to `127.0.0.1` on an
ephemeral port unless `--port` names one, prints the URL carrying a per-launch token, optionally opens that URL
with the standard-library `webbrowser`, and exits on Ctrl-C or when the page posts that it is done. There is no
daemon, no configuration file, no PID file, and no state that survives the process. Starting it twice starts two
unrelated processes with two unrelated tokens.

Each view is one HTML document with its CSS and JavaScript inline, served from Python string constants or
package data. No bundler, no Node toolchain, no CDN, no external font, and no third-party script: everything the
browser executes was reviewed in this repository, and a page that fails to load a resource cannot exist because
there is nothing external to load. `Content-Security-Policy: default-src 'self'` is sent on every HTML response,
with the inline script admitted by a nonce generated per response — not by `'unsafe-inline'`, which would forfeit
the protection the header is being sent for.

The security model is identical in all three PRs:

- Loopback bind only. There is no `--host`; a request to bind anything other than `127.0.0.1` is refused rather
  than honoured. Remote access, LAN access, and tunnelling remain out of scope, as they are for the MCP server.
- A per-launch token from `secrets.token_urlsafe` is required on every request: as a query parameter on the
  first load, then as a request header the page sets on every subsequent request. It is compared with
  `secrets.compare_digest`. It is never written to the database, a file, a log line, or an error message; the
  printed URL on stdout is the only place it appears.
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

Disclosure parity is part of the foundation. The browser's ordinary reads apply the same sensitivity policy as
ordinary MCP reads: no `sensitive` or `restricted` material, no signal that a hidden record exists. Sensitive
material appears only when `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE` is set in the environment of the `pctx browse`
process itself, through the existing `process_elevation_enabled` rule, exactly as the MCP server consults it. The
page never offers a toggle, a checkbox, or a URL parameter for that: elevation is an operator decision made
before the process starts, and a decision the page could change would be a decision an open browser tab could
change.

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

Deliver `pctx browse`, the foundation above, and three read-only pages.

- People list: name, relationship to self, and last interaction, from the same read the `pctx list --json`
  command uses. Rows link to the person page.
- Person page: the `brief` content — summary, affiliations, facts, interactions, reminders, and traits — from the
  same use case behind `pctx brief` and `get_person_context`, with the same section bounds and the same
  `truncated` flags. A section truncated for the CLI is truncated here, and says so.
- Pending batches list: batch id, source label, staged time, and candidate counts, read from the source receipts
  that `pctx sources` and `ListImportSources` already expose.

JSON endpoints wrap the existing read use cases and return what those use cases return. There is no new read
model, no cache, no search index, and no semantic search. Every page is read-only: no page carries a form, and
no endpoint in this PR writes. The batches list shows batch identity and counts only, and links into M30.2's
review page once that PR exists.

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

Commit requires one explicit click on a confirmation naming the number of candidates that will be committed.
There is no auto-commit, no commit as a side effect of accepting, and no single control that selects everything
and commits it. After every mutation the page reloads its state from the server rather than patching a local
copy, so what is displayed is what the store holds and a stale tab cannot act on a view the store has moved past.
The size ceilings that bound `pctx import review` apply unchanged; a batch too large to review in the CLI is not
made reviewable by being viewed in a browser.

### M30.3 — Inline edit in the browser

Depends on M29.1. Add editing to the batch page from M30.2.

Each pending row expands into an edit form generated from the candidate type's field list: strings, dates, the
sensitivity enum, and confidence, each rendered with the native input type for it. Saving posts a field patch to
an endpoint wrapping `AmendStagedCandidate`, which re-validates and re-resolves the candidate exactly as the CLI
amendment path does. Validation refusals are shown against the field they name, one message per field, without
repeating the submitted value.

An ambiguous person candidate gets a picker listing the matcher's candidates with enough context to tell them
apart, so the user chooses one or leaves it unresolved. The choice is recorded through the same amendment path
M29.1 defines, not through a browser-specific resolution step.

There is no free-text form for adding a candidate that the importer did not produce: the browser edits what was
staged, it does not author records. Committed and withdrawn rows are not editable.

## Privacy and compatibility

No new durable record, table, migration, or machine JSON contract is introduced. The endpoints are HTTP
projections of existing use cases, and the versioned envelopes documented for integrations are untouched.
Reads write nothing; mutations flow through the existing use cases and therefore through the existing
transaction, audit, and changelog seam.

Nothing leaves the machine. The page loads no external resource, the process makes no outbound request, and the
ordinary-commands-never-touch-the-network rule holds for `pctx browse` as it does for every other command. A
browser page is a new surface on which recorded data is displayed, so the disclosure rules are the ones already
in force: ordinary reads only, operator elevation from the process environment only, no signal that withheld
records exist, and the review disclosure warning wherever staged candidates are shown.

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
  bodies, not in any file the process writes.
- An inline script without the response's nonce is blocked by the Content-Security-Policy; the page's own script
  runs.
- A candidate whose value contains markup renders as text on the review page and in the edit form, and executes
  nothing.
- Sensitive and restricted records are absent from every page without elevation and present with
  `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE` set for the `pctx browse` process; no page control changes that state.
- Person-page section bounds and `truncated` flags match `pctx brief` for the same person.
- The pending-batches list matches `pctx sources` for the same database.
- The review page's row order matches `pctx import review` for the same batch, including withdrawn rows.
- Withdraw and commit refusals display the use-case error code and never the refused payload.
- The commit confirmation names the count that is committed, and the count committed equals it.
- After accept, withdraw, commit, and amend, the page state is refetched from the server and matches a fresh
  `pctx import review` of the same batch.
- An invalid edit is refused per field; a valid edit is visible through `pctx import review` afterwards.
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
