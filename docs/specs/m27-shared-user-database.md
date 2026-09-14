# M27 — Shared per-user database

Status: Planned — not implemented. This specification changes no runtime behavior or existing database.
See [roadmap](../roadmap.md#m27--shared-per-user-database) and
[PR checklist](pr-plan.md#m27--shared-per-user-database).

## Purpose and existing behavior

Give agents running for the same operating-system user one predictable personal-context store. Today the shared
resolver checks an explicit argument, environment, config file, OpenClaw workspaces, and finally the XDG data
directory. Workspace discovery and differing agent environments can select different databases.

M27 replaces implicit workspace selection and the XDG production fallback with `~/.pctx/people.db`. Sharing
requires the same home directory, filesystem access, and compatible explicit configuration. This does not grant
sandbox access, share across users or machines, or introduce a service or synchronization layer.

## Planned PR

### M27.1 — Adopt the shared user database default

The CLI and MCP server must use this first-match resolution order, independent of working directory or agent:

1. Explicit `--db`/server argument.
2. `PEOPLE_CONTEXT_DB`.
3. `db_path` in `{XDG_CONFIG_HOME or ~/.config}/people-context/config.toml`.
4. `~/.pctx/people.db`, using the user's home directory.

Preserve existing path expansion and override semantics. Keep configuration in its existing XDG config location;
`XDG_DATA_HOME` no longer selects the production default. Remove automatic selection through `OPENCLAW_WORKSPACE`
and `~/.openclaw/workspace`. Explicit paths still support intentionally isolated stores.

Reuse `config.py` and the shared database-opening path. Resolution and diagnostics must not create directories,
open SQLite, apply migrations, or modify data. Parents are created only when a permitted database open needs them.
Preserve existing encryption/key requirements and private-file protections.

### Explicit transition for existing installations

Never automatically move, copy, merge, or delete an old database. When no explicit argument, environment override,
or config-file path wins, and `~/.pctx/people.db` is absent, check all legacy candidates:

- `{OPENCLAW_WORKSPACE}/people-context/people.db` when the workspace is configured and exists;
- `~/.openclaw/workspace/people-context/people.db` when that workspace exists;
- `{XDG_DATA_HOME or ~/.local/share}/people-context/people.db`.

An existing legacy database path blocks creation of a fresh default. Inspect paths without opening database
contents, including encrypted files. Database-opening CLI commands must fail with an actionable error; MCP startup
must refuse before creating/opening the new store, with diagnostics kept off the stdio protocol stream. Explain
that the user must explicitly select an old path with an existing override or deliberately migrate before retrying.
Multiple legacy databases require an explicit choice; do not select one by discovery order or merge them.

An existing new default wins even if legacy files remain. Explicit overrides bypass this legacy discovery guard,
including an explicit choice of the new path. With no legacy file and no new default, normal creation is allowed.
`pctx db-path` continues to print the resolved path without opening a database; its verbose trace must distinguish
the selected default from a blocked transition and explain how to proceed. Merely printing a path does not promise
that opening it will succeed.

### Setup and documentation

Preserve existing setup entries with pinned paths. Newly generated entries retain explicit `--db` and
`PEOPLE_CONTEXT_DB` pinning and existing relative-path anchoring, but no longer pin a database because of
`OPENCLAW_WORKSPACE`. Config-file paths and the new default remain unpinned. Setup must not silently produce a
working-looking default entry when the legacy transition is blocked; report the required explicit choice before
writing configuration. Preserve non-mutating dry-run behavior.

The fictional demo remains at its separate XDG `demo.db` path, ignoring production path resolution and encryption
as today. Docker's explicit `PEOPLE_CONTEXT_DB=/data/people.db` remains an override.

In the implementing PR, update CLI resolution/setup instructions, architecture, plugin guides, and any other
current-behavior descriptions of the old default. Document how to keep an old store via an explicit override and
how to relocate deliberately with all clients stopped and SQLite-consistent backup/copy handling, preserving
encryption and permissions. Do not recommend copying only a live database file while WAL writes may be pending.
Assess the default-path change against the [compatibility promise](../compatibility.md) and
[release policy](../releasing.md), and record the required release classification before shipping. This spec-only
change makes no version bump.

## Acceptance scenarios and verification

- Fresh CLI and MCP environments with the same home select the same new default across working directories,
  OpenClaw workspace settings, and custom XDG data directories. Neither path resolution nor diagnostics writes.
- Argument, environment, and config overrides retain their precedence, expansion, and isolation behavior.
- One legacy file or several legacy files block implicit fresh-store creation; no new store or parent directory
  is created and no old file is changed. Cover both plaintext and encrypted legacy files without inspecting them.
- An existing new default wins despite legacy files. An explicit old or new path bypasses the guard. A workspace
  directory without a database does not block creation. Legacy checks cover all candidates, not just the first.
- CLI refusal, MCP startup refusal, verbose diagnostics, setup refusal, and dry-run behavior agree. Setup retains
  explicit pinning and absolute anchoring but does not introduce workspace-derived pinning or rewrite existing
  entries merely because the default changed.
- Demo isolation, explicit Docker paths, private creation, and encryption requirements remain intact.

Extend existing configuration, setup, CLI, MCP/runtime, and SQLite tests using temporary homes and fictional data;
never inspect a developer's personal database. Run focused checks, then repository-required Ruff, mypy, pytest,
and packaging checks for M27.1. The documentation-only preparation is verified by link checks, consistency review,
and `git diff --check`; it does not claim these runtime acceptance scenarios already pass.

## Boundaries and dependencies

One implementation PR, reusing the delivered resolver, runtime, setup, and storage protections. No dependency on
unfinished milestones. No schema migration, new CLI migration command, dependency, automatic relocation, database
merge, new agent-specific configuration layer, or change to disclosure controls. Existing SQLite concurrency
behavior remains in force; a shared path is not a new cross-agent transaction guarantee.
