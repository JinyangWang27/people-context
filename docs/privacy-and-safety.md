# Privacy and Safety

`people-context` stores personal, potentially sensitive information about people in the user's life. This
document lays out the safety model: what the software guarantees, what it depends on the user's environment
for, and what its threat model does and does not cover.

## Local, user-owned, no surprise network activity

The entire dataset lives in a single SQLite file the user owns and controls (see
[docs/data-model.md](data-model.md) and [docs/cli.md](cli.md) for its exact location and how to inspect it
directly). The server does not phone home, sync, or have remote accounts. Stdio serving, loopback HTTP
serving, ordinary CLI commands, and `semantic_search` make no outbound requests. The sole in-project network
path is explicit: `pctx reindex --semantic` may download the pinned multilingual model after
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

Aggregate reports follow the same boundary. `get_stale_relationships` and `pctx stale` count only
`public`/`personal` interactions, and they aggregate in SQL rather than filtering afterwards, so a person whose
interactions are all `sensitive`/`restricted` produces exactly the same row as a person with no interactions at
all. Timing metadata is disclosure too: an ordinary recency report that moved whenever an elevated interaction
was recorded would leak when those conversations happened. The report returns names, relationship categories,
dates, and counts only — never interaction summaries or channels.

`upcoming_dates` and `pctx upcoming` apply the same rule to date facts. Only `public`/`personal` birthday facts
are projected, and an elevated birthday contributes neither an entry nor a `skipped_unparseable` increment, so
the counter cannot be used to detect that one exists. A birthday entry is labelled `Birthday` rather than the
stored value, so the upcoming date is disclosed without the birth year. The report also never guesses a
timezone for a stored reminder datetime; it reports the calendar day the reminder was written with.

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
are discarded before staging, and per-card skip reasons never echo raw values. For WhatsApp chat exports, only
the timestamp prefix and the sender label of each message are read: everything after the sender separator —
message text, attachment file names, and system notices — never reaches a candidate, a skip reason, a log
record, or an error, and a fixed `WhatsApp chat` interaction summary is staged for each calendar day. For
Outlook contacts CSV, only the canonical name, email, company, job title, and birthday columns are read, so
`Notes`, `Web Page`, and every other exported column are never staged. `stage_candidates` accepts only
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

## Optional at-rest encryption

By default the database is plain SQLite, readable by any process running as the user and by anyone who can read
the file. That default has not changed. `people-context-mcp --encrypted` and the global CLI form
`pctx --encrypted ...` opt into SQLCipher instead, which encrypts the main database file and its WAL and shared
memory companions.

The key comes only from the `PEOPLE_CONTEXT_DB_KEY` environment variable. It is never a flag value (which a
process listing or shell history would expose), never a config-file entry, and never written to a log, an
exception message, an audit payload, or a changelog payload. An unset, empty, or whitespace-only key is refused
before anything is opened, and there is no fallback to plaintext: a refusal exits non-zero rather than quietly
writing an unencrypted file. A wrong key produces one generic message that distinguishes neither key material
nor page contents. SQLCipher itself also writes its own `hmac check failed` diagnostics to stderr in that case;
they carry no key material or record text, and stdout stays clean so the stdio protocol is never corrupted.

Encryption is applied before any schema metadata is read, so a new and an existing encrypted database both run
the ordinary forward-only migrations afterwards. Every mutation still flows through the same audit and changelog
seam; encryption changes how the file is stored, not what is recorded.

What this does and does not protect:

- **Protected:** a stolen disk or backup, a copied database file, and another local account reading the file
  directly — the main file, `-wal`, and `-shm` all fail to open without the key.
- **Not protected:** the running process itself. While the server or CLI is running the key is in memory and the
  data is decrypted for use. Encryption is not a substitute for full-disk encryption or OS account separation,
  and it does not protect exports: `pctx export` and `pctx sync push` write plaintext files regardless.
- **Not covered by this milestone:** key rotation, OS keychain integration, and multiple keys.

**Platform support.** The `encrypted` extra installs `sqlcipher3-binary`, which publishes prebuilt wheels for
**glibc-based Linux x86_64** (`manylinux2014`) only, and no source distribution. The dependency is marked
`sys_platform == 'linux' and platform_machine == 'x86_64'`, so macOS, Windows, and non-x86_64 resolution keeps
working without silently claiming an unavailable binding.

PEP 508 defines no libc marker, so that marker cannot exclude **musl-based Linux x86_64 (Alpine)**, where no
compatible artifact exists: requesting the extra there fails to install. For that reason the binding is not part
of the default `dependency-groups.dev`, so the plain `uv sync` development command never breaks on musl — only an
explicit `--extra encrypted` or `--all-extras` can.

