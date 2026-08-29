# Architecture

`people-context` is built as a hexagonal (ports & adapters) system. A small `domain` + `app` core holds
all product logic and imports nothing from the outside world; every technology-specific concern — SQLite,
the MCP SDK, file-based importers, the CLI — lives in an adapter that depends on the core through narrow
interfaces (`ports`). This document explains the layers, the dependency rule, how new adapters slot in, and
several structural decisions that only make sense in the context of this layout.

See also: [docs/data-model.md](data-model.md) for what the core actually stores, and
[docs/decisions/0003-hexagonal-architecture.md](decisions/0003-hexagonal-architecture.md) for the ADR.

## Layer diagram

```
                         ┌───────────────────────────────────────────┐
                         │                 adapters                    │
                         │                                              │
                         │  adapters/sqlite/     adapters/mcp/          │
                         │    db.py                server.py            │
                         │    migrations/*.sql     tools/*.py           │
                         │    repository.py         merge_store.py      │
                         │    forget_store.py       record_store.py     │
                         │    audit_log.py          unit_of_work.py     │
                         │    semantic.py        importers/*.py         │
                         │                                              │
                         │  cli/*.py              runtime.py config.py  │
                         └───────────────────┬─────────────────────────┘
                                             │ implements
                         ┌───────────────────▼─────────────────────────┐
                         │                  ports                       │
                         │  ports/repository.py   PersonReader,         │
                         │                          PersonWriter,        │
                         │                          SearchHit            │
                         │  ports/audit_log.py     AuditLog, AuditEntry  │
                         │  ports/changelog.py     Changelog, entries     │
                         │  ports/hlc.py           HybridLogicalClock     │
                         │  ports/unit_of_work.py  UnitOfWork             │
                         │  ports/clock.py         Clock, SystemClock     │
                         │  ports/sleep.py         Sleeper, SystemSleeper │
                         └───────────────────┬─────────────────────────┘
                                             │ depended on by (never implemented in)
                         ┌───────────────────▼─────────────────────────┐
                         │                   app                        │
                         │  people/      context/       records/         │
                         │  relationships/ imports/     exports/         │
                         │  semantic/      _mutation.py                  │
                         └───────────────────┬─────────────────────────┘
                                             │ operates on
                         ┌───────────────────▼─────────────────────────┐
                         │                  domain                      │
                         │  person.py  relationship.py  organization.py │
                         │  fact.py  observation.py  trait.py           │
                         │  interaction.py  reminder.py  preferences.py │
                         │  shared.py (Confidence, Sensitivity, …)      │
                         └───────────────────────────────────────────────┘
```

## Layer responsibilities

### `domain`

Pure data and small, dependency-free behaviour: `Person`, `Alias`, `Relationship`, `Organization`,
`Affiliation`, `Fact`, `Observation`, `Trait`, `Interaction`, `Reminder`, and the shared value objects
(`Confidence`, `Sensitivity`, `Provenance`, `ValidityPeriod`, ULID id generation, name normalization). These
are Pydantic models with no knowledge of persistence, transport, or process boundaries. Modules group only
cohesive domain concepts.

### `app`

Use cases are organized by product capability: `people`, `context`, `records`, `relationships`, `imports`,
`exports`, and `semantic`. Small symmetric use cases may share a focused module; workflows with distinct
models, staging, and orchestration responsibilities are split. Classes still remain focused and depend only
on `domain` and narrow `ports`, never on a concrete adapter. The root `app` package is not a public barrel.
Use cases contain the only place application-level policy (e.g. the ambiguity threshold in identity
resolution, the minimal-disclosure cap in context assembly, the finding codes, precedence, and suggested
actions in `records/doctor.py`, or the path redaction in `context/stats.py`) is allowed to live.

### `ports`

