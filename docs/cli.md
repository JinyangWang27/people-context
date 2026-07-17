# CLI

`people-context-mcp` ships a companion CLI, `people-context`, so the data is always directly inspectable and
editable without going through an MCP client. The CLI is built with `argparse`, has no third-party
dependencies, and calls the **same `app`-layer use cases** as the MCP tools do (see
[docs/architecture.md](architecture.md#entrypoint-wiring)), so audit and provenance rules apply identically
regardless of which surface was used to make a change.

## Global options

| Option | Meaning |
|---|---|
| `--db PATH` | Explicit database path, overriding all other resolution sources (see below). |

## Commands

| Command | Purpose | Notes |
|---|---|---|
| `people-context db-path` | Print the resolved database path. | `-v`/`--verbose` prints the full `describe_resolution()` trace — every source checked, in order, and which one won. |
| `people-context list` | List known people: `id`, `canonical_name`, alias count, summary excerpt, as a table. | `--all` includes soft-deleted people. |
| `people-context search QUERY` | Ranked search results for `QUERY`, via the same `SearchPeople` use case `search_people` uses. | |
| `people-context show PERSON` | Show identity, aliases, active relationships, affiliations/roles, the ranked facts/interactions slice, and active communication reminders. | `PERSON` may be an id or a name; it is resolved via `ResolvePerson`. If resolution is ambiguous, the command errors and lists candidates instead of guessing. Uses `GetPersonContext` with `max_items=10` and `include_sensitive=true`. |
| `people-context export [--output FILE]` | Domain-shaped JSON dump of the full portable dataset to stdout, or to `FILE` if given. | Includes soft-deleted people and decoded preferences/audits. |
| `people-context edit PERSON --name/--summary` | Edit canonical identity fields. | Requires at least one field and rejects another active person's canonical name. |
| `people-context add-alias PERSON VALUE [--kind/--lang/--script]` | Add a normalized-deduplicated alias. | |
| `people-context set communication_philosophy VALUE` | Set the supported free-text user preference. | Other keys are rejected. |
| `people-context delete PERSON [--yes]` | Preview and permanently forget a person graph. | Without `--yes`, only `y`/`yes` confirms. |
| `people-context reindex` | Atomically rebuild `person_search` from active people and aliases. | Remains person-only and makes no network call. |
| `people-context reindex --semantic` | Rebuild lexical search, then explicitly download/cache the pinned multilingual model and atomically replace eligible person/interaction vectors plus model metadata. | Prints model id, pinned URL, approximately 512 MB size, and cache directory before download. Sensitive/restricted interactions are excluded. |

All output is plain text (tables for `list`/`search`, structured text for `show`); no third-party formatting
dependency is used.

The curation commands reuse the same app-layer use cases as MCP writes/destructive tools, so they carry the
same audit, validation, and provenance behaviour. `show`, `edit`, `add-alias`, and `delete` share one resolver:
active id first, then `ResolvePerson`; unknown names exit 1 and ambiguous names exit 2 with candidates.

## Database location resolution order

Both the CLI and the MCP server resolve the database path identically, via `config.py:resolve_db_path()`.
The first source below that resolves wins; none of the later sources are consulted once an earlier one
matches. Paths are `~`-expanded. This function never creates the file or its directories — that happens
when the SQLite adapter opens the connection.

| Order | Source | Detail |
|---|---|---|
| 1 | Explicit argument | The CLI's `--db PATH`, or the equivalent argument passed to the server. |
| 2 | `PEOPLE_CONTEXT_DB` environment variable | |
| 3 | `db_path` key in a config file | `{XDG_CONFIG_HOME or ~/.config}/people-context/config.toml`, read via `tomllib`. |
| 4 | Agent workspace auto-detect | Checked in order, first existing directory wins: (a) `OPENCLAW_WORKSPACE` env var, if set and the directory exists; (b) `~/.openclaw/workspace`. The resulting path is `<workspace>/people-context/people.db`. This lives in one small module (`config.py`) specifically so more agent workspaces (e.g. Levey) can be added with a one-line change. |
| 5 | XDG data fallback | `{XDG_DATA_HOME or ~/.local/share}/people-context/people.db`. |

`people-context db-path -v` prints exactly which sources were checked and which one won — this is the
authoritative way to debug "why is it reading/writing the DB I didn't expect."

## Direct database access

The database is a **plain, documented SQLite file** — nothing about it requires this project's own tools to
inspect or modify. Standard SQLite tooling works directly against it:

- **DB Browser for SQLite** — a GUI for browsing and editing tables, running ad hoc queries.
- **Datasette** — a read-oriented web UI, useful for exploring and cross-referencing tables.
- **The `sqlite3` command-line shell** — for scripting or one-off queries, e.g.
  `sqlite3 "$(uv run people-context db-path)" "select canonical_name from persons"`.

Direct SQL edits are legal — it is the user's data, and there is no proprietary lock-in. However, the
`people-context` CLI and the MCP tools are the **preferred** path for changes, because they:

- keep the FTS5 search indexes (`person_search`, `interaction_search`) in sync with the underlying tables
  (see [docs/data-model.md](data-model.md#fts5-tables)), and
- write the corresponding `audit_log` entries automatically, preserving the accountability trail described
  in [docs/privacy-and-safety.md](privacy-and-safety.md).

If a person or alias row is edited directly with an external SQL tool, the FTS index for that row can go
stale until it is rebuilt. `people-context reindex` rebuilds the person-only `person_search` table from
active `persons` and `aliases`; direct SQL changes do not, on their own, get an `audit_log` entry, since the
CLI/MCP layer is what writes those.

The semantic vec0 table is also derived. Direct changes to people or interactions can make it stale;
`people-context reindex --semantic` is the repair command. A failed/offline model fetch occurs before vector
replacement, so the previous semantic index and its metadata remain intact.

## Server entrypoint transport flags

`people-context-mcp` remains stdio by default. `people-context-mcp --http --host 127.0.0.1 --port 8765`
selects Streamable HTTP at `/mcp`. `--host` accepts only `127.0.0.1`; other values are argparse errors with
exit code 2. The HTTP endpoint is unauthenticated loopback, not a remote deployment surface.

## Semantic model download and cache

The optional extra pins
`minishlab/potion-multilingual-128M@73908c3438cf03b6a01bcb9611d62b23d0726f08`, a 101-language model that
includes Chinese. `reindex --semantic` is the only command permitted to call Hugging Face with network
access. It announces the pinned artifact URL, approximately 512 MB download, and resolved cache directory
first; `HF_HUB_CACHE`, `HF_HOME`, and `XDG_CACHE_HOME` overrides are honored. Server-side search opens the
cache with `local_files_only=True` and returns `not_available` rather than downloading.
The extra uses `model2vec>=0.8.2,<0.9` and `sqlite-vec>=0.1.9,<0.2`; neither is a base dependency.

See [docs/data-model.md](data-model.md) for the full schema reference these tools operate on.