On musl, macOS, Windows, or arm64, install a compatible `sqlcipher3` build yourself (it needs a local SQLCipher
library); the `--encrypted` flag refuses with installation guidance until one is importable. CI installs the
extra on the platform it claims and asserts the binding actually imports, so a marker or resolution mistake
fails the build instead of quietly skipping the encryption tests.

Losing the key means losing the data. There is no recovery path, by design.

## Export for portability

The human-operated `pctx export` CLI produces a deterministic, domain-shaped JSON export of the
full portable dataset, including soft-deleted people, interaction participant ids, preference text, and
decoded audit payloads. Derived `person_search`/semantic vec0 rows and pending `import_staging` candidates are
excluded. M6 also excludes `devices`, `changelog`, and `sync_conflicts`: this export remains the byte-compatible
version-1 portability snapshot, not a sync bootstrap package. Semantic model id/dimension preferences remain portable.

Both `pctx export --output FILE` and `pctx sync push --output DIR` publish through one shared atomic
private-file writer. Content is written to a `0600` temporary file created with `O_CREAT | O_EXCL` in the
destination directory, `fsync`ed, and then moved into place. Other local accounts therefore never see a
partially written personal-data file, an existing permissive destination is replaced rather than left with its
old mode, a symlink at the destination is replaced instead of followed to an unexpected target, and a failed
write leaves any previously valid file untouched.

The maximal-disclosure `export_data` MCP tool is absent by default. An operator must start the server process
with `PEOPLE_CONTEXT_MCP_ENABLE_EXPORT=1` before a client can discover it. This process-level boundary, not a
model-supplied tool argument or advisory annotation, is the security control. Prefer the CLI for routine export.

## Person brief and person index

`pctx brief` and `pctx list --json` are human-operated, CLI-only read paths. They record nothing, mint no audit or
changelog rows, and add no model-callable tool.

A brief defaults to ordinary disclosure. `--include-sensitive` widens only the context-backed facts,
interactions, and traits; communication guidance keeps its own `public`/`personal` contract in both modes, so the
flag can never pull an elevated record into the guidance section. Both levels are stated in the document — in the
Markdown header and in the JSON `disclosure` object — alongside a notice that the brief is outside the server's
disclosure controls once rendered or written. Reading it back, redirecting stdout, or handing the file to another
tool is the operator's own disclosure decision.

The person index is identity only: stable id, canonical name, alias values, summary, `is_self`, and a `deleted`
flag. It carries no facts, interactions, traits, or reminders at any sensitivity level, which is what lets an
integration list people without reading their records.

`pctx brief --output FILE` publishes through the same shared atomic private-file writer, so the destination is
`0600`, an existing permissive file is replaced rather than left with its old mode, a destination symlink is
replaced instead of followed, and a failed write preserves any previously valid file. The command refuses to
publish over the database it is reading or any of that database's sidecars.

## Obsidian plugin

The Obsidian plugin under `obsidian-plugin/` is a read-only consumer of the two documents above. It calls
`pctx list --json` and `pctx brief <person-id> --json`, never opens the SQLite database, and has no write path.
It never passes `--include-sensitive` or `--all`, so it sees ordinary-disclosure records for people who have not
been soft-deleted. A brief is always addressed by the stable id the index returned, never by a display name.

Contact data is treated as untrusted command input: the plugin spawns the configured executable with a separate
argument array and `shell: false`, never builds a command string, offers no free-form arguments setting, bounds
every run with a timeout and output caps, and validates a person id before it can become an argument. Panes are
painted as text nodes, so a name containing markup or shell metacharacters stays inert. An encrypted database is
opened with the `PEOPLE_CONTEXT_DB_KEY` value the Obsidian process already carries; the plugin never stores,
prompts for, or logs the key, and reports the CLI's own refusal rather than falling back to plaintext.

The brief pane stores the opaque id of the person it is showing in the host's workspace layout, so a restored
tab reopens on that person; no name and no records are persisted, but that id lives in the vault.

What the plugin renders is still personal data, and it renders it inside a vault. **Anything cached or written
into a synchronized vault has left this project's local-first perimeter** and is governed by that sync provider,
exactly as an exported brief or vault Markdown is. See [obsidian-plugin.md](obsidian-plugin.md).

## vCard export

`pctx export-vcard` is a human-operated, CLI-only export of active people as vCards. It is a read path: it
records nothing, mints no audit or changelog rows, and adds no model-callable tool. Soft-deleted people are
excluded, and affiliations are evaluated as of the export date.

