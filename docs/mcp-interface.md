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
| `get_person_context` | Bounded, sensitivity-gated person context. | `person_id` or `person` (a name), optional purpose / `include_communication`, `max_items` | Stable `PersonContextResult` with additive `withheld` counts. |
| `get_communication_guidance` | Structured communication signals, not generated advice. | `person_id` or `person`, optional situation | Traits, relationships, roles, friction notes, reminders, philosophy. |
| `list_reminders` | Pull-based reminder listing. | optional `person_id` or `person`, due/status filters | Ordered reminders. |
| `get_relationship_graph` | Minimal-disclosure structural neighborhood. | `person_id` or `person`, `depth=2`, optional canonical types | Nodes, canonical edges, `truncated`. |
| `find_connection` | One deterministic shortest relationship path. | `person_a`, `person_b`, `max_depth=4` | Ordered perspective-rendered hops or not-connected. |
| `get_stale_relationships` | Recency report over ordinary interactions only. | optional `category`, `threshold_days=90`, `limit=20` | Ordered recency rows and `truncated`. |
| `upcoming_dates` | Ordinary birthdays and dated active reminders in a window. | `window_days=30`, optional `person_id` or `person` | Ordered entries and `skipped_unparseable`. |
| `get_person_timeline` | Bounded newest-first chronology of one person's durable records. | `person_id` or `person`, `limit=50` | Ordered entries, `found`, and `truncated`. |
| `get_consolidation_context` | Bounded maintenance evidence: what is stored about one person and how it relates. | `person_id` or `person`, `limit=50` | Facts, traits, observations, deterministic `signals`, and per-collection truncation. |

| `review_import` | Staged candidates and statuses for one batch. | `batch_id` | Candidate rows; inspection only. |

All thirteen tools are annotated `readOnlyHint=true`. `review_import` was annotated as a write before M21 even
though it never mutated anything; correcting the annotation removes a spurious approval prompt.

### Naming a person instead of passing an id

Every ordinary read above that takes a single `person_id` — `get_person_context`, `get_communication_guidance`,
`list_reminders`, `get_relationship_graph`, `get_person_timeline`, `get_consolidation_context`, and
`upcoming_dates` — also accepts `person`: the name, nickname, or alias as the user said it. `find_connection` and
the operator-elevated `get_sensitive_person_context` take ids only. The tool resolves it through the same `resolve_person` pipeline and applies the same contract — an
`ambiguous` resolution returns `{"error": "ambiguous_person", "candidates": [...]}` and reads nothing, no match
returns `{"error": "person_not_found"}`, and neither argument returns `{"error": "missing_person"}`. Passing
`person_id` keeps the previous behavior exactly; `person` is additive and saves the resolve round-trip when the
name is unambiguous.

### `withheld`

`get_person_context` adds one additive field:

```json
{"withheld": {"sensitive": 1, "restricted": 0, "truncated": false}}
```

`sensitive` and `restricted` count facts, interactions, and (when communication traits were requested) traits
that exist but were not disclosed at the caller's level; `truncated` says the shared facts/interactions budget
cut the ranked list. Counts only — never a predicate, value, or id — so an agent can say "something is withheld"
instead of "nothing is stored". The elevated `get_sensitive_person_context` returns zero counts for what it
discloses.

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

## M19 person-timeline contract

### `get_person_timeline`

```json
{
  "found": true,
  "person_id": "A",
  "limit": 50,
  "include_sensitive": false,
  "entries": [{
    "entry_type": "interaction",
    "entry_id": "01J...",
    "person_id": "A",
    "effective_at": "2026-05-01T09:00:00+00:00",
    "basis": "occurred_at",
    "summary": "quarterly sync",
    "detail": "video",
    "sensitivity": "personal",
    "valid_from": null,
    "valid_to": null,
    "source_session_id": "01J...",
    "evidence": [],
    "evidence_truncated": false
  }],
  "truncated": false
}
```

The timeline is a projection over durable records — interactions the person attended, observations, facts,
affiliations, relationships, and traits — assembled per call. It is not an event store, not an audit dump, and
it writes nothing: no audit row, no changelog row, no durable state changes when it is read.

`entry_id` is the durable record's own id, so an entry stays resolvable through the ordinary reads, which apply
their own disclosure rules. `summary` and `detail` are the record's own display components: an interaction's
summary and channel, an observation's text, a fact's predicate and value, an affiliation's role and
organization, a relationship's type and counterpart, a trait's category and value. No raw import material
appears, because none is stored.

