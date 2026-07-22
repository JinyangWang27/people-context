# CLI

`people-context` is the human-operated companion to the MCP server. It uses the same application use cases for
curation, so validation, audit, HLC, and changelog capture match MCP writes.

## Global option

`--db PATH` explicitly selects the SQLite database and overrides every other location source.

## Commands

| Command | Purpose |
|---|---|
| `db-path [-v]` | Print the resolved DB path; verbose mode prints the complete resolution trace. |
| `init` | Safely seed or add to the self identity, optionally review a vCard import, and set a philosophy. |
| `demo [--reset]` | Seed the isolated packaged fictional demo; refuse replacement unless `--reset` is supplied. |
| `list [--all] [--limit N]` | List people; `--all` includes soft-deleted rows. |
| `search QUERY [--limit N]` | Ranked lexical person search. |
| `show PERSON` | Resolve an id/name and print identity plus context; relationships use perspective `display_type`. |
| `export [--output FILE]` | Full portable JSON envelope, unchanged by M7. |
| `edit PERSON [--name NAME] [--summary TEXT]` | Edit canonical identity fields. |
| `add-alias PERSON VALUE [--kind KIND] [--lang LANG] [--script SCRIPT]` | Add an alias. |
| `set communication_philosophy VALUE` | Set the supported user preference. |
| `delete PERSON [--yes]` | Preview and permanently forget a person graph. |
| `sync-log [--limit N] [--entity ID] [--payloads]` | Inspect local replay entries; payloads are opt-in. |
| `reindex` | Rebuild the active-person FTS index. |
| `reindex --semantic` | Explicitly obtain the pinned model and atomically rebuild semantic vectors. |
| `relationship-types` | List vocabulary and uncategorized types currently used by active edges. |
| `relationship-types add ...` | Add portable custom vocabulary (add-only in v1). |
| `normalize-relationships [--apply]` | Dry-run or apply audited canonical rewrites of legacy edges. |
| `export-vault --output DIR [--include-sensitive]` | Generate a deterministic Obsidian relationship vault. |

`show`, `edit`, `add-alias`, and `delete` try an active id first and then `ResolvePerson`. Unknown references exit
1; ambiguous names exit 2 and print candidates rather than guessing.

## Onboarding

```bash
uv run people-context init
```

On a fresh database, `init` asks for a canonical self name and optional comma-separated email handles. On a
non-empty database it continues only when one unambiguous active self already exists and the operator confirms
additive onboarding; it keeps that person's id and canonical name. Optional vCard intake uses the existing
stage/review/commit gate and commits only the candidate ids entered at the prompt. The self identity exists before
the file is parsed, so a card matching a self handle is excluded with all its dependent candidates. The optional
one-line communication philosophy is prompted last.

## Packaged demo

```bash
uv run people-context demo --reset
```

The demo always uses the absolute `{XDG_DATA_HOME or ~/.local/share}/people-context/demo.db` path. It ignores the
global `--db` option, `PEOPLE_CONTEXT_DB`, config files, and workspace discovery, and `--reset` removes only that
database plus its explicit `-wal` and `-shm` companions. The command seeds fictional audited people, handles,
affiliations, facts, interactions, and a connected relationship graph, then prints the path-targeted server command
and concrete `resolve_person`, `get_relationship_graph`, and `find_connection` calls using the created ids.

## Relationship vocabulary

List seeded/custom rows and uncategorized stored types:

```bash
uv run people-context relationship-types
```

Add a symmetric custom type with repeatable synonyms:

```bash
uv run people-context relationship-types add co_founder_of \
  --category professional --symmetric --synonym cofounder
```

Add an inverse pair:

```bash
uv run people-context relationship-types add advises \
  --category professional --inverse advised_by
```

`--inverse` and `--symmetric` are mutually exclusive. Type, inverse, category, and synonyms are normalized to
snake case. Existing rows/synonyms are rejected because v1 vocabulary is add-only. Custom vocabulary is written
through the M6 audit/changelog seam; migration seeds are reference data and are not logged.

## Normalize legacy relationships

Migration 003 does not rewrite stored edges. Preview changes:

```bash
uv run people-context normalize-relationships
```

Apply them:

```bash
uv run people-context normalize-relationships --apply
```

Dry-run is the default and performs no writes. Apply uses the same canonical policy as `set_relationship` and
captures every update/removal atomically in audit and changelog. Only duplicates with overlapping validity
periods are merged; an edge active today is preferred, otherwise the older row is retained.

## Vault export

```bash
uv run people-context export-vault --output ~/PeopleVault
```

The destination must be nonexistent, empty, or already contain `.people-context-vault`. A non-empty unmarked
directory is refused without changes. Re-export replaces only the marker plus `People/` and `Organizations/`;
`.obsidian/` and every other user-created path are preserved. Use `--include-sensitive` only with explicit intent;
exported Markdown is outside the server's disclosure controls. See [vault-export.md](vault-export.md).

## Database location resolution

The CLI and server use the same first-match order:

1. explicit `--db`/server argument;
2. `PEOPLE_CONTEXT_DB`;
3. `db_path` in `{XDG_CONFIG_HOME or ~/.config}/people-context/config.toml`;
4. `OPENCLAW_WORKSPACE`, then `~/.openclaw/workspace`, storing `people-context/people.db`;
5. `{XDG_DATA_HOME or ~/.local/share}/people-context/people.db`.

Paths are expanded and parent directories are created only when SQLite opens the selected database.
The dedicated `demo` path is the documented exception: it is deliberately isolated from this resolution chain.

## Direct SQLite access

The file is plain SQLite and may be inspected with DB Browser, Datasette, or `sqlite3`. Prefer CLI/MCP writes:
direct SQL bypasses audit/changelog capture and can stale FTS/semantic derived indexes. Repair person FTS with
`reindex`; repair semantic vectors with `reindex --semantic`. Directly inserted legacy relationship types may be
made canonical and replayable with `normalize-relationships --apply`.

## Server transport flags

`people-context-mcp` is stdio by default. `--http --host 127.0.0.1 --port 8765` selects unauthenticated loopback
Streamable HTTP at `/mcp`; no other bind host is accepted.