It defaults to ordinary disclosure. Sensitive and restricted birthday facts are invisible without
`--include-sensitive`: they supply no `BDAY` and contribute to none of the reported counts, so the counts never
signal that an elevated record exists. The counts themselves are aggregate only — how many affiliations or
birthdays were omitted or skipped, never which person or which value.

Stdout is the default. The document goes to stdout alone and the counts and the disclosure notice go to stderr,
so a redirected stream is a valid vCard file. `--output FILE` publishes through the same shared atomic
private-file writer, so the destination is `0600`, an existing permissive file is replaced rather than left with
its old mode, a destination symlink is replaced instead of followed, and a failed write preserves any previously
valid file. The command refuses to publish over the database it is reading or any of that database's sidecars.

The file carries names, aliases, mail addresses, one affiliation, and one birthday in plaintext, outside the
server's disclosure controls. Handing it to a contacts application or an address-book sync service is the
operator's own disclosure decision.

## Reminder calendar export

`pctx reminders-ics --output FILE` is a human-operated, CLI-only export of active reminders as iCalendar
`VTODO` entries. It is a read path: it records nothing, mints no audit or changelog rows, and adds no
model-callable tool. It publishes through the same shared atomic private-file writer, so the destination is
`0600`, an existing permissive file is replaced rather than left with its old mode, a destination symlink is
replaced instead of followed, and a failed write preserves any previously valid file.

The file carries reminder text, due dates, and creation timestamps in plaintext, outside the server's
disclosure controls. Handing it to a calendar application or a sync service is the operator's own disclosure
decision. The export never invents a timezone for a stored naive timestamp: such reminders are counted and
omitted rather than silently reinterpreted in the host timezone.

## Changelog watch

`pctx watch` is a human-operated, CLI-only tail of the local changelog. It is a read path: it records nothing,
mints no audit or changelog rows, persists no cursor between invocations, adds no model-callable tool, and makes
no network call.

Unlike `pctx sync-log`, whose payloads are opt-in behind `--payloads`, `watch` always prints the full replay
payload of every entry, because a partial tail is not a usable change feed. Those payloads carry the same
personal data the record itself holds, at every sensitivity level, and the command prints them to local stdout
only. Redirecting that stream to a file, a pipeline, or another program is the operator's own disclosure
decision, and the command says so on stderr before the first entry so that stdout stays a clean stream of JSON
lines.

## Data-quality findings

`pctx doctor` is a human-operated, CLI-only report with no MCP tool behind it. It is a read path: it records
nothing, mints no audit or changelog rows, repairs nothing, and makes no network call. Finding something is not
a failure, so the command exits `0` either way.

The report deliberately juxtaposes stored personal values, and it does not filter facts by sensitivity: a
contradiction between two `restricted` values is still a contradiction, and hiding it would leave the operator
unable to judge their own data. It carries no interaction summaries, relationship labels, or affiliation roles —
a dangling reference is reported as an entity type and id only. Like every other file this CLI writes, the
output sits outside the server's disclosure controls, and the command says so before printing any evidence — on
stdout ahead of the human report, and on stderr in `--json` mode. A report with no findings prints no notice,
because it exposes nothing.

Suggested repairs are data, not commands. Each one is a structured argv list or an MCP tool name with an
id-only argument mapping, so no display name is ever interpolated into something executable — which matters
precisely because several findings exist to report that two people share a display name. Nothing is executed for
the operator, and a soft-deleted person's repair is the operator-gated `forget` tool rather than a CLI command.

The doctor also refuses to adjudicate. It never picks which of two contradictory values should survive, so a
correction suggestion identifies the record and declares the payload it deliberately left empty
(`requires: ["fields"]`) rather than inventing a value the operator never asserted.

## Aggregate inventory

`pctx stats` is a human-operated, CLI-only report with no MCP tool behind it. Like the doctor it is a pure read
path: it records nothing, mints no audit or changelog rows, and makes no network call.

Its privacy property is structural rather than a filter applied at the end. The read port is defined so that
only counts, byte totals, and bucket names can cross it: no canonical name, alias value, fact value,
observation, interaction summary, or device display name is ever selected. Changelog entries are grouped by the
device's opaque id and the `devices` table is deliberately not joined, because its `display_name` is a machine
hostname — the one piece of identifying text in the sync tables.

That guarantee has to cover the grouping keys themselves, and not every key is vocabulary this project chooses.
Alias kinds and sensitivity levels are closed enumerations on every write path, including bundle restore, so
they are safe as written. Three are not. A relationship category is free text the operator typed at
`relationship-types add --category`, and a restored audit operation is whatever the origin installation wrote;
both are folded into non-identifying sentinels — `custom` and `other` — before they cross the port, so those
rows are still counted but the authored wording is never reported.

