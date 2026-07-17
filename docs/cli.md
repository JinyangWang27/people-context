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
| `people-context show PERSON` | Show a person's full record: identity, aliases, relationships, facts, traits, reminders. | `PERSON` may be an id or a name; it is resolved via `ResolvePerson`. If resolution is ambiguous, the command errors and lists the candidates instead of guessing. |
| `people-context export [--output FILE]` | JSON dump of all people (later: the full dataset) to stdout, or to `FILE` if given. | |

All output is plain text (tables for `list`/`search`, structured text for `show`); no third-party formatting
dependency is used.

### Planned (M3)

Edit and curation commands reuse the same app-layer use cases as the MCP write/destructive tools, so they
carry identical audit/provenance behaviour:

| Command | Purpose |
|---|---|
| `people-context edit` | Modify an existing record. |
| `people-context add-alias` | Add an alias to a person. |
| `people-context set` | Set a fact/relationship/affiliation/trait field. |
| `people-context delete` | Remove a record (soft-delete or forget, depending on scope). |
| `people-context reindex` | Rebuild the FTS5 search indexes (`person_search`, `interaction_search`) after manual, out-of-band edits to the SQLite file — see Direct database access below. |

A basic read/search command set (`db-path`, `list`, `search`, `show`, `export`) lands in **M0**; the full
curation surface above lands in **M3** alongside `merge_people`/`forget`/import (see
[docs/roadmap.md](roadmap.md)).

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

If a person, alias, or interaction row is edited directly with an external SQL tool, the FTS index for that
row can go stale until it is rebuilt. The planned `people-context reindex` command (M3, listed above) exists
specifically to rebuild both FTS5 tables from the current contents of `persons`/`aliases`/`interactions`
after this kind of manual edit; direct SQL changes do not, on their own, get an `audit_log` entry, since the
CLI/MCP layer is what writes those.

See [docs/data-model.md](data-model.md) for the full schema reference these tools operate on.
