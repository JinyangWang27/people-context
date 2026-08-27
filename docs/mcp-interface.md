# MCP interface

The server uses the official Python MCP SDK and runs over stdio by default. The same `build_server()` can run
unauthenticated Streamable HTTP on `127.0.0.1`; remote/authenticated transport remains out of scope.

## Annotations

- `readOnlyHint=true`: no state mutation; disclosure risk is still governed by each tool's response contract.
- default write annotation: clients should apply normal write approval.
- `destructiveHint=true`: irreversible/restructuring operations (`merge_people`, `forget`).

## Read-only tools

| Tool | Purpose | Main parameters | Result |
|---|---|---|---|
| `resolve_person` | Explainable identity resolution without silent guessing. | `query`, optional org/role/relationship hints, `limit` | Ranked candidates and `ambiguous`. |
| `search_people` | Broader lexical browsing. | `query`, optional filters | Candidate list. |
| `semantic_search` | Optional multilingual cosine retrieval over eligible people/interactions. | `query`, kinds, limit | `ok`, `not_available`, or `model_mismatch`. |
| `get_person_context` | Bounded, sensitivity-gated person context. | `person_id`, optional purpose, `max_items` | Stable `PersonContextResult`. |
| `get_communication_guidance` | Structured communication signals, not generated advice. | `person_id`, optional situation | Traits, relationships, roles, friction notes, reminders, philosophy. |
| `list_reminders` | Pull-based reminder listing. | optional person/due/status filters | Ordered reminders. |
| `get_relationship_graph` | Minimal-disclosure structural neighborhood. | `person_id`, `depth=2`, optional canonical types | Nodes, canonical edges, `truncated`. |
| `find_connection` | One deterministic shortest relationship path. | `person_a`, `person_b`, `max_depth=4` | Ordered perspective-rendered hops or not-connected. |
| `get_stale_relationships` | Recency report over ordinary interactions only. | optional `category`, `threshold_days=90`, `limit=20` | Ordered recency rows and `truncated`. |
| `upcoming_dates` | Ordinary birthdays and dated active reminders in a window. | `window_days=30`, optional `person_id` | Ordered entries and `skipped_unparseable`. |

All ten tools are annotated `readOnlyHint=true`.

## M7 graph contracts

### `get_relationship_graph`

```json
{
  "nodes": [{"person_id": "A", "name": "Alice", "is_self": true}],
  "edges": [{
    "subject_id": "A",
    "object_id": "B",
    "type": "reports_to",
    "label": null,
    "category": "professional"
  }],
  "truncated": false
}
```

Nodes intentionally contain only id, name, and `is_self`; summaries, facts, traits, observations, interactions,
and reminders are not graph data. Edge `type` is canonical. Depth defaults to 2 and is capped at 4; application
caps are 100 nodes and 300 edges. Traversal is separately bounded by a node budget well above the node cap, so
a request cannot cost unbounded work on a dense store. Cap removal, and traversal stopping on its budget, both
set `truncated=true`.

Unknown or soft-deleted roots return:

```json
{"error": "person_not_found", "person_id": "..."}
```

### `find_connection`

A connected result contains one hop for each traversed edge. Each hop contains the destination person and the
canonical edge plus `display_type` from the previous person's perspective:

```json
{
  "connected": true,
  "hops": [{
    "person": {"person_id": "B", "name": "Bob", "is_self": false},
    "edge": {
      "subject_id": "A",
      "object_id": "B",
      "type": "reports_to",
      "display_type": "reports_to",
      "label": null,
      "category": "professional"
    }
  }],
  "reason": null
}
```

Disconnected result: `{"connected": false, "hops": [], "reason": "not_connected"}`. Unknown/deleted endpoint
ids use the same structured `person_not_found` shape as graph lookup.

## M13 recency contract

### `get_stale_relationships`

```json
{
  "people": [{
    "person_id": "A",
    "name": "Alice",
    "categories": ["professional", "social"],
    "last_interaction_at": "2026-03-01T00:00:00Z",
    "days_since": 140,
    "interaction_count": 12
  }],
  "truncated": false
}
```

The report covers active, non-deleted people other than the self identity, whose relationship-to-self is
undefined. `categories` lists the deduplicated categories of the relationships that are active today between
that person and self; a person with no relationship to self reports `[]`.

Only `public`/`personal` interactions participate. A person whose interactions are all `sensitive`/`restricted`
is indistinguishable from a person with none: `last_interaction_at` is `null`, `days_since` is `null`, and
`interaction_count` is `0`. No summaries, channels, or other interaction content are returned.