`typing.Protocol` interfaces, split narrowly by concern rather than one fat repository interface:
`PersonReader` and `PersonWriter` (`ports/repository.py`), semantic embedding/vector/rebuild ports
(`ports/semantic.py`), `AuditLog` (`ports/audit_log.py`), `Changelog` (`ports/changelog.py`),
`MergeStore` (`ports/merge.py`), `ForgetStore` and `ForgetPreviewStore` (`ports/forget.py`),
`HybridLogicalClock` (`ports/hlc.py`), `BundleReader` (`ports/sync_bundle.py`),
`RecencyReader` (`ports/insights.py`), `CurationReader` (`ports/curation.py`),
`StatsReader` (`ports/stats.py`),
`ImportSourceStore` and the read-only `ImportSourceInspectionReader` (`ports/sources.py`),
`TraitEvidenceStore` and its read-only `TraitEvidenceReader` (`ports/evidence.py`),
`UnitOfWork` (`ports/unit_of_work.py`), `Clock` (`ports/clock.py`), and
`Sleeper` (`ports/sleep.py`).
Splitting concerns means read, merge, forget, audit, and sync use cases depend only on capabilities they consume.

A few callers legitimately need every side of one store — the semantic indexing decorators wrap person and
record persistence as a unit. They depend on `PeopleRepository` and `RecordStore`, which compose the narrow
ports rather than listing them as a union: a union would let a half-implementation satisfy the annotation and
fail on its first unsupported call. The narrow ports remain the right dependency everywhere else.

The application layer owns transaction orchestration through the UoW port; SQLite owns BEGIN/COMMIT/ROLLBACK.

### `adapters`

Concrete implementations of the ports, plus anything that talks to the outside world:

- `adapters/sqlite/` — `db.py` (connection, migrations, and local device initialization), migrations
  `001_initial.sql` and `002_sync_foundations.sql`, repositories, focused merge/forget, organization, preference,
  record, audit, changelog, HLC, and unit-of-work adapters. `record_store.py` persists only assertive records and
  reminders. `bundle_reader.py` reads every sync-bundle collection from one transaction so the exported bundle
  describes a single point in time. Adapter write methods join an enclosing transaction rather than committing
  independently.
- `adapters/filesystem/` — `private_file.py` (the shared atomic owner-only writer used by every personal-data
  file output) and `vault_writer.py`.
- `adapters/mcp/` — `server.py` (`build_server`/`main`, tool registration and annotations), `tools/` (one
  module per tool group).
- `adapters/importers/` — source-specific, stdlib-backed email, ICS, LinkedIn, and vCard extraction plus
  routing; every importer feeds the shared candidate staging use case described in [docs/import.md](import.md).
- `adapters/model2vec_embeddings.py`, `adapters/semantic_indexing.py`, and `adapters/sqlite/semantic.py` —
  optional cached embeddings, best-effort lifecycle refresh decorators, and same-file cosine vec0 storage.
- `cli/` — parser/dispatch and capability command modules for the `pctx` CLI, built on the same
  application runtime as the MCP tools while preserving `people_context.cli:main`.
- `adapters/runtime.py` — the shared composition root for SQLite, optional semantic decorators, clocks, and
  application use cases. Process entrypoints inject their own warning sink.
- `config.py` — DB path resolution (flag → env → config file → agent workspace → XDG); this is itself an
  adapter concern (it reads environment and filesystem) but is small enough to live at the package root.

## Dependency rule

Dependencies point inward only:

```
adapters/process  →  app  →  ports
                     │       │
                     └───────┴──→ domain
```

`domain` has no outward project-layer dependencies, `ports` depends only on `domain`, and `app` depends only
on `domain` and `ports` (plus modules within its own capability packages). A standard-library AST test
enforces these rules and rejects internal runtime import cycles. Any concrete adapter, MCP, SQLite, CLI, or
other process dependency entering the core is a layering bug.

## How new transports and importers slot in

Because `app` only depends on `ports`, adding a new way to reach the core is purely additive:

- **Streamable HTTP (delivered, M4).** `build_server()` remains the sole construction and registration path.
  `main()` alone selects the default stdio transport or configures loopback Streamable HTTP, so transport
  choice never duplicates use-case or tool wiring.
