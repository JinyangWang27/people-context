# CLI

`pctx` is the human-operated companion to the MCP server. It uses the same application use cases for
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
| `sync push --output DIR` | Write one complete plaintext bootstrap bundle as an owner-only file. |
| `sync pull --input PATH [--yes]` | Restore one bootstrap bundle into a freshly initialized database only. |
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
uv run pctx init
```

On a fresh database, `init` asks for a canonical self name and optional comma-separated email handles. On a
non-empty database it continues only when one unambiguous active self already exists and the operator confirms
additive onboarding; it keeps that person's id and canonical name. Optional vCard intake uses the existing
stage/review/commit gate and commits only the candidate ids entered at the prompt. The self identity exists before
the file is parsed, so a card matching a self handle is excluded with all its dependent candidates. The optional
one-line communication philosophy is prompted last.

## Packaged demo

```bash
uv run pctx demo --reset
```

The demo always uses the absolute `{XDG_DATA_HOME or ~/.local/share}/people-context/demo.db` path. It ignores the
global `--db` option, `PEOPLE_CONTEXT_DB`, config files, and workspace discovery, and `--reset` removes only that
database plus its explicit `-wal` and `-shm` companions. The command seeds fictional audited people, handles,
affiliations, facts, interactions, and a connected relationship graph, then prints the path-targeted server command
and concrete `resolve_person`, `get_relationship_graph`, and `find_connection` calls using the created ids.

## Relationship vocabulary

List seeded/custom rows and uncategorized stored types:

```bash
uv run pctx relationship-types
```

Add a symmetric custom type with repeatable synonyms:

```bash
uv run pctx relationship-types add co_founder_of \
  --category professional --symmetric --synonym cofounder
```

Add an inverse pair:

```bash
uv run pctx relationship-types add advises \
  --category professional --inverse advised_by
```

`--inverse` and `--symmetric` are mutually exclusive. Type, inverse, category, and synonyms are normalized to
snake case. Existing rows/synonyms are rejected because v1 vocabulary is add-only. Custom vocabulary is written
through the M6 audit/changelog seam; migration seeds are reference data and are not logged.

## Normalize legacy relationships

Migration 003 does not rewrite stored edges. Preview changes:

```bash
uv run pctx normalize-relationships
```

Apply them:

```bash
uv run pctx normalize-relationships --apply
```

Dry-run is the default and performs no writes. Apply uses the same canonical policy as `set_relationship` and
captures every update/removal atomically in audit and changelog. Only duplicates with overlapping validity
periods are merged; an edge active today is preferred, otherwise the older row is retained.

## Bootstrap sync bundle

```bash
uv run pctx sync push --output ~/transfer
```

`push` writes `DIR/people-context-sync-bundle.json`, creating `DIR` when it does not exist, and prints the path,
per-collection counts, device and changelog counts, and the origin device with its HLC watermark. The bundle is
one point-in-time snapshot read inside a single transaction: the portable domain rows, both relationship
vocabulary tables including custom rows, every changelog entry in ascending comparison-key order, the devices
those entries reference plus the active origin device, and the current watermark. The same database and clock
always produce byte-identical canonical JSON.

The file is written through the shared atomic private-file helper: content goes to a `0600` temporary file in the
destination directory and is then moved into place, so an interrupted export never leaves a truncated bundle, an
existing readable file is replaced rather than widened, and a symlink at the destination is replaced instead of
followed.

The bundle is **plaintext** and carries high-fidelity personal data, audit history, and full replay payloads.
Keep and transport it only on encrypted storage or through an encrypted channel. Push and pull are both
human-operated CLI actions; no MCP tool writes or restores a bundle.

```bash
uv run pctx sync pull --input ~/transfer [--yes]
```

`pull` accepts the bundle file itself or a directory containing `people-context-sync-bundle.json`. It parses and
validates the **complete** document — format, version, unknown fields, malformed rows, duplicate ids, the origin
device, changelog device references, the watermark, and every internal reference — before printing a preview,
asking for confirmation, or reserving the database. An invalid bundle therefore never reaches the prompt.

After confirmation (or `--yes`), restore takes a single `BEGIN IMMEDIATE` reservation and only then verifies that
the destination is baseline-empty: exactly one active local device, no rows in any mutable domain, preference,
staging, audit, sync, or derived-search table, no optional semantic vector storage, and only the canonical seeded
relationship vocabulary. Anything else is reported as `target_not_empty` with table names and counts — never
record contents — and nothing is written. Restore never deletes, clears, or merges existing data to make room.

Inside that same transaction it reconciles vocabulary, writes every bundled device as **retired** history, inserts
the domain rows, audit rows, and changelog entries verbatim, rebuilds the search index, and advances the local
hybrid logical clock past the bundle watermark. This device keeps its own active identity, so later local writes
carry its device id and sort after everything imported. Restore is the one path that does not mint new audit or
changelog rows: it reinstates the origin's history rather than recording a fresh mutation. Any failure rolls the
whole transaction back to the freshly initialized state. An invalid bundle or non-baseline target exits 1.

Optional semantic vectors are rebuildable cache data and are not carried in a bundle; run
`uv run pctx reindex --semantic` on the new device if you use semantic search.

A bootstrapped device can bootstrap the next one. Pushing from the restored device produces a bundle carrying its
own new entries alongside everything it imported, and every device already recorded as retired history is passed
on with its original retirement instant rather than a fresh one. Each hop still adds exactly one active identity:
the destination's own. Bootstrap remains a one-way hand-off to an empty database — it is not a way to re-sync two
devices that have both been written to.

## Vault export

```bash
uv run pctx export-vault --output ~/PeopleVault
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
