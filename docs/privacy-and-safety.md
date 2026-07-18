# Privacy and Safety

`people-context-mcp` stores personal, potentially sensitive information about people in the user's life. This
document lays out the safety model: what the software guarantees, what it depends on the user's environment
for, and what its threat model does and does not cover.

## Local, user-owned, no surprise network activity

The entire dataset lives in a single SQLite file the user owns and controls (see
[docs/data-model.md](data-model.md) and [docs/cli.md](cli.md) for its exact location and how to inspect it
directly). The server does not phone home, sync, or have remote accounts. Stdio serving, loopback HTTP
serving, ordinary CLI commands, and `semantic_search` make no outbound requests. The sole in-project network
path is explicit: `people-context reindex --semantic` may download the pinned multilingual model after
printing its identity, URL, approximately 512 MB size, and cache directory. Search uses
`local_files_only=True`; missing cache state returns `not_available` instead of downloading. This preserves
the no-surprise-network rule while keeping semantic retrieval optional.

M6 implements local durable change capture only. It adds one installation device row, persisted HLC state, and
a plaintext replay changelog inside the same SQLite file. It adds no network path, account, pairing, relay, peer
registration, remote access, batch encryption, replay engine, bootstrap restore, or background sync process.

## Minimal disclosure

Context-returning tools never dump full records. Responses are:

- **Capped** — facts and interactions share an explicit or default `max_items` ("disclosure budget"), so a
  caller only receives as much ranked assertive history as it asked for. Active relationships,
  affiliations, purpose-gated traits, and communication notes sit outside that budget.
- **Ranked** — the most relevant eligible facts/interactions are returned first, not an arbitrary slice.
- **Sensitivity-filtered** — ordinary MCP context excludes `sensitive` and `restricted` records. There is
  no model-supplied boolean that can widen this boundary.