A person is reported when `last_interaction_at` is `null`, or when the signed calendar age
`days_since = clock.now().date() - last_interaction_at.date()` is at least `threshold_days`. A future timestamp
yields a negative `days_since`, which is neither clamped nor reported as stale. Rows are ordered by null
interaction first, then oldest interaction, normalized name, and id; `limit` is applied after filtering and
ordering, and `truncated` becomes `true` when further qualifying rows exist.

Stored interaction timestamps keep whatever UTC offset the writer supplied, and some rows are naive, so the two
comparisons are deliberately different:

- **Selecting the latest interaction, and ordering the report, compare instants.** An aware timestamp is
  converted to UTC and a naive one is read as UTC — never in the host timezone. Text comparison would otherwise
  rank `2026-06-01T23:30:00-05:00` before `2026-06-02T02:00:00+00:00` despite it being the later instant.
- **`days_since` uses the stored timestamp's own calendar date**, so the age always agrees with the
  `last_interaction_at` the response carries. Normalizing here would let a response report a 31 May interaction
  and simultaneously call it zero days old.

`last_interaction_at` is always the stored value, at its original offset and precision. Normalization decides
comparisons; it never rewrites what is returned.

`threshold_days` accepts `0..36500` and `limit` accepts `1..100`. An out-of-range value returns
`{"error": "invalid_parameter", "message": "..."}` rather than a partial report. An optional `category` is
normalized with the shared relationship vocabulary rules, so `"Professional"` and `"professional"` select the
same rows.

## M13 upcoming-dates contract

### `upcoming_dates`

```json
{
  "entries": [{
    "person_id": "A",
    "name": "Alice",
    "kind": "birthday",
    "date": "2026-06-10",
    "label": "Birthday"
  }],
  "skipped_unparseable": 0
}
```

The report covers the inclusive interval `[today, today + window_days]`, where `today` is `clock.now().date()`
and `window_days` accepts `0..366` (default 30). A window of `0` reports today only. An out-of-range value
returns `{"error": "invalid_parameter", "message": "..."}` rather than a partial report. The optional
`person_id` restricts the report to one person.

`kind` is `"birthday"` or `"reminder"`. `label` is the constant `"Birthday"` for a birthday and the reminder's
own text for a reminder, so a birthday entry discloses the upcoming date without disclosing the stored birth
year. Entries are ordered by date, then normalized name, person id, kind, label, and the source record id, so
repeated calls against unchanged data return byte-identical output.

Two record types contribute:

- **Birthday facts** qualify only when the fact's `sensitivity` is `public` or `personal`, its `predicate` is
  exactly `birthday`, and its `value` is `YYYY-MM-DD` or `--MM-DD`. Both forms are annual recurrences: the
  month/day is projected to the earliest real occurrence on or after today. 29 February is never coerced to
  28 February or 1 March — a common year simply has no occurrence, so the next occurrence is the next actual
  leap day, which may fall outside every accepted window.
- **Active reminders** with a `due_at` contribute their stored calendar-date component. This report never
  reinterprets a naive stored datetime as a timezone, and never converts an aware one, so an entry always
  carries the calendar day the reminder was written with. Undated reminders (communication notes) and
  completed or cancelled reminders contribute nothing.

`skipped_unparseable` counts ordinary birthday facts whose value is not one of the two accepted forms —
including impossible dates such as `1985-02-29` or `--02-30`. Sensitive and restricted facts are invisible:
they contribute neither entries nor skip counts, so the count itself cannot signal that an elevated birthday
exists. Missing and soft-deleted people are skipped deterministically, which also removes any reminder pointing
at them.

## M15 resolution-detail contract

### `resolve_person`

Each candidate carries one additive field alongside the unchanged `match_reason`:

```json
{
  "query": "王小明",
  "candidates": [{
    "person_id": "A",
    "canonical_name": "Wang Xiaoming",
    "score": 1.0,
    "match_reason": "exact",
    "match_detail": "alias:native_script",
    "aliases": ["王小明"],
    "summary": null
  }],
  "ambiguous": false
}
```