`basis` names the stored field `effective_at` came from, which is what keeps "this happened then" distinct from
"this was written down then":

- `occurred_at`, `observed_at`, `updated_at` — an interaction, observation, or trait's own time;
- `valid_from` — the date a fact, affiliation, or relationship began to hold. A date has no time of day, so it
  is placed at `00:00:00Z`, the same convention M9.2 fixed for all-day calendar values; the entry also carries
  `valid_from`/`valid_to` so the date granularity is not lost;
- `recorded_at`, `created_at` — the record asserts no start date and is placed at the time it was written down.
  An undated record is neither dropped nor given an invented timestamp.

Entries are ordered by `effective_at` descending, then `entry_type`, then `entry_id`, so one database always
produces one order. Ordering compares instants at the stored precision: a stored timestamp keeps whatever offset
its writer supplied, an aware value is converted to UTC and a naive one is read as UTC — never in the host
timezone — and two records in the same second are separated by their microseconds rather than collapsed into a
tie. The stored value is what the response carries; normalization decides comparisons and never rewrites what is
returned.

`limit` accepts `1..200` and defaults to 50. An out-of-range value returns
`{"error": "invalid_parameter", "message": "..."}` rather than a partial page. `truncated` is `true` when more
entries exist below the page. An unknown or soft-deleted person returns `{"found": false, "entries": []}` rather
than an error.

Disclosure is the ordinary rule and this tool has no elevated variant: only `public`/`personal` records
participate, and `include_sensitive` is always `false` here. The local `pctx timeline --include-sensitive`
is the explicit human-operated opt-in. A `null` `sensitivity` is not an unknown level: affiliations and
relationships carry no disclosure field in the durable contract and are ordinary by construction. A relationship
is rendered from this person's side, exactly as `get_person_context` renders it, and an edge to a soft-deleted
person is omitted.

`source_session_id` is the M18 receipt of the earliest import whose committed candidate resolved to this
record — "which import wrote this down", which is not always "which import created it". A relationship an import
matches to an existing edge is updated in place and still earns a candidate mapping, so an edge entered by hand
can name the import that later touched it; only a record no import ever committed onto carries `null`. A record
several imports touched stays one entry naming the earliest.

`evidence` names the M18.3 durable records a trait rests on, each as an `evidence_type`/`evidence_id` pair. The
type is part of the citation because ids are unique only within their own table: a restored store may hold an
observation and an interaction under one id, and a bare id could not tell them apart. Citations are filtered by
*that evidence's* own disclosure level rather than the trait's — a personal trait may rest on a restricted
observation, and naming it would disclose that the record exists — and a citation whose record cannot be read is
omitted rather than named. `evidence_truncated` is `true` when more *readable* citations exist than one entry
reports; it is never set by links the caller may not see, because a visible trait answering with no citations
and a truncation flag would itself prove that hidden evidence exists.

## M19 consolidation contract

### `get_consolidation_context`

```json
{
  "found": true,
  "person_id": "A",
  "limit": 50,
  "include_sensitive": false,
  "facts": [{
    "fact_id": "01J...",
    "predicate": "employer",
    "value": "Acme",
    "valid_from": "2024-01-01",
    "valid_to": null,
    "recorded_at": "2024-01-05T09:00:00+00:00",
    "confidence": 1.0,
    "sensitivity": "personal",
    "provenance": {"source": "agent", "session": null, "stated_by": null},
    "source_session_id": null
  }],
  "traits": [{
    "trait_id": "01J...",
    "category": "communication_style",
    "value": "evidence-led",
    "evidence_note": "derived from the March review",
    "confidence": 0.6,
    "updated_at": "2026-03-02T09:00:00+00:00",
    "sensitivity": "personal",
    "provenance": {"source": "agent", "session": null, "stated_by": null},
    "source_session_id": null,
    "evidence": [{"evidence_type": "observation", "evidence_id": "01J..."}],
    "evidence_truncated": false
  }],
  "observations": [{
    "observation_id": "01J...",
    "text": "asked for numbers before agreeing",
    "observed_at": "2026-03-01T09:00:00+00:00",
    "sensitivity": "personal",
    "provenance": {"source": "agent", "session": null, "stated_by": null},
    "source_session_id": null,
    "cited_by_trait_ids": ["01J..."]
  }],
  "signals": [{
    "kind": "contradictory_fact",
    "entity_type": "fact",
    "key": "employer",
    "entity_ids": ["01J...", "01J..."]
  }],
  "facts_truncated": false,
  "traits_truncated": false,
  "observations_truncated": false,
  "signals_truncated": false
}
```

