# MCP Interface

This document describes the full MCP tool surface exposed by `adapters/mcp/server.py`, built with the
official `mcp` Python SDK's `FastMCP`. It covers, per tool: purpose, parameters, return shape, MCP
annotation, and implementation status. It also documents the ambiguity contract of `resolve_person` and the
minimal-disclosure behaviour of `get_person_context`. For the underlying schema, see
[docs/data-model.md](data-model.md); for the resolution algorithm, see
[docs/identity-resolution.md](identity-resolution.md).

## Transport

The server runs over **stdio** by default (`people-context-mcp` / `python -m people_context`). The same
`build_server()` construction/registration path can run loopback **Streamable HTTP** with
`people-context-mcp --http --host 127.0.0.1 --port 8765`; `main()` alone selects and configures transport.
The HTTP endpoint is `/mcp`, unauthenticated, restricted to loopback, and protected by allowed Host/Origin
checks for `127.0.0.1` and `localhost`. The server registers itself under the name
`"people-context"` and logs the resolved database path to stderr at startup (never stdout, since stdio
carries the protocol itself).

## Annotations

Every tool carries an MCP `ToolAnnotations` value that tells clients how to gate approval:

| Annotation | Meaning | Applies to |
|---|---|---|
| `readOnlyHint: true` | Tool only reads; safe to call without approval in most clients. | Resolution/search/context/reminder-listing/guidance tools. |
| (default, `readOnlyHint: false`) | Tool writes new or modified data; clients should apply their normal write-approval flow. | `remember_person`, `add_alias`, `set_relationship`, etc. |
| `destructiveHint: true` (implies `readOnlyHint: false`) | Tool can irreversibly delete or restructure data. | `merge_people`, `forget`. |

## Read-only tools

| Tool | Purpose | Parameters | Return shape | Status |
|---|---|---|---|---|
| `resolve_person` | Resolve a name/query to one or more candidate people, with scores and match reasons. | `query: str`, `hints?: {org?: str, role?: str, relationship?: str}`, `limit: int = 5` | `ResolutionResult`: `query`, `candidates: list[ResolutionCandidate]` (each with `person_id`, `canonical_name`, `score`, `match_reason`, `aliases`, `summary`), `ambiguous: bool` | **Implemented (M1)** |
| `search_people` | Free-text search over people (broader than resolution — for browsing/lookup rather than pinning down one identity). | `query: str`, `filters?` | `list[ResolutionCandidate]` | **Implemented (M0)** |
| `semantic_search` | Optional multilingual cosine retrieval over active people and public/personal interaction summaries. | `query: nonblank str`, `kinds: ["person" | "interaction"] = both`, `limit: 1..100 = 10` | Success: `{"status":"ok","model_id", "hits":[{"kind","entity_id","score","title","summary"}]}`. Missing package/model/index returns `not_available`; stored model mismatch returns `model_mismatch`, both with repair instructions. | **Implemented (M4)** |
| `get_person_context` | Minimal-disclosure context bundle for a person: narrow identity, active relationships/roles, and top-ranked facts/interactions. | `person_id: str`, `purpose?: str`, `max_items: int = 10`, `include_sensitive: bool = false` | `PersonContextResult`, with the stable shape documented below. | **Implemented (M1)** |
| `get_communication_guidance` | Structured bundle for composing communication advice: sensitivity-gated traits, active relationship/role context, up to five newest interaction summaries, active `communication_note` reminders, and the user's communication philosophy text. Observations are never returned. | `person_id: str`, `situation?: str` | `CommunicationGuidanceResult`: `found`, `person_id`, echoed `situation`, `traits: {category: list[Trait]}`, `relationships`, `affiliations`, `friction_notes: list[str]`, `reminders`, `communication_philosophy: str \| null`, `philosophy_set: bool`. | **Implemented (M2)** |
| `list_reminders` | List reminders, optionally filtered, so agents can surface due follow-ups/occasions on their own schedule (pull-based; no server-side scheduler). | `person_id?: str`, `due_before?: ISO datetime`, `status?: ReminderStatus` (defaults to `active`) | `{"reminders": list[Reminder]}`; due-dated items ordered by `due_at` ascending, then undated communication notes. | **Implemented (M2)** |

