# people-context-mcp

[![codecov](https://codecov.io/gh/JinyangWang27/people-context-mcp/graph/badge.svg)](https://codecov.io/gh/JinyangWang27/people-context-mcp)

A local-first [MCP](https://modelcontextprotocol.io) server that gives AI coding agents and personal agent
systems durable, user-owned context about the people they mention.

## Why

A model can recognize a name but does not know who that person is in the user's life. `people-context-mcp`
keeps identity, aliases, relationships, roles, durable facts, concise interactions, communication preferences,
and follow-ups in a local SQLite file, then exposes narrow tools that resolve identity and disclose only what a
request needs.

## Status

**M7 — relationship graph & vault export delivered.** Relationship types are data-backed and canonicalized,
relationship reads add perspective `display_type`, two bounded read-only graph tools are available, and the CLI
can generate a deterministic marker-owned Obsidian vault. Existing field meanings, resolution behavior, JSON
export envelope, and M6 sync capture remain compatible. The `merge_people` result intentionally adds the
`duplicate_relationships_removed` field, reporting how many overlapping relationship edges were collapsed;
callers that ignore unknown response fields require no changes.

## Features

- explainable exact/normalized/FTS/fuzzy identity resolution with aliases and ambiguity handling;
- bounded person context with sensitivity and purpose gates;
- canonical relationship vocabulary, synonyms, inverse pairs, symmetric types, and uncategorized extensions;
- minimal-disclosure relationship graph and shortest-path MCP tools with explicit caps/truncation;
- organizations and time-aware affiliations;
- separate facts, observations, traits, and concise interaction summaries;
- communication guidance grounded in traits, interaction friction, reminders, and user-authored philosophy;
- reviewable email/mbox/vCard/agent-candidate imports without retaining raw source content;
- optional pinned multilingual Model2Vec + `sqlite-vec` semantic retrieval;
- atomic audit plus replay changelog/HLC capture for every durable write;
- merge, forget/redaction, unchanged JSON export, and safe Obsidian vault export;
- stdio by default and explicit unauthenticated loopback-only Streamable HTTP.

## Quick start

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

Install the published package:

```bash
uv tool install people-context-mcp
people-context-mcp
```

For local development:

```bash
git clone https://github.com/JinyangWang27/people-context-mcp.git
cd people-context-mcp
uv sync
uv run people-context-mcp
```

Loopback HTTP is opt-in:

```bash
uv run people-context-mcp --http --host 127.0.0.1 --port 8765
```

The endpoint is `http://127.0.0.1:8765/mcp`. It is unauthenticated and must be treated as accessible to other
local processes. Prefer stdio.

## Example: graph-aware context and vault

After recording people and relationships through MCP, inspect structure with `get_relationship_graph` or
`find_connection`, then create a human-browsable vault:

```bash
uv run people-context export-vault --output ~/PeopleVault
```

The directory is accepted only when nonexistent, empty, or already marked with `.people-context-vault`.
Re-export is byte-deterministic over unchanged data. Sensitive/restricted facts require the explicit
`--include-sensitive` flag; exported files are outside server disclosure controls.

## MCP client configuration

Claude Code:

```bash
claude mcp add people-context -- uv run --directory /path/to/people-context-mcp people-context-mcp
```

Generic stdio configuration:

```json
{
  "mcpServers": {
    "people-context": {
      "command": "uv",
      "args": ["run", "--directory", "<repo>", "people-context-mcp"]
    }
  }
}
```

## Security model

This project executes local Python with the launching user's filesystem permissions. The database is plaintext
SQLite; rely on filesystem permissions and full-disk encryption. Ordinary MCP discovery excludes elevated
sensitive context and full export. Operator-gated tools require process environment flags; models cannot enable
them through arguments. Vault export is intentionally CLI-only.

## Optional semantic search

The base install downloads nothing. Opt in explicitly:

```bash
uv sync --extra semantic
uv run people-context reindex --semantic
```

Only that reindex command may download the pinned multilingual model. Server startup/search are cache-only.

## Database location

Server and CLI use the first available source:

1. explicit `--db`/server argument;
2. `PEOPLE_CONTEXT_DB`;
3. `db_path` in the XDG config file;
4. `OPENCLAW_WORKSPACE` or `~/.openclaw/workspace`;
5. the XDG data fallback.

Inspect the selected path with `uv run people-context db-path -v`.

## CLI overview

```bash
uv run people-context db-path [-v]
uv run people-context list [--all]
uv run people-context search <query>
uv run people-context show <person>
uv run people-context export [--output FILE]
uv run people-context relationship-types
uv run people-context relationship-types add TYPE --category C [--inverse T | --symmetric]
uv run people-context normalize-relationships [--apply]
uv run people-context export-vault --output DIR [--include-sensitive]
uv run people-context edit PERSON [--name NAME] [--summary TEXT]
uv run people-context add-alias PERSON VALUE [--kind KIND]
uv run people-context set communication_philosophy VALUE
uv run people-context delete PERSON [--yes]
uv run people-context sync-log [--limit N] [--entity ID] [--payloads]
uv run people-context reindex [--semantic]
```

See [docs/cli.md](docs/cli.md).

## Architecture

The codebase follows ports and adapters:

```text
adapters (SQLite, MCP, filesystem, imports, CLI)
        ↓ implement
ports (narrow Protocols)
        ↑ used by
app (use cases and policy)
        ↓ operates on
domain (entities and values)
```

Dependencies point inward. Vocabulary normalization and graph caps live in app/domain; recursive SQL and file
writing live in adapters. One composition root wires both stdio and HTTP.

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Layering, dependency rule, entrypoint wiring |
| [docs/data-model.md](docs/data-model.md) | Schema including migrations 002/003 and `display_type` |
| [docs/relationship-graph.md](docs/relationship-graph.md) | Vocabulary, normalization, perspective, traversal, curation |
| [docs/vault-export.md](docs/vault-export.md) | Layout, marker safety, determinism, sensitivity |
| [docs/mcp-interface.md](docs/mcp-interface.md) | MCP tools and stable response contracts |
| [docs/cli.md](docs/cli.md) | CLI commands and DB resolution |
| [docs/design/sync.md](docs/design/sync.md) | Sync design and M6 local foundations |
| [docs/releasing.md](docs/releasing.md) | PyPI trusted publishing, Codecov, and release procedure |
| [docs/privacy-and-safety.md](docs/privacy-and-safety.md) | Disclosure, audit, forget, threat model |
| [docs/roadmap.md](docs/roadmap.md) | M0 through M7 |

## Contributing

- Keep domain/app independent of SQLite, MCP, and filesystem adapters.
- Use narrow Protocol ports and fake-port tests for app behavior.
- Run `uv run ruff check .` and `uv run pytest -q`.
- Line length is 120.

## License

MIT. See [LICENSE](LICENSE).