This read exists to be *read before proposing*. It gathers what the store now holds about one person, the
provenance and evidence behind it, and the places it may say the same thing twice — the material a maintenance
proposal needs. It writes nothing: no audit row, no changelog row, no durable state changes when it is called.

Each record type carries its own page of `limit` rows and its own truncation flag, so a person with four hundred
imported observations is not reported as having no facts worth looking at. `limit` accepts `1..200` and defaults
to 50; an out-of-range value returns `{"error": "invalid_parameter", "message": "..."}` rather than a partial
page. An unknown or soft-deleted person returns `{"found": false}` with every collection empty. Facts are ordered
newest first by asserted `valid_from`, or by `recorded_at` when they assert none — the same placement
`get_person_timeline` uses, so one `limit` describes one window across both reads. Traits are ordered by
`updated_at` and observations by `observed_at`, each with the id breaking an exact tie.

`signals` relates two records that share a normalized predicate or category. It reports relations, never
verdicts: it does not decide which record is right, does not merge anything, and does not score a trait by
counting the rows that support it.

| `kind` | Meaning |
|---|---|
| `duplicate_fact` | Same predicate and value over days both facts cover. |
| `restated_fact` | Same predicate and value over days that do not meet. |
| `contradictory_fact` | Same predicate, different values, over days both cover. |
| `succeeding_fact` | Same predicate, different values, over days that do not meet — what a well-formed supersession leaves behind. |
| `duplicate_trait` | Same category and value. |
| `divergent_trait` | Same category, different values. |

Comparison uses the project's own name normalization (NFKC, casefold, combining marks stripped, whitespace
collapsed) and the domain's inclusive `ValidityPeriod` overlap — the same overlap M15's `doctor` decides fact
conflicts with, so the two surfaces cannot disagree about what "at the same time" means. Nothing here reaches for
an embedding or a similarity threshold; an agent wanting a semantic reading has the bounded text in front of it.
`entity_ids` always holds exactly two ids in ascending order, so a pair is reported once. Signals are ordered by
`entity_type`, then `key`, then those ids, and capped at 200 with `signals_truncated` saying so — one dense group
must not turn a bounded page into a quadratic response. Because the cap cuts that one order where it stands, a
cap reached inside an early group leaves later groups unreported; the flag is what says so, and a narrower
`limit` is how a caller looks at one part of a crowded person at a time.

`cited_by_trait_ids` is the reverse of the traits' own M18.3 evidence links, restricted to traits on this page.
It is what lets a reader tell three observations that independently support one trait from three copies of one
event; M19 leaves that judgement, and any change to a trait's confidence, to the user.

Every record carries its own stored `provenance` — `source`, `session`, `stated_by` — because a maintenance
proposal is an argument about which of two records to believe, and who asserted each is half of that argument.
`source_session_id` is a *different* fact and no substitute for it: it names an M18 import receipt when one
exists and is `null` for everything recorded directly, so a trait entered by hand would otherwise arrive with
nothing to argue from. These are the same records, at the same levels, that `get_person_context` already returns
with their provenance attached; the record's own sensitivity decides whether the row appears at all.

Disclosure is the ordinary rule and this tool has no elevated variant: only `public`/`personal` records
participate, `include_sensitive` is always `false` here, and a trait names only evidence readable at that level.
Filtering happens in the SQL read rather than after it, so an elevated record can neither displace an ordinary
one from the page nor change a signal it does not appear in. No raw source material is returned, because none is
stored — a receipt says that material was processed, never what it said.

## M19 fact-supersession contract

### `supersede_fact`

```json
{
  "superseded": {"id": "01J...", "value": "Acme", "period": {"valid_from": "2024-01-01", "valid_to": "2026-06-30"}},
  "replacement": {"id": "01K...", "value": "Globex", "period": {"valid_from": "2026-07-01", "valid_to": null}},
  "transaction_id": "01K..."
}
```

`supersede_fact(fact_id, new_value, effective_from, confidence?, sensitivity?)` records a **temporal transition**:
the stored value was correct, and then the real-world state changed. `correct_record` remains the tool for a
value that was simply wrong, and must not be repurposed to overwrite a historically correct one — doing so erases
the fact that the old value was ever the case.

