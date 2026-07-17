# 0002. Storage: SQLite

## Status

Accepted.

## Context

The system needs local, durable, queryable storage for people, aliases, relationships, organisations, facts,
observations, traits, interactions, reminders, preferences, import staging, and an audit log (see
[docs/data-model.md](../data-model.md)), plus text search over names/aliases and interaction summaries (see
[docs/identity-resolution.md](../identity-resolution.md)). The user additionally required that the database
be directly viewable and editable with standard, widely available tools, not locked behind this project's
own code (see [docs/cli.md](../cli.md)).

## Decision

Use **SQLite**, in a single user-owned file, with `PRAGMA journal_mode=WAL` for safe concurrent
reader/writer access and FTS5 virtual tables (`person_search`, `interaction_search`) for ranked text search.
Schema changes are applied through a small migration runner keyed off `PRAGMA user_version`, with migration
SQL files under `adapters/sqlite/migrations/`. Accessed only through the standard library `sqlite3` module —
no external database driver.

## Consequences

- The database is a single, portable file. It works out of the box with `sqlite3` (the CLI shell), DB
  Browser for SQLite, and Datasette — satisfying the requirement that users can inspect/edit their own data
  without this project's tooling (see [docs/cli.md](../cli.md#direct-database-access)).
- WAL mode allows the CLI and the MCP server to open the same file concurrently without corrupting it,
  though it does not substitute for filesystem-level access control (see
  [docs/privacy-and-safety.md](../privacy-and-safety.md#threat-model-notes)).
- FTS5 gives prefix/token search "for free," without a separate search service — this backs stage 3 of
  identity resolution and the `search_people`/CLI `search` paths.
- SQLite has no native encryption; the file is plaintext on disk. Mitigated today by recommending OS-level
  disk encryption, with SQLCipher noted as a possible future option if that changes — see
  [docs/privacy-and-safety.md](../privacy-and-safety.md#threat-model-notes).
- Because everything is one file, `sqlite-vec` (an embeddings extension for SQLite) can be added later,
  in the same file, for semantic person/interaction retrieval (M4 — see [docs/roadmap.md](../roadmap.md)),
  without a storage migration.

## Alternatives considered

- **JSON files (one per person, or one flat file)** — simplest possible format, trivially human-readable.
  Rejected because it has no real query power: relationship traversal, time-bounded validity queries ("who
  was her manager in 2024"), and ranked text search would all have to be reimplemented in application code,
  and concurrent-write safety would need to be built from scratch.
- **An embedded graph database** — relationships between people are naturally graph-shaped, and a graph
  database would make traversal queries more natural to express. Rejected as a heavy dependency for what is,
  in practice, a small graph (a single user's network of people) — SQLite's relational model handles the
  actual query patterns (direct relationship lookups, affiliation history, fact retrieval) without needing
  general graph-traversal machinery, and keeps the "one plain file" property intact.
- **`sqlite-vec` embeddings from the start** — deferred rather than rejected: useful for semantic retrieval,
  but not needed for the v1 exact/normalized/FTS/fuzzy resolution pipeline, so it is picked up later (M4) in
  the same SQLite file rather than adding an embedding dependency to the initial scaffold.