Device ids are the third, and they are handled differently because collapsing them would destroy the
distribution rather than protect it: per-device counts exist precisely to tell devices apart. An id this
installation generated is opaque by construction and names nobody, so it is reported as itself. `sync pull`
accepts any non-blank device id, though, so a bundle can carry a hostname or a personal label where an opaque
key belongs — those keep a bucket of their own under a positional pseudonym instead. Tightening bundle
validation would be the other route, but it would reject bundles that restore today and would bind future id
formats, so the report pseudonymizes at its own boundary rather than narrowing an established contract.

The report is also a single consistent snapshot, not a series of independent reads. Every count is taken inside
one transaction, so a writer committing mid-report — the MCP server running beside the CLI is a supported
arrangement — cannot produce figures that contradict each other.

The resolved database path is not an aggregate. It usually carries the operator's account name and it says where
the file lives, so the application redacts it and includes it only when the operator passes `--include-path`.
The adapter measures the file but never returns the path, so a caller cannot obtain it by accident.

Elevation gate status is read from the environment of the CLI process itself, using the same rule the MCP server
applies, and is reported as a fact about *this* environment. Neither the use case nor the adapter starts,
contacts, or probes an MCP server to find out what a server elsewhere would expose, and no gate state is ever
taken from an argument a model could supply.

Aggregate metadata is still information: how many people you track, how much you record about them, how much of
it is `restricted`, and how many devices have written here are all revealing even without a single name. The
command says so before the report and on stderr in `--json` mode, and, like every other file this CLI writes,
the output sits outside the server's disclosure controls.

## Bootstrap sync bundle

`pctx sync push` writes one complete point-in-time bootstrap bundle: the portable dataset, both relationship
vocabulary tables, every changelog entry, the referenced device rows, and the origin HLC watermark.

- The bundle is **plaintext** and is strictly higher fidelity than `pctx export`, because it carries full replay
  payloads and audit history. Keep and transport it only on encrypted storage or through an encrypted channel.
- Forgotten-record redaction travels verbatim. The bundle contains the already-redacted payloads and ID-only
  tombstones a local read would return; nothing is reconstructed or enriched.
- Push and pull are human-operated CLI actions. No MCP tool exports or restores a bundle, and no model-callable
  surface changed.
- Strict versioned validation protects integrity and compatibility. It does not authenticate a sender:
  authenticity and encrypted transport remain future protocol work.

`pctx sync pull` restores one bundle, and only into a database that is still exactly as `open_db` created it.

- The complete document is parsed and validated before any preview, confirmation prompt, or database
  reservation, so an invalid bundle can never reach the destination.
- The destination must be baseline-empty: one active local device, no rows in any mutable domain, preference,
  staging, audit, sync, or derived-search table, no optional vector storage, and only seeded relationship
  vocabulary. Refusals name tables and counts, never record contents, and write nothing. Restore never deletes,
  clears, or merges existing state to make the target look fresh.
- Every imported device is written as retired history and the destination keeps its own active identity, so a
  bundle can never duplicate a live device identity or let two machines mint the same device id.
- Restore is verbatim: original ids, timestamps, provenance, audit rows, and changelog rows are reinstated
  without minting new audit or changelog entries. Forgotten-record redaction therefore stays redacted, and
  nothing is reconstructed or enriched.
- Semantic vectors are not transferred. They are rebuildable cache data; run `pctx reindex --semantic` locally.

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

### Local-first versus cloud-hosted memory, as of 2026-08-05

This comparison exists so that the trade-off is judged on verifiable properties rather than on marketing. It
compares this project with two shapes of hosted alternative — assistant-integrated memory (ChatGPT, Claude) and
a dedicated memory platform (Mem0) — on four axes only: storage location at rest, what a vendor breach or legal
demand can expose, whether the product works fully offline, and what deletion means. Vendor behavior is
summarized from that vendor's own documentation on the date above and is not re-verified automatically; treat
the linked primary sources as authoritative and re-check them before relying on this table.

Every cell is sourced per axis below. Where a vendor does not document an axis directly, the entry says what is
documented instead of asserting more, so a claim is never wider than its source.