## Write tools

Annotated as writes (not read-only); MCP clients apply their normal approval flow.

| Tool | Purpose | Parameters | Return shape | Status |
|---|---|---|---|---|
| `remember_person` | Create or update a person by name; merges aliases and optionally sets a summary. | `name: str`, `aliases?: list[{value, kind, lang?, script?}]`, `summary?: str`, `is_self?: bool`, `source?: str`, `session?: str` | `RememberPersonResult`: `person: Person`, `created: bool` | **Implemented (M0)** |
| `add_alias` | Add an alias (nickname, native-script name, transliteration, handle, former name) to an existing person. | `person_id`, `value`, `kind?`, `lang?`, `script?` | Updated `Person` | **Implemented (M2)** |
| `set_relationship` | Create a directed, typed relationship between two existing people. | `subject_id`, `object_id`, `type`, `label?`, `valid_from?`, `valid_to?`, `confidence?` | `Relationship` | **Implemented (M2)** |
| `set_affiliation` | Create a person's role at an organization over a period; `org` accepts an existing id or a name to get/create. | `person_id`, `org`, `role`, `valid_from?`, `valid_to?`, `confidence?` | `Affiliation` | **Implemented (M2)** |
| `record_fact` | Record a time-aware fact about an existing person. | `person_id`, `predicate`, `value`, `valid_from?`, `valid_to?`, `confidence?`, `sensitivity?` | `Fact` | **Implemented (M2)** |
| `record_observation` | Record a subjective observation about an existing person. | `person_id`, `text`, `observed_at?`, `sensitivity?` | `Observation` | **Implemented (M2)** |
| `record_trait` | Record a derived characteristic with a category validated against `TraitCategory`. | `person_id`, `category`, `value`, `evidence_note?`, `confidence?`, `sensitivity?` | `Trait` | **Implemented (M2)** |
| `record_interaction` | Record a concise interaction summary after validating every participant id. | `summary`, `participant_ids`, `occurred_at?`, `channel?`, `sensitivity?` | `Interaction` | **Implemented (M2)** |
| `correct_record` | Correct whitelisted fields in place for a fact, observation, trait, relationship, affiliation, or reminder; audit retains full before/after snapshots. | `entity_type`, `entity_id`, `fields` | Updated entity | **Implemented (M2)** |
| `set_reminder` | Create a kind-validated reminder for an existing person. | `person_id`, `text`, `kind`, `due_at?`, `recurrence?` | `Reminder` | **Implemented (M2)** |
| `complete_reminder` | Transition an active reminder to completed. | `reminder_id` | Updated `Reminder` | **Implemented (M2)** |
| `set_communication_philosophy` | Store/update the user's free-text communication guidance framework. | `text: str` | `CommunicationPhilosophy`; audit contains lengths only. | **Implemented (M2)** |
| `import_content` | Deterministically extract candidates from `.eml`/mbox headers or vCard 3.0/4.0. Message bodies and ignored vCard fields are never stored. | `source_type: "email" \| "mbox" \| "vcard"`; email/vCard accept exactly one of content/path, mbox accepts path | `{"batch_id", "candidate_count", "skipped_message_ids", "skipped_without_id", "skipped_cards"}` | **Implemented (M4)** |
| `stage_candidates` | Atomically stage strict agent-extracted people, interactions, affiliations, and facts after parsing user-provided notes or other visible text. Raw notes are not accepted as a field. | `source: nonblank str`, `candidates: list[discriminated candidate]`; batch-local `ref` values connect dependencies | Normal `ImportBatchResult`; validation returns `invalid_candidates` with Pydantic details, allowed types, and valid fields. | **Implemented (M4)** |
| `review_import` | Return the staged candidates and current statuses for a batch. | `batch_id` | `{"batch_id": str, "candidates": [{"id", "source", "status", "candidate"}]}` | **Implemented (M3)** |
| `commit_import` | Commit accepted people and resolvable interactions/affiliations/facts with file or `import/agent:<source>` provenance. | `batch_id`, `accepted_ids` | `{"batch_id", "committed_ids", "unresolved_ids", "skipped_ids"}` | **Implemented (M4)** |