This applies most directly to `get_person_context` (see
[docs/mcp-interface.md](mcp-interface.md#minimal-disclosure-in-get_person_context)), but the same posture —
never return more than the task needs — applies across the tool surface.

## Sensitivity levels and defaults

Every assertive record (facts, observations, traits, interactions, relationships, affiliations) carries a
`sensitivity` value:

| Level | Meaning | Default inclusion in context responses |
|---|---|---|
| `public` | Freely shareable information, such as a public job title. | Included by default. |
| `personal` | Ordinary personal information not meant for broad disclosure. | Included by default. |
| `sensitive` | Information not to surface casually, such as health or finances. | Excluded. |
| `restricted` | The most guarded tier. | Excluded. |

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

For email and mbox imports, Subject values are treated as attacker-controlled input and are not persisted or
returned to the model. A fixed `Email correspondence` interaction summary is staged instead; message id,
date, channel, and participants remain available as narrow provenance. For vCards, NOTE/PHOTO/ADR/TEL/X-fields
are discarded before staging, and per-card skip reasons never echo raw values. `stage_candidates` accepts only
narrow structured fields; agents must extract concise candidates from notes rather than submit or persist the
notes themselves.

## Audit of every mutation

Every create, update, merge, and forget operation atomically writes primary state, an `audit_log` entry, and
one or more full replay rows in `changelog`; a failure rolls the whole logical operation back. The audit remains
for local accountability and deliberately uses privacy-preserving summaries for some operations.

Forget replaces matching earlier audit payloads and every covered changelog transaction with
`{"redacted": true}`. The audit is therefore append-oriented, not immutable. The changelog additionally stores
full after-images, installation identity, persisted HLC order, transaction grouping, changed fields, and actor
provenance. Communication philosophy remains length-only in audit while its full text is present in the local
changelog. See [the sync design](design/sync.md#2-fitness-of-the-current-audit-log-as-a-replication-source).

## Forget vs. soft delete

Two distinct deletion mechanisms exist, and they are not interchangeable:

- **Soft delete** (`persons.deleted_at`) hides a person from normal listings and resolution without
  physically removing any data. It is reversible and is the default outcome of ordinary "this person is no
  longer relevant" bookkeeping.
- **Forget** (the `forget` tool) is a **hard delete**: targeted rows are removed. Earlier audit rows and every
  covered changelog transaction are replaced with `{"redacted": true}`. The user-facing audit tombstone keeps
  scope and deletion counts; the durable changelog tombstone keeps stable target/coverage ids only. Neither
  contains names, values, summaries, observation text, or preference content.

See [docs/data-model.md](data-model.md#soft-delete-vs-forget) for the schema-level detail.

## Export for portability

The human-operated `people-context export` CLI produces a deterministic, domain-shaped JSON export of the
full portable dataset, including soft-deleted people, interaction participant ids, preference text, and
decoded audit payloads. Derived `person_search`/semantic vec0 rows and pending `import_staging` candidates are
excluded. M6 also excludes `devices`, `changelog`, and `sync_conflicts`: this export remains the byte-compatible
version-1 portability snapshot, not a sync bootstrap package. Semantic model id/dimension preferences remain portable.

The maximal-disclosure `export_data` MCP tool is absent by default. An operator must start the server process
with `PEOPLE_CONTEXT_MCP_ENABLE_EXPORT=1` before a client can discover it. This process-level boundary, not a
model-supplied tool argument or advisory annotation, is the security control. Prefer the CLI for routine export.

## Writes and destructive operations are annotated for client-side gating

Every write and destructive MCP tool carries the appropriate `ToolAnnotations` (`readOnlyHint`/
`destructiveHint`) so that MCP clients — Claude Code and others — can apply their own approval UI/policy
before executing a mutation. These annotations are advisory metadata, not an authorization boundary. High-
disclosure reads therefore use process-level capability gates and are absent from ordinary tool discovery.
See [docs/mcp-interface.md](mcp-interface.md#annotations).

## Threat model notes

### Sync threat model (design stage)

No sync exchange implementation exists in M6. The design in [docs/design/sync.md](design/sync.md) assumes direct
encrypted file exchange or an optional dumb relay that stores opaque batches.

- **Relay trust is deliberately narrow.** End-to-end authenticated encryption is expected before a batch
  leaves a device. Relay TLS is useful in transit but is not a substitute because the relay must not receive
  plaintext personal data or dataset keys. The relay may still observe metadata such as timing and batch size.
- **Relay retention is outside the local database guarantee.** A relay should support deletion and bounded
  retention, but backups may retain ciphertext. Future key rotation should make retired epochs unreadable.
- **Forget propagates when replicas reconnect.** A forget tombstone instructs each replica to hard-delete
  primary rows, redact local audit and changelog payloads, and suppress stale operations for the target.
- **A permanently offline device cannot be remotely erased.** A device that never reconnects keeps its copy.
  Retirement prevents future sync and should rotate keys, but it cannot delete data already stored there.
- **Inter-user sharing is a separate boundary.** `restricted` data must not sync to another user by default.
  Ownership, authenticated actors, sharing grants, and per-user `is_self` semantics require a later design.

The right to forget takes precedence over retaining a complete replicated history, but it cannot guarantee
physical deletion from an unreachable device or third-party backup.

- **Installed integrations execute local code.** A Claude Code/OpenClaw/Codex integration that starts this
  project through `uv` executes the repository's Python code with the user's normal filesystem permissions.
  It is not a sandboxed data-only extension. Install only from a repository and revision you trust.
- **Sensitive MCP reads require operator elevation.** `get_person_context` never returns `sensitive` or
  `restricted` rows. `get_sensitive_person_context` exists only when the server process starts with
  `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE=1`; models cannot enable it through tool arguments.
- **Loopback HTTP is unauthenticated.** `people-context-mcp --http` binds only to `127.0.0.1` and enables
  DNS-rebinding protection for `127.0.0.1`/`localhost` hosts and HTTP origins. This prevents remote binding
  and common browser rebinding attacks, but it is not process isolation: every local process able to reach
  loopback can attempt to use the MCP endpoint. Do not run it on a shared machine unless that trust boundary
  is acceptable. Authenticated or remotely reachable HTTP is explicitly deferred.
- **Semantic vectors are sensitivity-filtered derived data.** Only active people and public/personal
  interaction summaries are indexed. Search rechecks primary rows during hydration, so a stale vector for a
  deleted person or newly sensitive interaction is not returned. Reindex remains the repair path.
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