| Axis | `people-context` | Assistant-integrated memory (ChatGPT, Claude) | Dedicated memory platform (Mem0) |
|---|---|---|---|
| Storage at rest | One local SQLite file on the user's machine, plaintext in v1 | Vendor-operated back-end systems, tied to the account | Managed platform storage, or an open-source deployment on infrastructure you run |
| Vendor breach or legal demand | No vendor holds a copy, so there is no vendor account to breach or subpoena; exposure follows the user's own device, backups, and legal position | The vendor holds the data and both vendors publish policies for disclosing it under legal process | Follows the deployment: the managed platform places the data with the vendor, a self-hosted deployment does not |
| Fully offline | Yes for ordinary use; only `pctx reindex --semantic` may reach the network | No offline mode is documented; memory is an account feature of the hosted service | The managed platform is hosted; a self-hosted deployment runs on your own infrastructure and can use local models |
| Deletion | `forget` hard-deletes the targeted rows and redacts covered audit and changelog payloads in the same transaction | Deletion is a request to the vendor, subject to documented back-end retention windows | Delete, batch-delete, and filter-scoped delete APIs against the deployment holding the data |

Sources, by vendor and axis:

- **ChatGPT.**
  - *Storage and offline:* the [Memory FAQ](https://help.openai.com/en/articles/8590148-memory-faq) describes
    saved memories as an account-level ChatGPT feature managed in settings and carried across chats; no offline
    mode is described there or elsewhere in that collection.
  - *Legal demand:* the [privacy policy](https://openai.com/policies/row-privacy-policy/) and the published
    [law enforcement policy](https://cdn.openai.com/pdf/openai-law-enforcement-policy-v.2025-12.pdf) describe
    disclosure pursuant to valid legal process and, in limited emergencies, to prevent danger of death or
    serious physical injury.
  - *Deletion:* the [Memory FAQ](https://help.openai.com/en/articles/8590148-memory-faq) documents that saved
    memories can be viewed, deleted individually, cleared, or turned off, that a log of deleted saved memories
    may be retained for up to 30 days, and that memory and chat history delete separately — deleting memories
    does not delete past chats, and deleting a chat does not remove memories already saved from it.
- **Claude.**
  - *Storage and offline:* memory entries are listed and managed in
    [Settings > Memory](https://support.anthropic.com/en/articles/11817273-using-claude-s-chat-search-and-memory-to-build-on-previous-context)
    within the Claude account, and
    [data protection](https://privacy.anthropic.com/en/articles/10458704-how-does-anthropic-protect-the-personal-data-of-claude-ai-users)
    describes Anthropic-operated storage; no offline mode is described.
  - *Legal demand:* the privacy policy in the [Anthropic Legal Center](https://legal.anthropic.com/) covers
    disclosure to governmental and regulatory authorities as required by law, and the
    [Transparency Hub](https://www.anthropic.com/transparency/system-trust-reporting) describes how law
    enforcement and government data requests, including preservation requests, are processed.
  - *Deletion:* memory entries can be inspected and deleted individually, and deleting or expiring a
    conversation does not by itself remove memory generated from it. Deleted conversations leave the visible
    history immediately and back-end systems
    [within 30 days](https://privacy.anthropic.com/en/articles/7996878-can-you-delete-data-sent-via-claude-ai);
    [retention](https://privacy.anthropic.com/en/articles/10023548-how-long-do-you-store-personal-data) is
    longer for consumer accounts that opt in to model training.
- **Mem0.**
  - *Storage, legal demand, and offline:* [Platform versus open source](https://docs.mem0.ai/platform/platform-vs-oss)
    and the [open-source overview](https://docs.mem0.ai/open-source/overview) document the managed platform
    alongside a self-hosted deployment that runs the same engine on your own infrastructure, with your choice
    of vector store, embedder, and LLM — including local models. Storage location and legal exposure therefore
    follow the chosen deployment rather than the product name.
  - *Deletion:* [delete operations](https://docs.mem0.ai/core-concepts/memory-operations/delete) and the
    [delete API](https://docs.mem0.ai/api-reference/memory/delete-memories) cover single, batch, and
    filter-scoped removal.
- **This project.** The database path is resolved locally (see [docs/cli.md](cli.md)), ordinary server and CLI
  commands make no network request, and `forget` is a hard delete rather than a hide: targeted rows are removed
  and earlier audit rows plus every covered changelog transaction are replaced with `{"redacted": true}` in the
  same transaction, as described in [Forget vs. soft delete](#forget-vs-soft-delete).

What local-first does not buy, stated plainly: the database is plaintext SQLite, so device compromise, an
unencrypted disk, a synced backup folder, or an exported vault or JSON file exposes the same content; there is
no vendor security team, no server-side access logging, and no recovery path if the file is lost; and a legal
demand can still be served on the user directly. The mitigations remain full-disk encryption, filesystem
permissions, and deliberate handling of exports.