## Destructive tools

Annotated `destructiveHint: true`.

| Tool | Purpose | Parameters | Return shape | Status |
|---|---|---|---|---|
| `merge_people` | Atomically merge an active duplicate into an active primary, re-parent linked rows, remove resulting self-loops, and soft-delete the duplicate. The duplicate canonical name is retained as a `former_name` alias. | `primary_id`, `duplicate_id` | `{"person": Person, "moved": {facts, observations, traits, reminders, affiliations, relationships, interaction_participations}, "self_loops_removed": int}` | **Implemented (M3)** |
| `forget` | Atomically hard-delete a person graph or one `entity_type:entity_id` record, redact identifying prior audits, and append a minimal tombstone. | `target`, `scope: "person" \| "record"` | `{"scope": str, "target": str, "deleted": {plural_type: count}}` | **Implemented (M3)** |
| `export_data` | Full domain-shaped JSON export, including soft-deleted people and decoded audit/preference values. | (none) | Versioned envelope with `format`, `version`, `exported_at`, and every portable domain collection | **Implemented (M3)** |

## M3 lifecycle and import errors

Lifecycle/import failures are structured result objects. Merge adds `same_person` and
`self_merge_direction`; forget adds `invalid_scope` and `invalid_target` and reuses `person_not_found` /
`record_not_found`. Import adds `invalid_source`, `invalid_source_type`, `invalid_path`, `no_candidates`,
`batch_not_found`, and `candidate_not_in_batch`. Each result starts with `error` and `message`; target-specific
fields such as `person_id`, `entity_type`, `entity_id`, `batch_id`, or `candidate_ids` are included when useful.

M4 adds stable vCard per-card reasons (`missing_fn`, `unsupported_version`, `malformed_card`) using one-based
indexes. `stage_candidates` accepts only extra-forbidden discriminated shapes: person (`ref`, `name`,
aliases and optional summary/provenance fields), interaction (`summary`, `participant_refs`, `date` and
optional channel/message/sensitivity), affiliation (`person_ref`, `org`, `role`, optional validity/confidence),
and fact (`person_ref`, `predicate`, `value`, optional validity/confidence/sensitivity). Duplicate or unknown
batch-local refs reject the whole batch before staging.

## Semantic availability and scoring

The vec0 column explicitly declares `DISTANCE_METRIC=cosine`; API score is `1.0 - distance`. Results from
requested kinds merge by descending score, then entity id, and are rehydrated against current primary data
so deleted people and newly sensitive/restricted interactions cannot leak through stale vectors. Search
checks stored model id/dimension before embedding and never downloads. Run
`uv sync --extra semantic` followed by `uv run people-context reindex --semantic` to install and rebuild.

## M2 write errors

M2 tools return domain failures as structured result objects rather than protocol exceptions. Common shapes
start with `{"error": "person_not_found", "person_id": ...}`, `{"error": "record_not_found",
"entity_type": ..., "entity_id": ...}`, `{"error": "invalid_correction", "fields": [...],
"allowed_fields": [...]}`, `{"error": "reminder_not_active", "reminder_id": ..., "status": ...}`, or
`{"error": "validation_error", "details": [...]}`. Writes never create an implicit person. For
`set_affiliation`, a supplied existing organization id is used directly; a name is normalized and
get-or-created.

## The ambiguity contract of `resolve_person`

`resolve_person` never silently guesses when more than one plausible candidate exists:

- If two or more candidates score above the resolution threshold and the gap between the top two scores is
  small (`< 0.2`), the result is marked `ambiguous: true` and **all** qualifying candidates are returned,
  each with its own `score` and `match_reason`, so the caller (typically an LLM) can disambiguate using
  additional context (org, role, recent conversation) or ask the user.
- If no candidate clears the minimum score threshold, `candidates` is empty rather than returning a weak
  guess; callers are expected to fall back to `remember_person` to create a new record.

See [docs/identity-resolution.md](identity-resolution.md) for the full scoring pipeline this contract sits
on top of.

## Minimal disclosure in `get_person_context`

`get_person_context` is deliberately not "dump everything known about this person." It accepts:

