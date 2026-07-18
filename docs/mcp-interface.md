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

All eight tools are annotated `readOnlyHint=true`.

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
caps are 100 nodes and 300 edges. Cap removal sets `truncated=true`.

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

`set_relationship` accepts free-form type input. M7 snake-case normalizes it, resolves synonyms, canonicalizes
inverse direction, orders symmetric endpoints, and updates an existing active canonical edge instead of
inserting a duplicate. Unknown types remain legal.

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