- **New importers (delivered pattern).** Email/mbox and vCard adapters parse source formats into the same
  strict person/interaction/affiliation/fact candidate shapes. `stage_candidates` exposes that same path to
  agents extracting concise facts from notes; format parsing differs, while validation, matching, local-ref
  rewriting, atomic staging, review, and commit remain shared. M17 added `observation`, `trait`, and
  `relationship` to that vocabulary the same way: new strict models and reference rewriting, with commit
  delegating to the injected `RecordObservation`, `RecordTrait`, and `SetRelationship` use cases rather than
  reaching for a repository — import gains candidate types, never a privileged write path.
- **New repository backends.** Anything implementing `PersonReader`/`PersonWriter` (an in-memory fake for
  tests, a different embedded database) is a drop-in substitute for `SqlitePeopleRepository` — this is the
  Liskov Substitution Principle applied to the repository port, and it is what makes `app`-level tests
  possible without touching SQLite at all.

## Entrypoint wiring

Wiring — constructing concrete adapters and injecting them into use cases — happens only at entrypoints,
never inside `domain` or `app`:

- `adapters/runtime.py:build_runtime()` resolves the DB path, opens the SQLite connection, constructs the
  concrete stores and `SystemClock`, applies optional semantic decorators, and builds the application use
  cases once. CLI and MCP inject different warning callbacks without duplicating setup. Its `encrypted`
  argument selects `open_encrypted_db()` over the default `open_db()`; the two are separate functions rather
  than one parameterized opener, so no existing caller can be flipped between plaintext and SQLCipher by
  accident. Both return an opaque DB-API connection, and the stores below the seam are unaware of the
  difference.
- `adapters/mcp/server.py:build_server()` obtains the shared runtime and registers MCP tools that call its use
  cases. `main()` parses `--db` plus transport flags; it runs
  stdio by default or passes loopback host, port, and transport-security settings directly to
  `run(transport="streamable-http", ...)`.

  The server shares one SQLite connection (`check_same_thread=True`) across all tools. MCP SDK 2 dispatches
  synchronous handlers to worker threads, so the MCP adapter deliberately registers async tool wrappers that
  call the synchronous application use cases without yielding. SQLite access therefore remains serialized on
  the server event-loop thread. A long tool call still blocks the server; moving tool work off that thread or
  adding awaits around database access requires a connection-per-request or explicit serialized-access design
  first. Do not weaken this invariant by flipping `check_same_thread`.
- `cli/main.py:main()` performs parser dispatch against the same runtime, so CLI commands and MCP tools call
  the exact same use case classes and therefore obey the exact same audit/provenance rules.
- `__main__.py` (`python -m people_context`) is a thin alias to the stdio server entrypoint.

This is Dependency Inversion in practice: `app` and `domain` depend only on abstractions (`ports`); concrete
choices are made once, at the edge, in the entrypoint that happens to be running.

## SOLID mapping

| Principle | How it is applied here |
|---|---|
| **S**ingle Responsibility | Cohesive capability modules contain focused classes; file count is not a proxy for responsibility. |
| **O**pen/Closed | New adapters add tools, importers, and transports without changing core policy. |
| **L**iskov Substitution | SQLite and in-memory implementations substitute behind the same ports. |
| **I**nterface Segregation | Reader, writer, audit, and clock ports stay narrow. |
| **D**ependency Inversion | Core depends on ports; adapters implement them; entrypoints wire choices. |

## "Self as a person row"

The user is represented as an ordinary `persons` row with `is_self = true`, rather than as a special-cased
concept elsewhere in the schema or API. Consequences:

- Relationships are uniform, directed edges between two person ids — the user's own relationships (e.g.
  "reports to", "sibling of") are simply edges where `subject_id` is the self person's id. No parallel
  "user relationships" table or API is needed.
- Every tool, query, and CLI command that operates on a person works identically whether or not that person
  is the user.