The old fact keeps its person, predicate, value, provenance, and `recorded_at`; only its `valid_to` moves, to the
day before `effective_from`, because `ValidityPeriod` endpoints are inclusive. The replacement is a new fact for
the same person and predicate, valid from `effective_from`, and **inherits the old assertion's original
`valid_to`**: `[2026-01-01, 2026-12-31]` superseded on `2026-07-01` yields an old fact through `2026-06-30` and a
replacement valid `[2026-07-01, 2026-12-31]`. An originally open-ended fact stays open-ended, and a bounded one is
never silently widened. M19 adds no second endpoint argument; the person, the predicate, and the replacement's end
date cannot be changed here. `confidence` and `sensitivity` are the replacement's own and inherit the old fact's
when omitted.

`effective_from` must describe a transition while the old fact still held: strictly after any `valid_from`, and
not after any `valid_to`. A date that does not is refused rather than clamped:

```json
{"error": "invalid_supersession", "fact_id": "01J...", "reason": "effective_from_after_valid_to"}
```

`reason` is a stable machine code — `effective_from_not_after_valid_from`, `effective_from_after_valid_to`, or
`effective_from_has_no_prior_day` — and carries no stored value. An unknown id returns the shared
`record_not_found` payload; a fact whose person has been removed returns `person_not_found`.

Both durable rows, both audit rows, and both changelog rows commit as one unit of work, or none do. The two
row-level replay effects carry **the same non-empty `transaction_id`**, which the response returns. SQLite
atomicity alone is not grouping metadata: the sync contract defines `transaction_id` as the key tying together
every effect of one logical transaction, so without it replay and inspection would describe one indivisible
supersession as two unrelated changes. The closure is recorded under its own `supersede` op kind rather than
`correct`, so a replayer and an inspector can tell a transition from an in-place repair.

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
| `remember` | One statement about one person in one call: resolve, create if new, record a fact/trait/interaction/affiliation/relationship, commit once. |
| `remember_person` | Create/update a person and merge aliases/summary. |
| `add_alias` | Add a normalized-deduplicated alias. |
| `set_relationship` | Normalize vocabulary/direction and create or update one canonical active edge. |
| `set_affiliation` | Create a role at an existing or get/created organization. |
| `record_fact` | Record an objective time-aware fact. |
| `record_observation` | Record a subjective observation. |
| `record_trait` | Record a derived categorized trait. |
| `record_interaction` | Record a concise summary after participant validation. |
| `correct_record` | Correct whitelisted fields with lossless before/after audit. |
| `supersede_fact` | Close a historically correct fact and open its replacement, atomically. |
| `set_reminder` / `complete_reminder` | Create and transition reminders. |
| `set_communication_philosophy` | Store user-authored guidance; audit stores lengths, not text. |
| `import_content` / `stage_candidates` / `review_import` / `commit_import` | Reviewable distilled imports without raw source retention. |

`import_content(source_type, content, path, self_sender, forced)` accepts `email`, `mbox`, `vcard`, `ics`, `linkedin`,
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
hashes text it was not given. Supplying any of the other four without `source_kind` is refused rather than
silently ignored. The staging response gains the additive `source_session_id`, `duplicate`, and `reviewable`
fields; `import_content` gains the same three on a path-based import. A duplicate is reported, not raised: the response
describes the batch that already exists.

