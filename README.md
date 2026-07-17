# people-context-mcp

A local-first [MCP](https://modelcontextprotocol.io) server that gives AI coding agents and personal agent
systems contextual knowledge about the people the user mentions.

## The problem

An AI agent can recognise the string "Wang" appearing in a prompt. It has no idea who Wang is in *your* life:
whether it's your manager, your sister, a former colleague, or a stranger who emailed you once. Coding agents
and personal assistants increasingly need this kind of context — identity, relationship, role history,
communication style, and outstanding follow-ups — to act usefully and to avoid asking the user to re-explain
the same relationships in every session.

`people-context-mcp` stores that context locally, on disk, under the user's control, and exposes it to any
MCP-compatible client through a small set of tools: identity resolution (names, aliases, transliterations,
ambiguity), relationships, organisations/roles, time-aware facts versus subjective observations and traits,
interaction summaries, communication guidance framed by the user's own philosophy, reminders, and safe
persistence with provenance, confidence, sensitivity, audit, and forget/merge/export.

## Status

**M5 — sync groundwork delivered as design.** M0–M4 behaviour is unchanged. The
[sync design](docs/design/sync.md) concludes that the current audit log alone is not a replayable replication
source and proposes a dedicated changelog; M5 adds no sync runtime, transport, dependency, or schema migration.

## Features overview

- **Identity resolution** — exact, normalized, search, and bounded fuzzy name matching with explainable
  scores, alias support, organization/role/relationship hint boosting, and an explicit ambiguity contract.
- **Minimal-disclosure retrieval** — stable person context containing active relationships/affiliations and
  a single ranked facts/interactions budget, with sensitivity and purpose gates.
- **Optional multilingual semantic retrieval** — cosine search across active people and public/personal
  interaction summaries, using a pinned local Model2Vec model and same-file `sqlite-vec` index.
- **Relationships and organisations** — directed, typed, time-bounded edges between people, and
  affiliations (role + period) with organisations.
- **Facts vs. observations vs. traits** — objective, time-aware facts are kept separate from subjective
  observations and derived communication/behavioural traits, at the schema, API, and response level.
- **Communication guidance** — the server supplies structured signal (traits, friction history, the user's
  own communication philosophy text); the client LLM composes advice in the user's own voice.
- **Reminders** — follow-ups, occasions, and standing communication notes, pulled by clients on demand.
- **Provenance, confidence, sensitivity, audit** on every assertive record, plus forget, merge, and export.
- **Reviewable imports** — email/mbox headers, vCard contacts, and strict agent-extracted candidates are
  staged atomically; raw messages, vCard notes, and source notes are not persisted.
- **Local SQLite persistence** — a single, plain, user-owned file; no server-side accounts. Stdio is the
  default transport, with an explicit unauthenticated loopback-only HTTP option.

## Quick start

Requires Python 3.11 or later and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone <this-repo>
cd people-context-mcp
uv sync
```

Run the server directly (stdio transport):

```bash
uv run people-context-mcp
```

For a local HTTP client, opt into Streamable HTTP on loopback:

```bash
uv run people-context-mcp --http --host 127.0.0.1 --port 8765
```

The HTTP endpoint is `http://127.0.0.1:8765/mcp`. It is unauthenticated and intentionally binds only to
`127.0.0.1`; every process running as any local user that can reach loopback should be treated as a potential
client. DNS-rebinding checks restrict accepted hosts and browser origins to `127.0.0.1` and `localhost`.
Remote binding and authenticated HTTP access are deferred.

### Security model

Installing an integration that starts this project through `uv` executes local Python code with your user
account's filesystem permissions; it is not a sandboxed extension. The database is plaintext SQLite, so rely
on normal filesystem permissions and full-disk encryption for at-rest protection. Prefer stdio. Loopback HTTP
is unauthenticated and reachable by other local processes.

Ordinary MCP discovery excludes sensitive/restricted context and complete export. An operator may deliberately
restart an elevated server with `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE=1` and/or
`PEOPLE_CONTEXT_MCP_ENABLE_EXPORT=1`; models cannot enable these capabilities through tool arguments. Routine
full export remains available through the human-operated `people-context export` CLI.

### Optional semantic search

The base install does not include embedding dependencies and never downloads a model. Opt in and explicitly
build the derived index with:

```bash
uv sync --extra semantic
uv run people-context reindex --semantic
```

The command announces the pinned
[`minishlab/potion-multilingual-128M`](
https://huggingface.co/minishlab/potion-multilingual-128M/tree/73908c3438cf03b6a01bcb9611d62b23d0726f08
) revision, its Hugging Face URL, the approximately 512 MB download, and the resolved cache directory before
any network access. The model covers 101 languages, including Chinese. Ordinary server startup and
`semantic_search` are cache-only and never download; a missing package/model/index returns an actionable
`not_available` result.

### Wire into Claude Code

```bash
claude mcp add people-context -- uv run --directory /path/to/people-context-mcp people-context-mcp
```

### Wire into any other MCP client

`people-context-mcp` speaks standard MCP over stdio, so it works with any compliant client — Claude Code,
Codex, OpenClaw, Levey, or your own agent harness. A generic client configuration entry looks like:

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

Replace `<repo>` with the absolute path to this checkout.

## Database location

The server and CLI resolve the SQLite database path the same way, checking each of the following in order
and using the first one that resolves (full detail in [docs/cli.md](docs/cli.md) and
[docs/data-model.md](docs/data-model.md)):

| Order | Source | Notes |
|---|---|---|
| 1 | `--db` flag (CLI) / server argument | Explicit, always wins. |
| 2 | `PEOPLE_CONTEXT_DB` environment variable | |
| 3 | `db_path` in `~/.config/people-context/config.toml` or `$XDG_CONFIG_HOME` | |
| 4 | Agent workspace | `OPENCLAW_WORKSPACE`, then `~/.openclaw/workspace`; stores `people-context/people.db`. |
| 5 | XDG data fallback | `~/.local/share/people-context/people.db` or `$XDG_DATA_HOME`. |

The chosen path is logged to stderr at startup and can be inspected at any time with `people-context db-path`
(add `-v` to see which source won and why).

## CLI overview

A `people-context` CLI ships alongside the server so the data is always inspectable without an MCP client:

```bash
uv run people-context db-path [-v]
uv run people-context list [--all]
uv run people-context search <query>
uv run people-context show <person>
uv run people-context export [--output FILE]
uv run people-context edit PERSON [--name NAME] [--summary TEXT]
uv run people-context add-alias PERSON VALUE [--kind KIND]
uv run people-context set communication_philosophy VALUE
uv run people-context delete PERSON [--yes]
uv run people-context reindex
uv run people-context reindex --semantic
```

See [docs/cli.md](docs/cli.md) for the full reference, including direct SQLite access for power users.

## Design principles

- **Local-first, with no surprise network activity.** Everything lives in a single SQLite file the user
  owns. Only the explicit `reindex --semantic` command may download the optional pinned embedding model;
  serving and searching are cache-only.
- **Minimal disclosure.** Context tools return capped, ranked, sensitivity-filtered slices — never full dumps
  of a person's record.
- **Facts vs. observations.** Objective, time-stamped facts are kept structurally separate from subjective
  observations and derived traits; the distinction is preserved through the API and in response formatting.
- **Provenance, confidence, and sensitivity on everything.** Every assertive record — fact, observation,
  trait, relationship, affiliation — carries who/what asserted it, how confident that assertion is, and how
  sensitive it is.
- **No raw emails, transcripts, or conversation logs.** Interaction records are concise summaries only;
  imports extract and stage distilled candidates and never persist source content. Email Subject values are
  replaced with a fixed neutral summary before staging.
- **Approval-gated writes.** Every write and destructive MCP tool is annotated accordingly, so MCP clients
  can apply their own approval flows before mutating the store.
- **The user owns the data.** The database is a plain, documented SQLite file, directly accessible with
  standard tools, and fully exportable.

## Architecture at a glance

The codebase follows a hexagonal (ports & adapters) layout: a `domain` + `app` core with **zero** imports from
the MCP SDK or `sqlite3`, surrounded by adapters that plug the core into the outside world.

```
              ┌─────────────────────────────────────┐
              │              adapters                │
              │ sqlite/  mcp/  email/vcard  cli.py    │
              └───────────────┬───────────────────────┘
                               │ implements ports
              ┌────────────────▼──────────────────────┐
              │                 ports                  │
              │   PersonReader / PersonWriter / Audit   │
              │            / Clock (Protocols)          │
              └────────────────┬──────────────────────┘
                               │ depended on by
              ┌────────────────▼──────────────────────┐
              │                  app                   │
              │  use cases: resolve, search, remember…  │
              └────────────────┬──────────────────────┘
                               │ operates on
              ┌────────────────▼──────────────────────┐
              │                domain                  │
              │  Person, Relationship, Fact, Trait, …   │
              └─────────────────────────────────────────┘
```

Dependencies point inward only: `domain` and `app` never import `adapters` or `mcp`. One `build_server()`
registers tools for both stdio and localhost Streamable HTTP; source adapters feed one shared staging path.
See [docs/architecture.md](docs/architecture.md) for the full rationale, including the SOLID mapping,
the "self as a person row" choice, and the audit log's assessed limits as a sync foundation.

## Documentation index

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Hexagonal layering, dependency rule, SOLID mapping, entrypoint wiring |
| [docs/data-model.md](docs/data-model.md) | Tables, metadata, FTS5, bitemporal-lite, soft-delete vs. forget |
| [docs/design/sync.md](docs/design/sync.md) | M5 multi-device replication and multi-user design |
| [docs/mcp-interface.md](docs/mcp-interface.md) | MCP tools, parameters, return shapes, annotations, status |
| [docs/identity-resolution.md](docs/identity-resolution.md) | Resolution pipeline, scoring, ambiguity contract |
| [docs/communication-guidance.md](docs/communication-guidance.md) | Traits, philosophy, reminders, privacy |
| [docs/import.md](docs/import.md) | Staged email/mbox, vCard, and agent-candidate import |
| [docs/privacy-and-safety.md](docs/privacy-and-safety.md) | Disclosure, sensitivity, audit, forget, threat model |
| [docs/cli.md](docs/cli.md) | CLI reference, DB location resolution, direct SQLite access |
| [docs/roadmap.md](docs/roadmap.md) | M0 through M5 milestones and post-roadmap candidates |
| [docs/decisions/](docs/decisions/) | Architecture decision records |

## Contributing

- Follow SOLID: one entity per `domain/` module, one use case per `app/` module, narrow `typing.Protocol`
  ports split by concern, dependency inversion at the adapter boundary.
- Lint with `ruff`; line length is 120 characters (`line-length = 120` in `[tool.ruff]`).
- Tests run with `pytest`. New behaviour in `app/` should be tested against an in-memory fake repository to
  prove the port abstraction actually holds (Liskov substitutability), not just against SQLite.
- Domain and app code must not import anything from `adapters` or the `mcp` package.

## License

MIT. See [LICENSE](LICENSE).