`match_detail` is `"canonical_name"` when the query normalizes to the person's canonical name, `"alias:<kind>"`
when it normalizes only to an alias, and `null` for search-stage, fuzzy-stage, and `search_people` candidates.
When several stored values normalize to the query, the canonical name wins; otherwise the matching alias with
the lowest `(kind, id)` pair supplies the detail. The field is descriptive rather than a closed enum, so treat an
unrecognized `alias:<kind>` value as "matched via some alias". Scores, `match_reason`, ordering, the acceptance
threshold, and `ambiguous` are unchanged, and the detail names an alias *kind* only — never an alias value the
match did not already implicate. See [identity-resolution.md](identity-resolution.md#match_detail-which-stored-name-matched).

## Person context compatibility

M7 does not change existing relationship fields. Each hydrated relationship object adds one field:

```json
{
  "relationship": {
    "id": "relationship-id",
    "subject_id": "A",
    "object_id": "B",
    "type": "reports_to",
    "label": null,
    "period": {"valid_from": null, "valid_to": null},
    "confidence": 1.0,
    "provenance": {"source": "user", "session": null, "stated_by": null},
    "created_at": "2026-01-01T00:00:00Z"
  },
  "other_person_id": "B",
  "other_person_name": "Bob",
  "display_type": "reports_to"
}
```

The opposite endpoint sees `manages`. Symmetric and uncategorized relationships use their stored type.
Resolution, search, context budgets, sensitivity behavior, and all pre-M7 response fields remain unchanged.

## Write tools

| Tool | Purpose |
|---|---|
| `remember_person` | Create/update a person and merge aliases/summary. |
| `add_alias` | Add a normalized-deduplicated alias. |
| `set_relationship` | Normalize vocabulary/direction and create or update one canonical active edge. |
| `set_affiliation` | Create a role at an existing or get/created organization. |
| `record_fact` | Record an objective time-aware fact. |
| `record_observation` | Record a subjective observation. |
| `record_trait` | Record a derived categorized trait. |
| `record_interaction` | Record a concise summary after participant validation. |
| `correct_record` | Correct whitelisted fields with lossless before/after audit. |
| `set_reminder` / `complete_reminder` | Create and transition reminders. |
| `set_communication_philosophy` | Store user-authored guidance; audit stores lengths, not text. |
| `import_content` / `stage_candidates` / `review_import` / `commit_import` | Reviewable distilled imports without raw source retention. |

`import_content(source_type, content, path, self_sender)` accepts `email`, `mbox`, `vcard`, `ics`, `linkedin`,
`outlook`, and `whatsapp`. M14 added the last two source values plus the optional `self_sender` chat-export
label for the user; both are additive and the response shape is unchanged. See
[docs/import.md](import.md).

`stage_candidates(source, candidates)` accepts `person`, `interaction`, `affiliation`, and `fact`, and — from
M17 — the additive `observation`, `trait`, and `relationship` types. A request using one of the three new types
opts into a bounded contract: at most 500 candidates, a normalized 128-character `source`, 1 MiB of serialized
candidate JSON, and 8 KiB per string on every candidate including the legacy ones, plus tighter per-field
limits. Such a request also stages an additive `match_disposition` of `unmatched`/`matched`/`ambiguous` on each
person candidate, so ambiguity cannot be mistaken for a new identity. A request built only from the four
released types keeps its pre-M17 accepted shape and matching behavior; the `review_import` and `commit_import`
envelopes are unchanged. See [docs/import.md](import.md#agent-extracted-knowledge-m17).

M18.1 adds optional receipt metadata to both staging tools' responses and, on `stage_candidates`, to its
arguments: `source_kind`, `content_digest`, `extraction_fingerprint`, `label`, and `external_source_id`. All are
optional and omitting every one keeps the released behaviour exactly. `source_kind` is a bounded machine category
— never a person or a title — and recording one creates an import receipt; a caller-computed `content_digest`
additionally gives that receipt a duplicate claim, so re-staging the same artifact reports the existing batch
instead of copying it. Without a digest the session deliberately asserts no claim, because People Context never
hashes text it was not given. The staging response gains the additive `source_session_id` and `duplicate` fields;
`import_content` gains the same two on a path-based import. A duplicate is reported, not raised: the response
describes the batch that already exists. See
[docs/import.md](import.md#source-receipts-and-repeat-imports-m181).

`set_relationship` accepts free-form type input. M7 snake-case normalizes it, resolves synonyms, canonicalizes
inverse direction, orders symmetric endpoints, and updates an existing active canonical edge instead of
inserting a duplicate. Unknown types remain legal. Staged `relationship` candidates commit through this same
contract, and are ordinary-disclosure only because the durable relationship model carries no sensitivity
field.

## Destructive tools

`merge_people` atomically re-parents linked rows, removes resulting self-loops, preserves the duplicate name as
a former-name alias, and soft-deletes the duplicate. `forget` atomically hard-deletes a person graph or one
record and redacts identifying audit/changelog history according to the M6 tombstone contract.

## Operator-elevated reads

`get_sensitive_person_context` is registered only with `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE=1`.
`export_data` is registered only with `PEOPLE_CONTEXT_MCP_ENABLE_EXPORT=1`; ordinary full JSON export remains
available through the CLI.

The Obsidian vault export is CLI-only. M7 intentionally adds no MCP tool that writes arbitrary directories.
See [vault-export.md](vault-export.md).

## Compatibility

Tool names, required parameters, and existing response fields are stable within a major version; new parameters
are optional with compatible defaults and new response fields are additive. Clients should ignore unknown fields.
See [compatibility.md](compatibility.md) for the complete promise across MCP, database, CLI, and machine-readable
JSON surfaces.
