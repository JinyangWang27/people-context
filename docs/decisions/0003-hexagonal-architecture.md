# 0003. Architecture: hexagonal (ports and adapters)

## Status

Accepted.

## Context

The server needs to support more than one way of being reached (stdio MCP now, a localhost Streamable HTTP
transport and a CLI later) and more than one way of bringing data in (direct MCP/CLI writes now, file-based
importers later — see [docs/import.md](../import.md)). The core logic — identity resolution, minimal-
disclosure context assembly, recording facts/observations/traits, communication guidance, curation — should
not need to be rewritten, or even touched, every time a new transport or importer is added. The user also
required the codebase to follow SOLID throughout (see [docs/architecture.md](../architecture.md#solid-mapping)).

## Decision

Structure the codebase as a hexagonal (ports & adapters) system: a `domain` + `app` core with **zero**
imports from the MCP SDK or `sqlite3`, a set of narrow `typing.Protocol` ports (`PersonReader`,
`PersonWriter`, `AuditLog`, `Clock`) that the core depends on, and adapters (`adapters/sqlite`,
`adapters/mcp`, `adapters/importers`, `cli.py`) that implement those ports and handle all I/O. Dependencies
point inward only (`adapters → ports → app → domain`); wiring — constructing concrete adapters and injecting
them into use cases — happens exclusively at entrypoints (`adapters/mcp/server.py:build_server`,
`cli.py:main`). Full detail, including the layer diagram and the SOLID mapping, is in
[docs/architecture.md](../architecture.md).

## Consequences

- New transports (localhost Streamable HTTP, M4) and new importers (contacts/vCard, notes) are purely
  additive adapters; `domain` and `app` do not change to support them.
- `app`-level tests can run against an in-memory fake implementing `PersonReader`/`PersonWriter`, instead of
  a real SQLite database, proving both that the tests are fast and that the port abstraction genuinely holds
  (Liskov substitutability).
- The CLI and the MCP server share the exact same use case classes, so a person created via
  `people-context` and a person created via `remember_person` are recorded, audited, and provenance-tracked
  identically — there is no risk of the two surfaces drifting in behaviour.
- The cost of this decision is indirection: even the M0 vertical slice (`resolve_person`, `search_people`,
  `remember_person`) is spread across `domain`, `app`, `ports`, and `adapters/sqlite` plus
  `adapters/mcp`, rather than living in one file. This is accepted deliberately, in exchange for the adapter
  swap-in properties above holding from the very first commit rather than being retrofitted later.

## Alternatives considered

- **MCP-first monolith** — put tool handlers directly against SQLite, with resolution/context/recording
  logic written inline in `adapters/mcp/tools/*.py`. Would be faster to write initially and would remove a
  layer of indirection for the M0 feature set. Rejected because the core logic (identity resolution,
  minimal disclosure, communication guidance assembly) is expected to **outlive any one interface** — the
  project's own roadmap already commits to a second transport (Streamable HTTP, M4) and a CLI that must
  behave identically to the MCP tools (see [docs/cli.md](../cli.md)); an MCP-first monolith would require
  either duplicating that logic per interface or retrofitting the exact port boundary this ADR adopts up
  front, at a point where more code already depends on the wrong boundary.
