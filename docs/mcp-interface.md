# MCP Interface

This document describes the full MCP tool surface exposed by `adapters/mcp/server.py`, built with the
official `mcp` Python SDK's `FastMCP`. It covers, per tool: purpose, parameters, return shape, MCP
annotation, and implementation status. It also documents the ambiguity contract of `resolve_person` and the
minimal-disclosure behaviour of `get_person_context`. For the underlying schema, see
[docs/data-model.md](data-model.md); for the resolution algorithm, see
[docs/identity-resolution.md](identity-resolution.md).

## Transport

The server runs over **stdio** today (`people-context-mcp` / `python -m people_context`), which is what
`build_server(db_path).run()` starts. A localhost **Streamable HTTP** transport is planned for M4 (see
[docs/roadmap.md](roadmap.md)) as an additional adapter — the tool definitions and use cases underneath do
not change; only the transport adapter is added. The server registers itself under the name
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
| `resolve_person` | Resolve a name/query to one or more candidate people, with scores and match reasons. | `query: str`, `hints?` | `ResolutionResult`: `query`, `candidates: list[ResolutionCandidate]` (each with `person_id`, `canonical_name`, `score`, `match_reason`, `aliases`, `summary`), `ambiguous: bool` | **Implemented (M0)** |
| `search_people` | Free-text search over people (broader than resolution — for browsing/lookup rather than pinning down one identity). | `query: str`, `filters?` | `list[ResolutionCandidate]` | **Implemented (M0)** |
| `get_person_context` | Minimal-disclosure context bundle for a person: identity, active relationships/roles, top-N relevant facts/interactions. | `person_id: str`, `purpose?: str`, `max_items?: int`, `include_sensitive?: bool` | Bundle containing identity summary, active relationships/affiliations, and a capped, ranked slice of facts/interactions. Sensitive items excluded unless `include_sensitive` is set. | Stub — planned **M1** |
| `get_communication_guidance` | Structured bundle for composing communication advice: the person's traits, relevant relationship/role context, recent interaction friction notes, active `communication_note` reminders, and the user's communication philosophy text. | `person_id: str`, `situation?: str` | Structured bundle (traits, context, reminders, philosophy text) — advice itself is composed by the client LLM, not the server. | Stub — planned **M2** |
| `list_reminders` | List reminders, optionally filtered, so agents can surface due follow-ups/occasions on their own schedule (pull-based; no server-side scheduler). | `person_id?: str`, `due_before?`, `status?` | `list[Reminder]` | Stub — planned **M2** |

## Write tools

Annotated as writes (not read-only); MCP clients apply their normal approval flow.

| Tool | Purpose | Parameters | Return shape | Status |
|---|---|---|---|---|
| `remember_person` | Create or update a person by name; merges aliases and optionally sets a summary. | `name: str`, `aliases?: list[{value, kind, lang?, script?}]`, `summary?: str`, `is_self?: bool`, `source?: str`, `session?: str` | `RememberPersonResult`: `person: Person`, `created: bool` | **Implemented (M0)** |
| `add_alias` | Add an alias (nickname, native-script name, transliteration, handle, former name) to an existing person. | `person_id`, `value`, `kind?`, `lang?`, `script?` | Updated `Person` | Stub — planned **M2** |
| `set_relationship` | Create/update a directed, typed relationship between two people. | `subject_id`, `object_id`, `type`, `label?`, `valid_from?`, `valid_to?`, `confidence?` | `Relationship` | Stub — planned **M2** |
| `set_affiliation` | Create/update a person's role at an organization over a period. | `person_id`, `org_id` (or org name), `role`, `valid_from?`, `valid_to?`, `confidence?` | `Affiliation` | Stub — planned **M2** |
| `record_fact` | Record a time-aware fact about a person. | `person_id`, `predicate`, `value`, `valid_from?`, `valid_to?`, `confidence?`, `sensitivity?` | `Fact` | Stub — planned **M2** |
| `record_observation` | Record a subjective observation about a person. | `person_id`, `text`, `observed_at?`, `sensitivity?` | `Observation` | Stub — planned **M2** |
| `record_trait` | Record a derived characteristic (communication style, temperament, values, preference, topics to avoid). | `person_id`, `category`, `value`, `evidence_note?`, `confidence?`, `sensitivity?` | `Trait` | Stub — planned **M2** |
| `record_interaction` | Record a concise interaction summary and its participants. | `summary`, `occurred_at?`, `channel?`, `participant_ids`, `sensitivity?` | `Interaction` | Stub — planned **M2** |
| `correct_record` | Correct a previously recorded fact/observation/trait/relationship/affiliation without silently overwriting history. | `entity_type`, `entity_id`, corrected fields | Updated entity | Stub — planned **M2** |
| `set_reminder` | Create a reminder for a person. | `person_id`, `text`, `kind`, `due_at?`, `recurrence?` | `Reminder` | Stub — planned **M2** |
| `complete_reminder` | Mark a reminder completed. | `reminder_id` | Updated `Reminder` | Stub — planned **M2** |
| `set_communication_philosophy` | Store/update the user's free-text communication guidance framework. | `text: str` | `CommunicationPhilosophy` | Stub — planned **M2** |
| `import_content` | Extract candidate person/alias/fact/interaction records from a source (e.g. an `.eml`/mbox file) into staging. Raw content is parsed in-memory and discarded — never persisted. | `source_type`, `content` or `path` | Staging batch summary (`batch_id`, candidate count) | Stub — planned **M3** |
| `review_import` | Return the staged candidates for a batch for user review. | `batch_id` | List of staged candidates | Stub — planned **M3** |
| `commit_import` | Write the accepted staged candidates into the real tables, with provenance `source: import/<type>`. | `batch_id`, `accepted_ids` | Summary of records written | Stub — planned **M3** |

## Destructive tools

Annotated `destructiveHint: true`.

| Tool | Purpose | Parameters | Return shape | Status |
|---|---|---|---|---|
| `merge_people` | Merge a duplicate person record into a primary one; re-parents all related rows, keeps a full audit trail. | `primary_id`, `duplicate_id` | Merged `Person` | Stub — planned **M3** |
| `forget` | Hard delete a target (person or narrower scope) and write a tombstone audit entry. | `target`, `scope` | Confirmation of what was deleted | Stub — planned **M3** |
| `export_data` | Full JSON export of the dataset, for portability. | (none) | JSON document of all records | Stub — planned **M3** |

Stub tools currently return a fixed shape: `{"status": "not_implemented", "planned_milestone": "M1" | "M2" | "M3"}`,
so the full intended surface is visible in any MCP client's tool list even before each tool is implemented.

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

The result is always identity plus active (not expired) relationships/roles plus a capped, ranked slice of
facts/interactions — never a full table dump. See [docs/privacy-and-safety.md](privacy-and-safety.md) for
the disclosure policy this implements.