M18.3 adds three optional candidate fields to `stage_candidates`, all additive and all inert when omitted:
observation and interaction candidates accept a batch-local `evidence_ref` label, and a trait candidate accepts
`evidence_refs` naming those labels plus `evidence_ids` naming durable observations and interactions already in
the store. Staging rewrites each label to the canonical candidate id and stores neither the label nor a copy of
any record; commit resolves the rewritten ids through the M18.1 commit mapping, refuses evidence about anyone
but the trait's own subject, and leaves the trait unresolved rather than committing it ungrounded. Because that
mapping is what answers a batch-local citation, `evidence_refs` requires a request that also passes
`source_kind`; without one the request is refused with `evidence_requires_source_tracking`, while `evidence_ids`
stays available either way. An `evidence_ref` or `evidence_id` is at most 256 characters and one trait cites at
most 32 of them combined; both are opaque tokens preserved exactly — not trimmed, folded, or reshaped — so a
restored non-ULID id remains addressable. See
[docs/import.md](import.md#grounding-a-trait-in-the-records-it-was-drawn-from).

`get_person_context` gains an additive `trait_evidence` collection reporting which durable records the traits in
that same bundle were drawn from. It carries `trait_id`, `evidence_type`, and `evidence_id` only — never record
content — and is filtered twice: a link appears only for a trait the bundle actually disclosed, and only when
the *cited record's own* sensitivity is disclosable at the requested level. A visible trait therefore never
reveals that restricted evidence exists.

Because a path-based `import_content` now claims the file it reads, that tool also gains one optional `forced`
argument, matching the CLI's `--force`. It stages the same content as a distinct processing session and never
weakens the duplicate rule for later calls. It is additive and defaults to the released behaviour, and it is the
only route past `source_previously_redacted` after a hard forget — for `mbox`, which is read from a path and
cannot be resubmitted as inline content, the only route at all. See
[docs/import.md](import.md#source-receipts-and-repeat-imports-m181).

`set_relationship` accepts free-form type input. M7 snake-case normalizes it, resolves synonyms, canonicalizes
inverse direction, orders symmetric endpoints, and updates an existing active canonical edge instead of
inserting a duplicate. Unknown types remain legal. Staged `relationship` candidates commit through this same
contract, and are ordinary-disclosure only because the durable relationship model carries no sensitivity
field.

## M21 quick-capture contract

### `remember`

```json
{
  "person": "Alice Ng",
  "note": "prefers short emails",
  "kind": "auto",
  "org": "Acme",
  "role": "CTO",
  "relationship": null,
  "predicate": null,
  "trait_category": null,
  "sensitivity": "personal"
}
```

`person` is resolved through `resolve_person` with `org`/`role`/`relationship` as hints. The write proceeds
only for an **exact** normalized-name match or a top candidate scoring at least `0.7` (a strong lexical hit);
a fuzzy edit-distance match is never written against. When nobody matches, the person is created through
`RememberPerson`. Then, in the same transaction:

- `org` (with `role`, default `member`) records an affiliation through `SetAffiliation`;
- `relationship` records an edge from the user's self record to the person through `SetRelationship` with the
  normal vocabulary and inverse handling; without a self record the call returns `status: no_self` and writes
  nothing;
- `note` records one of `fact` (`predicate`, default `note`), `trait` (`trait_category`, default by rule or
  `other`), or `interaction` (the person as sole participant, occurring now). `kind: auto` classifies the note by
  a fixed keyword table documented on `classify_note` in `app/capture/quick_capture.py`: interaction cues, then
  topics to avoid, then communication style, then preference, otherwise a fact.

Response:

```json
{
  "status": "recorded",
  "person_id": "...",
  "canonical_name": "Alice Ng",
  "created": true,
  "recorded": [
    {"kind": "affiliation", "id": "...", "summary": "CTO at Acme"},
    {"kind": "trait", "id": "...", "summary": "communication_style: prefers short emails"}
  ],
  "candidates": [],
  "message": null
}
```

`status` is one of `recorded`, `ambiguous` (several candidates close together), `unconfirmed` (one weak
candidate), `no_self`, `nothing_to_record` (a bare name), or `invalid_request`. The last covers a structural
`kind` without its payload (`affiliation` without `org`, `relationship` without `relationship`, a note kind
without `note`) and an elevated `sensitivity` combined with `org` or `relationship`: affiliations and
relationships carry no sensitivity level and are disclosed by every ordinary read, so the call refuses rather
than silently recording an ungated row — record the private statement as a `fact` instead. Both checks run
before any resolution or write. Every status other than `recorded` carries an
empty `recorded` list and writes nothing; `ambiguous` and `unconfirmed` return `candidates` so the agent can ask.
All rows share one `transaction_id` in the audit log and changelog, exactly as the individual tools would have
written them, and a failure anywhere rolls back everything including a person created moments earlier.

`remember` records what the user *stated*. Material extracted or inferred from transcripts, notes, or earlier
conversation still goes through `stage_candidates` → `review_import` → `commit_import`.

### Prompts and resources

The server also exposes the packaged usage guidance through the protocol, for clients that do not load skills:

| Kind | Name / URI | Purpose |
|---|---|---|
| resource | `people-context://guide` | The usage skill body: resolution first, context vs. guidance, meeting prep, propose-then-commit capture. |
| resource | `people-context://self` | Narrow identity of the user's own record, or `{"found": false}`. |
| prompt | `who(name)` | Resolve, then read context only on a confident match. |
| prompt | `remember(statement)` | `remember` for a direct statement; `stage_candidates` for extracted material. |
| prompt | `meeting_prep(attendees)` | Context, guidance, and reminders per attendee; read-only. |
| prompt | `end_of_session_capture()` | Propose learnings with `stage_candidates`; never commit. |
| prompt | `maintenance_review(name)` | Timeline and consolidation signals, then proposals awaiting approval. |

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
