# people-context

<!-- mcp-name: io.github.jinyangwang27/people-context -->

[![CI](https://github.com/JinyangWang27/people-context/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/JinyangWang27/people-context/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/JinyangWang27/people-context/graph/badge.svg)](https://codecov.io/gh/JinyangWang27/people-context)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/JinyangWang27/people-context/badge)](https://scorecard.dev/viewer/?uri=github.com/JinyangWang27/people-context)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/13681/badge)](https://www.bestpractices.dev/projects/13681)
[![PyPI](https://img.shields.io/pypi/v/people-context)](https://pypi.org/project/people-context/)
[![PyPI downloads](https://img.shields.io/pypi/dm/people-context)](https://pypi.org/project/people-context/)
[![Python](https://img.shields.io/pypi/pyversions/people-context)](https://pypi.org/project/people-context/)
[![License](https://img.shields.io/github/license/JinyangWang27/people-context)](https://github.com/JinyangWang27/people-context/blob/main/LICENSE)

A local-first [MCP](https://modelcontextprotocol.io) server and CLI that give AI agents durable, user-owned
context about the people in your life.

## Why

A model can recognize a name but does not know who that person is in the user's life. `people-context`
keeps identity, aliases, relationships, roles, durable facts, concise interactions, communication preferences,
and follow-ups in a local SQLite file, then exposes narrow tools that resolve identity and disclose only what a
request needs.

## Agent plugins

### Codex

Install the repository as a Codex marketplace and add the bundled plugin:

```bash
codex plugin marketplace add JinyangWang27/people-context
codex plugin add people-context@people-context-plugins
```

Start a new Codex session after installation. The plugin launches the local stdio server, stores data outside
the installed plugin copy, and keeps sensitive-context and full-export tools disabled by default. See
[docs/codex-plugin.md](docs/codex-plugin.md) for runtime, update, validation, and publishing details.

### Claude Code

Install the repository as a Claude Code marketplace and add the bundled plugin:

```bash
claude plugin marketplace add JinyangWang27/people-context
claude plugin install people-context@people-context-plugins
```

Restart Claude Code or run `/reload-plugins` after installation. The plugin launches the local stdio server,
stores data outside the installed plugin copy, and keeps sensitive-context and full-export tools disabled by
default. See [docs/claude-code-plugin.md](docs/claude-code-plugin.md) for runtime, update, validation, and
publishing details.

### OpenClaw

Install the published plugin from ClawHub:

```bash
openclaw plugins install clawhub:openclaw-plugin-people-context
openclaw plugins inspect people-context --runtime --json
```

The native OpenClaw plugin connects to the opt-in loopback HTTP server, which must be running separately.
Persistent writes are optional, and sensitive-context and export wrappers are not exposed. See
[docs/openclaw-plugin.md](docs/openclaw-plugin.md) for configuration, security, validation, and ClawHub publishing
details.

## Features

- explainable exact/normalized/FTS/fuzzy identity resolution with aliases and ambiguity handling;
- bounded person context with sensitivity and purpose gates;
- canonical relationship vocabulary, synonyms, inverse pairs, symmetric types, and uncategorized extensions;
- minimal-disclosure relationship graph and shortest-path MCP tools with explicit caps/truncation;
- ordinary-disclosure staleness reporting over stored interaction recency, as an MCP tool and CLI command;
- ordinary-disclosure upcoming birthdays and dated reminders, with real leap-day projection, as an MCP tool
  and CLI command;
- organizations and time-aware affiliations;
- separate facts, observations, traits, and concise interaction summaries;
- communication guidance grounded in traits, interaction friction, reminders, and user-authored philosophy;
- reviewable email/mbox/vCard/calendar/LinkedIn/Outlook/WhatsApp/agent-candidate imports without retaining raw
  source content;
- optional pinned multilingual Model2Vec + `sqlite-vec` semantic retrieval;
- atomic audit plus replay changelog/HLC capture for every durable write;
- merge, forget/redaction, unchanged JSON export, and safe Obsidian vault export;
- stdio by default and explicit unauthenticated loopback-only Streamable HTTP.

## Demo

A packaged fictional dataset is the fastest way to see identity resolution, graph traversal, and bounded
context without touching real data:

```bash
uvx --from people-context pctx demo --reset
```

The demo always writes its own dedicated database at
`{XDG_DATA_HOME or ~/.local/share}/people-context/demo.db`. It ignores `--db`, `PEOPLE_CONTEXT_DB`, the config
file, and workspace discovery, and `--reset` replaces only that file plus its `-wal`/`-shm` companions, so a
real database is never read or modified. Seeding writes audited fictional people, handles, affiliations, facts,
interactions, and a connected relationship graph, then prints the path-targeted server command and concrete
tool calls that use the ids it just created:

```text
Demo database: /home/you/.local/share/people-context/demo.db
Start MCP server: people-context-mcp --db /home/you/.local/share/people-context/demo.db
resolve_person {"query": "Amina Hassan"}
get_relationship_graph {"person_id": "<amina-id>", "depth": 2}
find_connection {"person_a": "<self-id>", "person_b": "<sofia-id>"}
```

Person ids are generated per seed, so the printed values differ from the placeholders above. Start the printed
server command in an MCP client and run the printed calls verbatim. See
[docs/cli.md](docs/cli.md#packaged-demo).

## Quick start

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

The fastest path from discovery to a working stdio server is a zero-clone, zero-install run of the published
`people-context` distribution:

```bash
uvx --from people-context people-context
```

For a persistent installation of both the MCP server and the human-operated CLI:

```bash
uv tool install people-context
people-context
pctx --help
```

`people-context-mcp` remains an equivalent MCP server command for existing client configurations. `pctx` is the
human-operated CLI.

For local development:

```bash
git clone https://github.com/JinyangWang27/people-context.git
cd people-context
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
uv run pctx export-vault --output ~/PeopleVault
```

The directory is accepted only when nonexistent, empty, or already marked with `.people-context-vault`.
Re-export is byte-deterministic over unchanged data. Sensitive/restricted facts require the explicit
`--include-sensitive` flag; exported files are outside server disclosure controls.

## Other MCP clients

Clients that support local stdio MCP servers can use:

```json
{
  "mcpServers": {
    "people-context": {
      "command": "uvx",
      "args": ["--from", "people-context", "people-context"]
    }
  }
}
```

## Desktop app and editors

A native-UV [MCPB](https://github.com/modelcontextprotocol/mcpb) bundle installs the server into MCP-aware
desktop hosts (such as Claude Desktop) with one click; the host's `uv` runtime installs the pinned
`people-context` release and runs the same stdio server. Build it with `mcpb/build.sh`.

Cursor, Windsurf, and VS Code use the canonical `uvx --from people-context people-context` invocation with
per-editor config files. See [docs/desktop-and-editors.md](docs/desktop-and-editors.md).

## Obsidian

A desktop-only, read-only Obsidian plugin lives under [`obsidian-plugin/`](obsidian-plugin/). It renders a
browsable person index and per-person briefs from `pctx list --json` and `pctx brief <person-id> --json` — it never
opens SQLite, never writes, and never requests sensitive disclosure. Build and install it with:

```bash
cd obsidian-plugin && npm ci --no-audit --no-fund && npm run build
```

then copy `build/main.js`, `build/manifest.json`, and `build/styles.css` into
`<vault>/.obsidian/plugins/people-context/`. Rendering into a synchronized vault takes that content outside this
project's local-first perimeter. See [docs/obsidian-plugin.md](docs/obsidian-plugin.md) for settings, encrypted-database
behavior, process-execution safety, and release mirroring.

## Docker (optional)

An optional non-root container image runs the same stdio MCP server. It is a convenience distribution, not the
default path and not a security sandbox: the server still runs local Python with your filesystem permissions.
Mount storage at `/data`; the image sets `PEOPLE_CONTEXT_DB=/data/people.db`.

```bash
docker volume create people-context-data
docker run --rm -i -v people-context-data:/data ghcr.io/jinyangwang27/people-context:latest
```

Loopback HTTP is not the container default, and the runtime makes no outbound network request. The published
image is `linux/amd64`; on other architectures, build it locally. GHCR publishes packages privately at first, so
anonymous pulls work only after the package is made public once. See [docs/docker.md](docs/docker.md) for
bind-mount ownership, the CLI entrypoint, MCP client configuration, and publishing.

## Security model

This project executes local Python with the launching user's filesystem permissions. The database is plaintext
SQLite by default; rely on filesystem permissions and full-disk encryption, or opt into at-rest encryption as
shown below. Ordinary MCP discovery excludes elevated
sensitive context and full export. Operator-gated tools require process environment flags; models cannot enable
them through arguments. Vault export is intentionally CLI-only. For a dated, sourced comparison with
cloud-hosted memory tools on storage, breach and legal exposure, offline operation, and deletion, see
[docs/privacy-and-safety.md](docs/privacy-and-safety.md#local-first-versus-cloud-hosted-memory-as-of-2026-08-05).

## Optional at-rest encryption

Plaintext SQLite remains the default. Opt into SQLCipher explicitly:

```bash
uv sync --extra encrypted
export PEOPLE_CONTEXT_DB_KEY='your passphrase'
uv run pctx --encrypted list
uv run people-context-mcp --encrypted
```

The key is read only from `PEOPLE_CONTEXT_DB_KEY` — never a flag value, config file, or log. Without a non-empty
key the flag refuses to start and never falls back to plaintext, and losing the key means losing the data.
Prebuilt wheels cover glibc-based Linux x86_64 only; macOS, Windows, arm64, and musl/Alpine need a locally built
`sqlcipher3`. See
[docs/privacy-and-safety.md](docs/privacy-and-safety.md#optional-at-rest-encryption) for what this protects.

## Optional semantic search

The base install downloads nothing. Opt in explicitly:

```bash
uv sync --extra semantic
uv run pctx reindex --semantic
```

Only that reindex command may download the pinned multilingual model. Server startup/search are cache-only.

## Database location

Server and CLI use the first available source:

1. explicit `--db`/server argument;
2. `PEOPLE_CONTEXT_DB`;
3. `db_path` in the XDG config file;
4. `OPENCLAW_WORKSPACE` or `~/.openclaw/workspace`;
5. the XDG data fallback.

Inspect the selected path with `uv run pctx db-path -v`.

## CLI overview

```bash
uv run pctx db-path [-v]
uv run pctx list [--all]
uv run pctx search <query>
uv run pctx show <person>
uv run pctx stale [--category C] [--threshold-days N] [--limit N]
uv run pctx upcoming [--window-days N] [--person PERSON]
uv run pctx doctor [--json] [--only CODE[,CODE...]]
uv run pctx stats [--json] [--include-path]
uv run pctx export [--output FILE]
uv run pctx relationship-types
uv run pctx relationship-types add TYPE --category C [--inverse T | --symmetric]
uv run pctx normalize-relationships [--apply]
uv run pctx export-vault --output DIR [--include-sensitive]
uv run pctx export-vcard [--output FILE] [--include-sensitive] [--version 3.0|4.0]
uv run pctx edit PERSON [--name NAME] [--summary TEXT]
uv run pctx add-alias PERSON VALUE [--kind KIND]
uv run pctx set communication_philosophy VALUE
uv run pctx delete PERSON [--yes]
uv run pctx sync push --output DIR
uv run pctx sync pull --input PATH [--yes]
uv run pctx sync-log [--limit N] [--entity ID] [--payloads]
uv run pctx reindex [--semantic]
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
| [docs/data-model.md](docs/data-model.md) | Schema, migrations, and perspective `display_type` |
| [docs/relationship-graph.md](docs/relationship-graph.md) | Vocabulary, normalization, perspective, traversal, curation |
| [docs/vault-export.md](docs/vault-export.md) | Layout, marker safety, determinism, sensitivity |
| [docs/mcp-interface.md](docs/mcp-interface.md) | MCP tools and stable response contracts |
| [docs/compatibility.md](docs/compatibility.md) | What stays stable across releases for MCP, DB, CLI, and JSON |
| [docs/cli.md](docs/cli.md) | CLI commands and DB resolution |
| [docs/design/sync.md](docs/design/sync.md) | Sync design and delivered local foundations |
| [docs/releasing.md](docs/releasing.md) | PyPI trusted publishing, Codecov, and release procedure |
| [docs/mcp-registry.md](docs/mcp-registry.md) | MCP Registry namespace, `server.json`, and community-directory submission matrix |
| [docs/desktop-and-editors.md](docs/desktop-and-editors.md) | Native-UV MCPB Desktop bundle and Cursor/Windsurf/VS Code snippets |
| [docs/docker.md](docs/docker.md) | Optional non-root stdio Docker image, data volume, and GHCR publishing |
| [docs/claude-code-plugin.md](docs/claude-code-plugin.md) | Claude Code install, runtime, privacy, validation, and publishing |
| [docs/codex-plugin.md](docs/codex-plugin.md) | Codex install, runtime, privacy, validation, and publishing |
| [docs/openclaw-plugin.md](docs/openclaw-plugin.md) | OpenClaw install, runtime, privacy, validation, and ClawHub publishing |
| [docs/obsidian-plugin.md](docs/obsidian-plugin.md) | Obsidian read-only panes, subprocess safety, encryption, and mirrored releases |
| [docs/privacy-and-safety.md](docs/privacy-and-safety.md) | Disclosure, audit, forget, threat model |
| [docs/use-cases](docs/use-cases/README.md) | Narrative recipes for onboarding, meeting prep, follow-up, migration, and auditing |
| [docs/evals.md](docs/evals.md) | Evaluation harness, fixed tasks, scoring rules, and dated recorded results |
| [docs/roadmap.md](docs/roadmap.md) | Delivered milestones and planned work |
| [docs/specs](docs/specs/) | One implementation spec per planned milestone |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for issue and private-security reporting, architecture constraints,
validation commands, and the pull-request review process.

## License

MIT. See [LICENSE](LICENSE).
