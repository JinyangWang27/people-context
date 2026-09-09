# M23 — Explainable person briefs and history

Status: Planned. See [docs/roadmap.md](../roadmap.md#m23--explainable-person-briefs-and-history).

## Purpose

Extend the existing person brief with explicitly requested history, assembled from existing durable records.
A biography is a presentation of attributed knowledge, uncertainty, and change, not another durable source of truth.
The brief must explain the limits of what it can show instead of suggesting it contains a complete personal history.

Reuse the delivered [brief](../cli.md#person-brief) and
[timeline](../mcp-interface.md#get_person_timeline) contracts. Historical M19 planning status need not be rewritten
to use those delivered capabilities.

## Planned PR

### M23.1 — Add explicitly requested history to briefs

Add an opt-in history section to the existing brief composition and CLI/export surface. A conceptual invocation is:

```text
pctx brief PERSON --include-history [--history-limit N] [--include-sensitive] [--json]
```

History is off by default. When requested, compose it from the existing timeline with its default limit of **50**
and accepted range **1–200**. A limit alone must not silently enable history; document and validate option
combinations consistently. Do not add a second history query, separate profile model, new table, or all-purpose
profile MCP endpoint.

Preserve current brief defaults, context budgets, existing JSON fields and their meanings, and the versioned
`people-context-brief` compatibility contract. Add history and its metadata only additively. The representation must
distinguish “not requested” from “requested but empty” and report the requested bound, disclosure, and truncation.
No history query runs for an ordinary brief that did not request it.

### What the history explains

- Keep each timeline entry's stable record type and id, concise display fields, validity bounds, timestamp basis,
  available source-session reference, and readable evidence references. Reuse deterministic timeline ordering.
- Distinguish recorded assertions from subjective observations and inferred traits. Display available confidence,
  attribution, and evidence where the existing composed reads supply them; do not manufacture missing attribution
  or turn an import receipt into independent confirmation.
- Show validity periods and identify whether a displayed timestamp is an event date, validity start, record creation,
  recording date, or trait update. Preserve the timeline's `basis` rather than presenting all timestamps as events.
- Preserve approximate or unknown dates in claim text. A record placed by `recorded_at` does not establish when a
  year-only educational qualification or an undated historical role actually occurred.
- Expose history `truncated` and per-entry `evidence_truncated` using existing disclosure semantics. A bounded page,
  missing receipt, or absent readable evidence never proves that no other history or evidence exists.
- Source references describe available receipts and durable evidence, not raw document contents or a complete audit
  of every change. A receipt may identify an import that touched a record rather than originally created it.

Agents composing a profile continue to use existing MCP reads such as `get_person_context`, `get_person_timeline`,
`get_consolidation_context`, and purpose-specific guidance. Explain which available records support a narrative;
do not persist that narrative into an unrestricted summary as a parallel authority.

## Privacy and compatibility

Apply disclosure independently to records and their evidence, before bounding the readable page. An ordinary trait
must not name restricted evidence, and hidden evidence must not set a truncation flag that reveals its existence.
Affiliations and relationships remain ordinary by construction because they lack sensitivity controls; history
cannot retroactively protect sensitive details incorrectly stored there.

The local brief's explicit `--include-sensitive` may widen requested history through the existing local timeline
policy; label that history disclosure separately. Preserve current context disclosure and guidance's permanently
ordinary policy. MCP reads retain their existing ordinary disclosure and gain no elevated access through this work.
Every exported brief retains the notice that rendered plaintext is outside server disclosure controls.

Reads remain free of domain, audit, and changelog mutations. Existing brief fields must not be repurposed to carry
history or silently expose older records under their current meanings. Preserve existing person resolution and
not-found/ambiguity behavior.

## Acceptance scenarios and verification

- An ordinary brief retains its existing fields, defaults, content selection, and disclosure, without a history
  query. Explicit history adds one bounded section; JSON distinguishes an empty section from no history request.
- Limits default to 50, accept 1 and 200, and reject values outside 1–200. A longer readable history sets truncation;
  both human and machine output make the incomplete coverage apparent.
- An attributed CV assertion, a subjective observation, and a derived trait remain distinguishable, with available
  evidence and source references preserved. Repeated CV receipts do not become independent corroboration.
- Exact validity periods remain visible. Year-only and approximate claims retain their wording; recording a missing-
  date historical role today does not render today as the employment start date.
- Conflicting CV assertions, concurrent roles, and historically correct superseded values stay visible when they
  fall within the readable page. Composition neither selects a winner nor rewrites those records.
- Ambiguous identity produces the existing resolution outcome instead of mixing histories. A bounded or partially
  readable source history is not presented as an exhaustive biography.
- A visible trait supported by restricted evidence exposes neither that evidence's id nor a hidden-evidence
  truncation signal. Sensitive history requires explicit local disclosure, and guidance remains ordinary even then.
- If records change between reads, a brief remains a generated view of available records, not an approval token or
  an atomic cross-tool snapshot. Any later update must reread affected targets under
  [M24](m24-reviewed-person-updates.md); generating the brief changes no stored history.
- Focused composition/rendering tests cover bounds, timestamp basis, additive JSON compatibility, separate record
  and evidence disclosure, and read-only behavior. Reuse timeline checks rather than implementing a parallel suite
  for a second timeline algorithm; run required repository checks for the implementation PR.

## Dependencies and deferred work

M23 independently reuses delivered brief and timeline capabilities; it does not wait for M22 or M24. M22's attributed
claims improve future presentations but are not required for history to work on existing records.

Structured partial-date storage, dedicated life events, automated belief revision, semantic deduplication, and
generic batch mutation are deferred. This milestone adds no stored biography, raw-document retrieval, automatic
personality assessment, complete audit reconstruction, or new all-purpose profile endpoint.