- `purpose?` — a hint about why the context is being requested (e.g. `"communication"`, `"scheduling"`),
  which the use case can use to decide which record types are relevant.
- `max_items?` — a "disclosure budget": the caller states how many items it actually needs, and the server
  returns only the top-ranked slice within that budget, rather than every fact/interaction on file.
- `include_sensitive?` — sensitive-tagged items are excluded from the response unless this is explicitly
  set, keeping the default response safe to hand to a general-purpose coding agent that has no particular
  need for a person's more private information.

`max_items` may be zero and must not be negative. Facts and interactions compete in one combined pool, so
`len(facts) + len(interactions) <= max_items`. Records are first ordered newest-first and assigned ordinal
recency from `1` down to `0`, then ranked by `0.7 * recency + 0.3 * confidence`; interactions use confidence
`1.0`. Ties break by newest timestamp, record kind, then id. Public and personal records are eligible by
default; `include_sensitive=true` also admits sensitive and restricted records.

Relationships and affiliations are active, fully hydrated, and outside the disclosure budget. Observations
remain empty in M1. Traits are returned only when `purpose` contains `"communication"` case-insensitively,
and only active `communication_note` reminders are returned; both are also outside the budget.

The exact top-level response shape is:

```json
{
  "found": true,
  "person_id": "requested-person-id",
  "identity": {
    "id": "person-id",
    "canonical_name": "Alice Example",
    "aliases": ["Ally"],
    "summary": "Colleague",
    "is_self": false
  },
  "relationships": [
    {
      "relationship": {
        "id": "relationship-id",
        "subject_id": "person-id",
        "object_id": "other-person-id",
        "type": "colleague_of",
        "label": null,
        "period": {"valid_from": null, "valid_to": null},
        "confidence": 1.0,
        "provenance": {"source": "user", "session": null, "stated_by": null},
        "created_at": "2025-01-01T00:00:00Z"
      },
      "other_person_id": "other-person-id",
      "other_person_name": "Bob"
    }
  ],
  "affiliations": [
    {
      "affiliation": {
        "id": "affiliation-id",
        "person_id": "person-id",
        "org_id": "organization-id",
        "role": "Engineer",
        "period": {"valid_from": null, "valid_to": null},
        "confidence": 1.0,
        "provenance": {"source": "user", "session": null, "stated_by": null},
        "created_at": "2025-01-01T00:00:00Z"
      },
      "organization_name": "Acme"
    }
  ],
  "facts": [
    {
      "id": "fact-id",
      "person_id": "person-id",
      "predicate": "location",
      "value": "Dubai",
      "period": {"valid_from": null, "valid_to": null},
      "recorded_at": "2025-01-01T00:00:00Z",
      "confidence": 1.0,
      "sensitivity": "personal",
      "provenance": {"source": "user", "session": null, "stated_by": null}
    }
  ],
  "interactions": [
    {
      "id": "interaction-id",
      "summary": "Discussed the launch",
      "occurred_at": "2025-01-01T00:00:00Z",
      "channel": "call",
      "participant_ids": ["person-id"],
      "sensitivity": "personal",
      "provenance": {"source": "user", "session": null, "stated_by": null}
    }
  ],
  "observations": [],
  "traits": [
    {
      "id": "trait-id",
      "person_id": "person-id",
      "category": "communication_style",
      "value": "Prefers concise updates",
      "evidence_note": null,
      "confidence": 1.0,
      "sensitivity": "personal",
      "provenance": {"source": "user", "session": null, "stated_by": null},
      "updated_at": "2025-01-01T00:00:00Z"
    }
  ],
  "reminders": [
    {
      "id": "reminder-id",
      "person_id": "person-id",
      "text": "Prefer written updates",
      "kind": "communication_note",
      "due_at": null,
      "recurrence": null,
      "status": "active",
      "created_at": "2025-01-01T00:00:00Z"
    }
  ]
}
```

Nullable values are shown explicitly; timestamps and dates use Pydantic JSON mode. For an unknown or
soft-deleted person, `found` is `false`, `person_id` still contains the requested id, `identity` is `null`,
and every array is empty.
