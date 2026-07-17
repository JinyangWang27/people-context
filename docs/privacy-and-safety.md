# Privacy and Safety

`people-context-mcp` stores personal, potentially sensitive information about people in the user's life. This
document lays out the safety model: what the software guarantees, what it depends on the user's environment
for, and what its threat model does and does not cover.

## Local, user-owned, no network required

The entire dataset lives in a single SQLite file the user owns and controls (see
[docs/data-model.md](data-model.md) and [docs/cli.md](cli.md) for its exact location and how to inspect it
directly). The server performs no network calls of its own; it does not phone home, does not sync to any
service, and has no concept of a remote account. Any network activity in the system as a whole comes only
from the MCP client the server is wired into (e.g. Claude Code talking to Anthropic's API) — the
people-context server itself has no such dependency, per [docs/decisions/0002-sqlite.md](decisions/0002-sqlite.md).

## Minimal disclosure

Context-returning tools never dump full records. Responses are:

- **Capped** — facts and interactions share an explicit or default `max_items` ("disclosure budget"), so a
  caller only receives as much ranked assertive history as it asked for. Active relationships,
  affiliations, purpose-gated traits, and communication notes sit outside that budget.
- **Ranked** — the most relevant eligible facts/interactions are returned first, not an arbitrary slice.
- **Sensitivity-filtered** — items above the caller's disclosure setting are excluded unless explicitly
  requested (`include_sensitive`).

This applies most directly to `get_person_context` (see
[docs/mcp-interface.md](mcp-interface.md#minimal-disclosure-in-get_person_context)), but the same posture —
never return more than the task needs — applies across the tool surface.

## Sensitivity levels and defaults

Every assertive record (facts, observations, traits, interactions, relationships, affiliations) carries a
`sensitivity` value:

| Level | Meaning | Default inclusion in context responses |
|---|---|---|
| `public` | Freely shareable information (e.g. a public job title). | Included by default. |
| `personal` | Ordinary personal information not meant for broad disclosure but not especially sensitive. | Included by default. This is the default sensitivity for observations, facts, and traits when not otherwise specified. |
| `sensitive` | Information the user would not want casually surfaced (health, family conflict, finances, etc.). | Excluded unless `include_sensitive` is explicitly set. |
| `restricted` | The most guarded tier. | Excluded by default; included by `get_person_context` only when the caller deliberately sets `include_sensitive=true`. |

## Facts and observations, kept separate

Facts, observations, and traits are separated at three levels simultaneously — schema (different tables),
API (different tools: `record_fact` vs. `record_observation` vs. `record_trait`), and response formatting
(a context bundle labels which items are objective facts and which are subjective observations/derived
traits, rather than flattening them into one undifferentiated "here's what I know" block). See
[docs/data-model.md](data-model.md#facts-vs-observations-vs-traits) for the full comparison.

## No raw emails, conversations, or transcripts

By default, and by design, the system never stores raw message content:

- **Interactions** are concise, human/LLM-written summaries (`interactions.summary`), never transcripts or
  message bodies.
- **Imports** (see [docs/import.md](import.md)) extract and stage distilled candidates; the source file is
  parsed in-memory and discarded, and only a narrow provenance reference (e.g. a message id and date) is
  retained, not the message itself.

This is a hard constraint on the design, not a configurable option — there is no code path that persists
raw source content.

## Audit of every mutation

Every create, update, merge, and forget operation writes an entry to the append-only `audit_log` table
(`op`, `entity_type`, `entity_id`, `payload_json`, `source`, timestamp) before or alongside the mutation
itself — see [docs/data-model.md](data-model.md#audit_log). This gives the user a complete, chronological
record of what the system was told and when, independent of the current state of any given row. It also
happens to be the substrate the future changelog-based sync design (M5) would build on — see
[docs/architecture.md](architecture.md#append-only-audit-log-as-future-sync-foundation).

## Forget vs. soft delete

Two distinct deletion mechanisms exist, and they are not interchangeable:

- **Soft delete** (`persons.deleted_at`) hides a person from normal listings and resolution without
  physically removing any data. It is reversible and is the default outcome of ordinary "this person is no
  longer relevant" bookkeeping.
- **Forget** (the `forget` tool) is a **hard delete**: the targeted rows are actually removed from the
  database. Earlier audit rows whose entity id is deleted, or whose nested payload contains the forgotten
  person id as an exact scalar, are replaced with `{"redacted": true}`. The new tombstone contains only the
  scope and pluralized deletion counts — no names, values, summaries, or ids in its payload.

See [docs/data-model.md](data-model.md#soft-delete-vs-forget) for the schema-level detail.

## Export for portability

`export_data` produces a deterministic, domain-shaped JSON export of the full portable dataset on demand,
including soft-deleted people, interaction participant ids, preference text, and decoded audit payloads.
Derived `person_search` rows and pending `import_staging` candidates are excluded. Export does not mutate
data, but remains write-gated because it is a maximal-disclosure operation.

## Writes and destructive operations are annotated for client-side gating

Every write and destructive MCP tool carries the appropriate `ToolAnnotations` (`readOnlyHint`/
`destructiveHint`) so that MCP clients — Claude Code and others — can apply their own approval UI/policy
before executing a mutation. The server does not attempt to implement its own approval prompt; it relies on
the MCP client to honour these annotations, which is the standard mechanism MCP defines for this purpose.
See [docs/mcp-interface.md](mcp-interface.md#annotations).

## Threat model notes

- **The database file is plaintext SQLite.** Anyone with filesystem read access to the `.db` file (and its
  `-wal`/`-shm` companions while the server is running) can read its contents directly — there is no
  application-level encryption in v1. This is a deliberate trade-off for a plain, user-inspectable,
  tool-friendly file (see [docs/decisions/0002-sqlite.md](decisions/0002-sqlite.md) and
  [docs/cli.md](cli.md) for direct-access tooling).
- **Recommended mitigation: OS-level disk encryption.** Users concerned about at-rest confidentiality should
  rely on full-disk encryption (FileVault, BitLocker, LUKS, etc.) on the machine where the database lives,
  the same way they would for any other locally-stored personal data.
- **Future option: SQLCipher.** Transparent, encrypted-at-rest SQLite (via SQLCipher or an equivalent) is a
  plausible future enhancement if application-level encryption becomes a priority, but is not part of the
  v1 design — it would trade away some of the "plain file, any SQLite tool works" property described in
  [docs/cli.md](cli.md), so it is deferred rather than adopted by default.
- **Multi-process access.** WAL mode (see [docs/decisions/0002-sqlite.md](decisions/0002-sqlite.md)) allows
  concurrent readers and a single writer; the CLI and the MCP server may both open the same file safely, but
  this is not a substitute for access control — anything that can open the file can read or write it, subject
  to normal filesystem permissions.