- It keeps the door open for a future multi-user mode: a second self-flagged person, scoped by owner, would
  not require a separate relationship model. Ownership and the per-owner uniqueness rule still require a
  migration; see [the sync design](design/sync.md#7-multi-user-considerations).

<a id="append-only-audit-log-as-future-sync-foundation"></a>
## Accountability audit and replayable changelog

M6 implements ADR [0004](decisions/0004-changelog-vs-audit-log.md): every application mutation executes inside
an explicit `UnitOfWork` and commits primary rows, the accountability audit, persisted HLC advancement, and one
or more full replay changelog entries together. A failure at any point rolls the complete logical operation back.
Read paths do not open a UoW.

The two histories remain intentionally different:

- `audit_log` is user-facing accountability. Payloads may summarize private values; communication philosophy
  continues to record lengths only.
- `changelog` is local replay state. It stores full after-images, installation `device_id`, HLC components,
  transaction grouping, operation kind, changed fields, actor provenance, and payload schema version.

`Changelog.list_entries` returns entries newest first under the deterministic
`(hlc_physical_ms, hlc_logical, device_id, op_id)` comparison key. It reads a bounded page by default — 100 rows,
or the `--limit` `pctx sync-log` passes — while `limit=None` returns every matching row for a full-history
consumer.

Merge writes row-level child effects and a semantic parent manifest under one `transaction_id`. Forget is the
explicit append-only exception: it hard-deletes primary rows, redacts covered audit and changelog payloads, and
retains an ID-only forget tombstone indefinitely in M6. `pctx sync-log` is the only new inspection
surface; no MCP tool, peer, exchange protocol, or replay engine is introduced.

`pctx sync push` adds the first consumer beyond local inspection. `SqliteBundleReader` opens one read
transaction and returns the portable snapshot, both relationship-vocabulary tables, every changelog entry in
ascending comparison-key order, the devices those entries reference, the active origin device, and its HLC
watermark. `ExportSyncBundle(BundleReader, Clock)` validates that source into the strict versioned
`people-context-sync-bundle` document in `domain/sync_bundle.py`.

`pctx sync pull` closes the loop. `RestoreSyncBundle(BootstrapRestorer)` parses the document and applies the
document-level rules in `domain/sync_bundle.py::validate_bundle_document` — duplicate keys, origin identity,
changelog device references, watermark coverage, and every internal reference — before previewing counts or
prompting, so a bad document never reaches the destination. `SqliteBootstrapRestorer` then does all writing under
one `BEGIN IMMEDIATE` reservation taken *before* the baseline-empty checks, so no concurrent writer can slip a row
in between validating the target and filling it. It reconciles vocabulary, writes bundled devices as retired
history, inserts domain, audit, and changelog rows verbatim, rebuilds FTS through `PersonSearchIndexer`, and
advances the local clock through `HybridLogicalClock.observe()`. It is the sole documented exception to the
`audit_mutation` seam: reinstating recorded history must not manufacture new history. Any failure rolls the
transaction back to the freshly initialized state.

## Single-user now, multi-user-safe choices

The product is single-user through M6. Several existing choices reduce the cost of future multi-user work
without solving it:

- **IDs are ULIDs**, not auto-increment integers. Their large random component makes collisions across devices
  or users extremely unlikely. Their timestamp prefix is useful for diagnostics, but must not be treated as a
  causal order or global replication cursor when clocks can differ.
- **Self is a person row.** The relationship model remains uniform when `is_self` becomes unique per owner.
- **Assertive records carry provenance and sensitivity.** These columns provide useful attribution and
  disclosure vocabulary, although provenance strings are not authenticated actors.
- **Hexagonal boundaries isolate transports and persistence.** Authenticated transport, sync storage, and
  policy adapters can be added without moving protocol concerns into the domain.

Future multi-user support still needs owner and actor identities, ownership/sharing grants, per-owner `is_self`,
and an inter-user sensitivity policy. The audit log is not the changelog. Full analysis is in
[the sync design](design/sync.md#7-multi-user-considerations).
