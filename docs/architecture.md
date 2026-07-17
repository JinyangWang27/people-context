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
                         │    repository.py                             │
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
                         │  ports/clock.py         Clock, SystemClock    │
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
(`ports/semantic.py`), `AuditLog` (`ports/audit_log.py`), and `Clock` (`ports/clock.py`). Splitting concerns means a use case that only reads people never has to
depend on write or audit capability — Interface Segregation in practice.

### `adapters`

Concrete implementations of the ports, plus anything that talks to the outside world:

- `adapters/sqlite/` — `db.py` (connection + pragmas + migration runner), `migrations/001_initial.sql`,
  `repository.py` (`SqlitePeopleRepository`, implementing `PersonReader` + `PersonWriter`), `audit_log.py`
  (`SqliteAuditLog`).
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
  `SqlitePeopleRepository`, `SqliteAuditLog`, and `SystemClock`, builds the `app` use cases from them, and
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
| **O**pen/Closed | New tools, importers, and transports are added as new adapters; `domain`/`app` are not modified to support them. |
| **L**iskov Substitution | Any `PersonReader`/`PersonWriter` implementation (SQLite, an in-memory test fake) is substitutable behind the same Protocol. |
| **I**nterface Segregation | Ports are split narrowly by concern — reader, writer, audit log, clock — rather than one fat repository interface. |
| **D**ependency Inversion | `domain`/`app` depend only on `ports`; adapters implement those ports; wiring happens exclusively at entrypoints. |

## "Self as a person row"

The user is represented as an ordinary `persons` row with `is_self = true`, rather than as a special-cased
concept elsewhere in the schema or API. Consequences:

- Relationships are uniform, directed edges between two person ids — the user's own relationships (e.g.
  "reports to", "sibling of") are simply edges where `subject_id` is the self person's id. No parallel
  "user relationships" table or API is needed.
- Every tool, query, and CLI command that operates on a person works identically whether or not that person
  is the user.
- It keeps the door open for a future multi-user mode (see [docs/roadmap.md](roadmap.md), M5): a second
  self-flagged person, scoped by owner, would not require a schema redesign.

## Append-only audit log as future sync foundation

Every mutation — create, update, merge, forget — writes an `AuditEntry` (`ports/audit_log.py`) before or
alongside the write itself. In M0 this exists purely for local accountability (see
[docs/privacy-and-safety.md](privacy-and-safety.md)), but the append-only, ordered structure is deliberately
shaped so a future changelog-based export/replication mechanism (see [docs/roadmap.md](roadmap.md), M5) can
be built directly on top of it, without committing to CRDTs, vector clocks, or any specific sync protocol
today. The audit log is the single point where "what changed and when" is captured; a sync design only has
to decide how to ship that log elsewhere.

## Single-user now, multi-user-safe choices

The product is single-user in M0–M4. Two choices keep multi-user extension cheap without paying for it now:

- **IDs are ULIDs**, not auto-increment integers. ULIDs are globally unique and lexically sortable by
  creation time, so records created independently by two future users (or two future replicas of the same
  user's data) never collide and can still be merged in time order.
- **The audit log is the changelog.** Because every mutation is already recorded as an ordered, append-only
  entry, a future multi-user or multi-device design can reason about "what happened, in what order" without
  retrofitting change tracking onto tables that were not designed to be diffed.

Neither choice implies any multi-user implementation commitment today — see
[docs/roadmap.md](roadmap.md) M5 for where this is picked back up.
