# Architecture

`people-context-mcp` is built as a hexagonal (ports & adapters) system. A small `domain` + `app` core holds
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
                         │    repository.py       unit_of_work.py     │
                         │    audit_log.py       changelog.py  hlc.py   │
                         │    semantic.py        email_import.py        │
                         │                       vcard_import.py        │
                         │                                              │
                         │  cli.py                config.py             │
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
                         └───────────────────┬─────────────────────────┘
                                             │ depended on by (never implemented in)
                         ┌───────────────────▼─────────────────────────┐
                         │                   app                        │
                         │  resolve_person.py   person_context.py       │
                         │  record.py           communication.py        │
                         │  importing.py        curate.py   export.py   │
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
are Pydantic models with no knowledge of persistence, transport, or process boundaries. One entity per
module (SRP).

### `app`

Use cases: one class per module, each depending only on `ports` (never on a concrete adapter). Examples:
`ResolvePerson`, `SearchPeople`, `RememberPerson`, `SemanticSearch`, `CandidateStager`, and the lifecycle,
guidance, import, curation, and export use cases. Use cases orchestrate domain
objects and ports; they contain the only place application-level policy (e.g. the ambiguity threshold in
identity resolution, or the minimal-disclosure cap in context assembly) is allowed to live.

### `ports`

`typing.Protocol` interfaces, split narrowly by concern rather than one fat repository interface:
`PersonReader` and `PersonWriter` (`ports/repository.py`), semantic embedding/vector/rebuild ports
(`ports/semantic.py`), `AuditLog` (`ports/audit_log.py`), `Changelog` (`ports/changelog.py`),
`HybridLogicalClock` (`ports/hlc.py`), `UnitOfWork` (`ports/unit_of_work.py`), and `Clock`
(`ports/clock.py`). Splitting concerns means read use cases do not depend on write, audit, or sync capability.
The application layer owns transaction orchestration through the UoW port; SQLite owns BEGIN/COMMIT/ROLLBACK.

### `adapters`

Concrete implementations of the ports, plus anything that talks to the outside world:

- `adapters/sqlite/` — `db.py` (connection, migrations, and local device initialization), migrations
  `001_initial.sql` and `002_sync_foundations.sql`, repositories, `audit_log.py`, `changelog.py`, `hlc.py`, and
  `unit_of_work.py`. Adapter write methods join an enclosing transaction rather than committing independently.
- `adapters/mcp/` — `server.py` (`build_server`/`main`, tool registration and annotations), `tools/` (one
  module per tool group).
- `adapters/email_import.py` and `adapters/vcard_import.py` — source-specific, stdlib-backed extraction;
  both feed the shared candidate staging use case described in [docs/import.md](import.md).
- `adapters/model2vec_embeddings.py`, `adapters/semantic_indexing.py`, and `adapters/sqlite/semantic.py` —
  optional cached embeddings, best-effort lifecycle refresh decorators, and same-file cosine vec0 storage.
- `cli.py` — the `people-context` CLI, built on the same `app` use cases as the MCP tools.
- `config.py` — DB path resolution (flag → env → config file → agent workspace → XDG); this is itself an
  adapter concern (it reads environment and filesystem) but is small enough to live at the package root.

## Dependency rule

Dependencies point inward only:

```
adapters  →  ports  →  app  →  domain
```

`domain` and `app` do not import anything from `adapters`, and do not import the `mcp` package or `sqlite3`.
This is enforced by convention and code review today; a lint rule (e.g. `ruff`'s `TID251`/banned-imports, or
an import-linter contract) may be added later to enforce it mechanically. Any module under `domain/` or
`app/` that needs to import `sqlite3`, `mcp`, or anything under `adapters/` is a layering bug.

## How new transports and importers slot in

Because `app` only depends on `ports`, adding a new way to reach the core is purely additive:

- **Streamable HTTP (delivered, M4).** `build_server()` remains the sole construction and registration path.
  `main()` alone selects the default stdio transport or configures loopback Streamable HTTP, so transport
  choice never duplicates use-case or tool wiring.
- **New importers (delivered pattern).** Email/mbox and vCard adapters parse source formats into the same
  strict person/interaction/affiliation/fact candidate shapes. `stage_candidates` exposes that same path to
  agents extracting concise facts from notes; format parsing differs, while validation, matching, local-ref
  rewriting, atomic staging, review, and commit remain shared.
- **New repository backends.** Anything implementing `PersonReader`/`PersonWriter` (an in-memory fake for
  tests, a different embedded database) is a drop-in substitute for `SqlitePeopleRepository` — this is the
  Liskov Substitution Principle applied to the repository port, and it is what makes `app`-level tests
  possible without touching SQLite at all.

## Entrypoint wiring

Wiring — constructing concrete adapters and injecting them into use cases — happens only at entrypoints,
never inside `domain` or `app`:

- `adapters/mcp/server.py:build_server()` resolves the DB path, opens the SQLite connection, constructs
  `SqlitePeopleRepository`, `SqliteAuditLog` (paired with the local changelog/HLC), and `SystemClock`, builds
  the `app` use cases from them, and
  registers MCP tools that call into those use cases. `main()` parses `--db` plus transport flags; it runs
  stdio by default or applies loopback HTTP settings before `run(transport="streamable-http")`.
- `cli.py:main()` performs the equivalent wiring for the CLI, so CLI commands and MCP tools call the exact
  same use case classes and therefore obey the exact same audit/provenance rules.
- `__main__.py` (`python -m people_context`) is a thin alias to the stdio server entrypoint.

This is Dependency Inversion in practice: `app` and `domain` depend only on abstractions (`ports`); concrete
choices are made once, at the edge, in the entrypoint that happens to be running.

## SOLID mapping

| Principle | How it is applied here |
|---|---|
| **S**ingle Responsibility | One use case per `app/` module; one entity per `domain/` module. |
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

Merge writes row-level child effects and a semantic parent manifest under one `transaction_id`. Forget is the
explicit append-only exception: it hard-deletes primary rows, redacts covered audit and changelog payloads, and
retains an ID-only forget tombstone indefinitely in M6. `people-context sync-log` is the only new inspection
surface; no MCP tool, peer, exchange protocol, or replay engine is introduced.

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
