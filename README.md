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

**M3 — lifecycle and import delivered.** The complete documented MCP surface is SQLite-backed: identity,
retrieval, writes, guidance, reminders, atomic merge/forget, portable export, and staged email/mbox import.
The companion CLI includes inspection plus audited edit, alias, preference, delete, and reindex commands.
See [docs/roadmap.md](docs/roadmap.md) for the remaining milestone plan.

## Features overview

- **Identity resolution** — exact, normalized, search, and bounded fuzzy name matching with explainable
  scores, alias support, organization/role/relationship hint boosting, and an explicit ambiguity contract.
- **Minimal-disclosure retrieval** — stable person context containing active relationships/affiliations and
  a single ranked facts/interactions budget, with sensitivity and purpose gates.
- **Relationships and organisations** — directed, typed, time-bounded edges between people, and
  affiliations (role + period) with organisations.
- **Facts vs. observations vs. traits** — objective, time-aware facts are kept separate from subjective
  observations and derived communication/behavioural traits, at the schema, API, and response level.
- **Communication guidance** — the server supplies structured signal (traits, friction history, the user's
  own communication philosophy text); the client LLM composes advice in the user's own voice.
- **Reminders** — follow-ups, occasions, and standing communication notes, pulled by clients on demand.
- **Provenance, confidence, sensitivity, audit** on every assertive record, plus forget, merge, and export.
- **Local SQLite persistence** — a single, plain, user-owned file; no network calls, no server-side accounts.

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
| 3 | `db_path` key in `~/.config/people-context/config.toml` (or `$XDG_CONFIG_HOME`) | |
| 4 | Agent workspace auto-detect | `OPENCLAW_WORKSPACE` env var, else `~/.openclaw/workspace`, first existing dir wins; stored as `people-context/people.db` inside it. |
| 5 | XDG data fallback | `~/.local/share/people-context/people.db` (or `$XDG_DATA_HOME`). |

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
```

See [docs/cli.md](docs/cli.md) for the full reference, including direct SQLite access for power users.

## Design principles

- **Local-first.** Everything lives in a single SQLite file the user owns; no network access is required or
  performed.
- **Minimal disclosure.** Context tools return capped, ranked, sensitivity-filtered slices — never full dumps
  of a person's record.
- **Facts vs. observations.** Objective, time-stamped facts are kept structurally separate from subjective
  observations and derived traits; the distinction is preserved through the API and in response formatting.
- **Provenance, confidence, and sensitivity on everything.** Every assertive record — fact, observation,
  trait, relationship, affiliation — carries who/what asserted it, how confident that assertion is, and how
  sensitive it is.
- **No raw emails, transcripts, or conversation logs.** Interaction records are concise summaries only;
  imports extract and stage distilled candidates and never persist source content.
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
              │  sqlite/   mcp/   importers/   cli.py │
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

Dependencies point inward only: `domain` and `app` never import `adapters` or `mcp`. New transports
(a future localhost Streamable HTTP server) and new importers (contacts, vCard, notes) slot in as additional
adapters without touching the core. See [docs/architecture.md](docs/architecture.md) for the full rationale,
including the SOLID mapping and the "self as a person row" and "audit log as sync foundation" decisions.

## Documentation index

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Hexagonal layering, dependency rule, SOLID mapping, entrypoint wiring |
| [docs/data-model.md](docs/data-model.md) | Every table, common metadata columns, FTS5, bitemporal-lite, soft-delete vs. forget |
| [docs/mcp-interface.md](docs/mcp-interface.md) | Full MCP tool surface, parameters, return shapes, annotations, implementation status |
| [docs/identity-resolution.md](docs/identity-resolution.md) | The 5-stage resolution pipeline, scoring, ambiguity contract |
| [docs/communication-guidance.md](docs/communication-guidance.md) | Traits, communication philosophy, reminders, privacy treatment |
| [docs/import.md](docs/import.md) | Extract-and-stage import pipeline (email first), no-raw-content rule |
| [docs/privacy-and-safety.md](docs/privacy-and-safety.md) | Minimal disclosure, sensitivity, audit, forget, threat model |
| [docs/cli.md](docs/cli.md) | `people-context` CLI reference, DB location resolution, direct SQLite access |
| [docs/roadmap.md](docs/roadmap.md) | M0 through M5 milestones |
| [docs/decisions/](docs/decisions/) | Architecture decision records (ADRs) |

## Contributing

- Follow SOLID: one entity per `domain/` module, one use case per `app/` module, narrow `typing.Protocol`
  ports split by concern, dependency inversion at the adapter boundary.
- Lint with `ruff`; line length is 120 characters (`line-length = 120` in `[tool.ruff]`).
- Tests run with `pytest`. New behaviour in `app/` should be tested against an in-memory fake repository to
  prove the port abstraction actually holds (Liskov substitutability), not just against SQLite.
- Domain and app code must not import anything from `adapters` or the `mcp` package.

## License

MIT. See [LICENSE](LICENSE).
