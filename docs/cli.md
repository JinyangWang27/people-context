# CLI

`pctx` is the human-operated companion to the MCP server. It uses the same application use cases for
curation, so validation, audit, HLC, and changelog capture match MCP writes.

## Global options

`--db PATH` explicitly selects the SQLite database and overrides every other location source.

`--encrypted` opens that database through SQLCipher instead of plain SQLite, reading the key only from the
`PEOPLE_CONTEXT_DB_KEY` environment variable. Without the flag nothing changes; with the flag and no non-empty
key the command exits `2` without opening or creating anything, and never falls back to plaintext. See
[privacy-and-safety.md](privacy-and-safety.md#optional-at-rest-encryption) for the platform support matrix and
what encryption does and does not protect.

## Commands

| Command | Purpose |
|---|---|
| `db-path [-v]` | Print the resolved DB path; verbose mode prints the complete resolution trace. |
| `init` | Safely seed or add to the self identity, optionally review a vCard import, and set a philosophy. |
| `demo [--reset]` | Seed the isolated packaged fictional demo; refuse replacement unless `--reset` is supplied. |
| `list [--all] [--limit N] [--json]` | List people; `--all` includes soft-deleted rows, `--json` emits the person index. |
| `search QUERY [--limit N]` | Ranked lexical person search. |
| `stale [--category C] [--threshold-days N] [--limit N]` | Report people with no recent ordinary interaction. |
| `upcoming [--window-days N] [--person PERSON]` | Report ordinary birthdays and dated reminders coming up. |
| `show PERSON` | Resolve an id/name and print identity plus context; relationships use perspective `display_type`. |
| `brief PERSON [--include-sensitive] [--json] [--output FILE]` | Compose one person's deterministic brief. |
| `export [--output FILE]` | Full portable JSON envelope, unchanged by M7. |
| `edit PERSON [--name NAME] [--summary TEXT]` | Edit canonical identity fields. |
| `add-alias PERSON VALUE [--kind KIND] [--lang LANG] [--script SCRIPT]` | Add an alias. |
| `set communication_philosophy VALUE` | Set the supported user preference. |
| `delete PERSON [--yes]` | Preview and permanently forget a person graph. |
| `sync push --output DIR` | Write one complete plaintext bootstrap bundle as an owner-only file. |
| `sync pull --input PATH [--yes]` | Restore one bootstrap bundle into a freshly initialized database only. |
| `sync-log [--limit N] [--entity ID] [--payloads]` | Inspect local replay entries; payloads are opt-in. |
| `watch [--interval S] [--from-start]` | Follow the local changelog as JSON lines until interrupted. |
| `reindex` | Rebuild the active-person FTS index. |
| `reindex --semantic` | Explicitly obtain the pinned model and atomically rebuild semantic vectors. |
| `relationship-types` | List vocabulary and uncategorized types currently used by active edges. |
| `relationship-types add ...` | Add portable custom vocabulary (add-only in v1). |
| `normalize-relationships [--apply]` | Dry-run or apply audited canonical rewrites of legacy edges. |
| `export-vault --output DIR [--include-sensitive]` | Generate a deterministic Obsidian relationship vault. |
| `export-vcard [--output FILE] [--include-sensitive] [--version V]` | Export active people as deterministic vCards. |
| `reminders-ics --output FILE` | Export active dated reminders as an owner-only iCalendar `VTODO` file. |

`show`, `brief`, `edit`, `add-alias`, and `delete` try an active id first and then `ResolvePerson`. Unknown
references exit 1; ambiguous names exit 2 and print candidates rather than guessing.

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

## Stale relationships

```bash
uv run pctx stale --category professional --threshold-days 120 --limit 25
```

Prints one row per active person you have not interacted with for at least `--threshold-days` days, plus everyone
with no ordinary interaction at all (shown as `never`). `--category` keeps only people whose relationship to you
is in that category today; the value uses the same normalization as relationship vocabulary, so `Professional`
and `professional` behave identically. `--threshold-days` accepts `0..36500` (default 90) and `--limit` accepts
`1..100` (default 20); an out-of-range value exits `2` without printing a partial report.

The report is a recency signal, not a health score: it shows ids, names, categories, the last interaction date,
the signed day count, and an interaction count, and never interaction summaries. Only `public`/`personal`
interactions are counted, so a person you only have `sensitive`/`restricted` interactions with appears as
`never` — the CLI report deliberately does not widen the disclosure level here. Your own self identity is not
reported, and a future-dated interaction is never treated as stale. The day count is measured from the last
interaction date the report prints, so the two always agree; ordering instead compares instants, so a timestamp
recorded with a different offset is placed by when it actually happened, and a naive stored timestamp is read as
UTC rather than in the host timezone. Rows are ordered never-contacted first, then oldest interaction, name, and
id; when more people qualify than `--limit`, the command says so.

## Upcoming dates

```bash
uv run pctx upcoming --window-days 60 --person "Alice"
```

Prints one row per upcoming birthday or dated reminder inside the inclusive window `[today, today + N]`.
`--window-days` accepts `0..366` (default 30); `0` reports today only, and an out-of-range value exits `2`
without printing a partial report. `--person` takes an id or a resolvable name and behaves like every other
person argument: an unknown name exits `1` and an ambiguous one exits `2` with candidates.

Birthdays come from `public`/`personal` facts whose predicate is exactly `birthday` and whose value is
`YYYY-MM-DD` or `--MM-DD`; both are treated as annual recurrences and projected to the next real occurrence.
29 February is never moved to the 28th or to 1 March, so it only appears when the window actually reaches the
next leap day. Reminders come from active reminders with a `due_at`, reported on the calendar day they were
stored with — the command never reinterprets a naive stored datetime in the host timezone, and never converts
an aware one.

A birthday row is labelled `Birthday` rather than the stored value, so the report shows when the date falls
without printing the birth year. Ordinary birthday facts whose value is neither accepted form — including
impossible dates such as `1985-02-29` — are counted in a trailing skipped line rather than guessed at.
`sensitive`/`restricted` facts are invisible here: they produce no row and are not counted, so the skipped
count cannot reveal that an elevated birthday exists.

## Person brief

```bash
uv run pctx brief "Alice Zhang"
uv run pctx brief 01J... --json --output ~/alice.json
uv run pctx brief "Alice Zhang" --include-sensitive
```

Composes one person's context, communication guidance, and reminders into a single document. It is a read path:
it records nothing, mints no audit or changelog rows, and adds no MCP tool. Reminders come from the same
`ListReminders` read the `reminders` workflow uses, which is why a brief shows `follow_up` and `occasion` rows
that person context alone never returns.

Disclosure is asymmetric and labelled rather than implied. `--include-sensitive` widens only the context-backed
records — facts, interactions, and traits — because that is the one read that accepts the flag. Communication
guidance keeps its own `public`/`personal` contract in both modes, so a brief taken with the flag still shows
guidance built from ordinary records only. Both levels are printed in the Markdown header and carried in the JSON
`disclosure` object, next to a notice saying the document is outside the server's disclosure controls entirely.

Markdown is the default and goes to stdout. `--json` emits the versioned `people-context-brief` document instead;
it is the stable machine form and is listed in
[compatibility.md](compatibility.md#machine-readable-json). The Markdown layout is deterministic but not frozen —
build integrations on the JSON.

Ordering does not depend on the order rows come back from storage: relationships, affiliations, traits, and
reminders each sort by a key ending in a record id, and facts and interactions keep the relevance ranking person
context already imposes. Nor does it depend on the machine: wherever a stored timestamp is compared as an
instant — the shared fact/interaction budget and the guidance friction-note limit both truncate an ordered list —
a naive value is read as UTC rather than in the host timezone, matching `stale`. Dated reminders precede undated
ones and are ordered by their stored timestamp spelling instead, because a naive and an aware value cannot be
compared as instants at all.

`--output` publishes through the same atomic private-file writer as `export` and `sync push`: the result is
`0600`, an existing permissive destination is replaced rather than left with its old mode, a destination symlink
is replaced instead of followed, and a failed write leaves any previous file untouched. The command refuses,
without writing anything, when `--output` names the database it is reading or one of that database's `-wal`,
`-shm`, or `-journal` sidecars.

## Person index

```bash
uv run pctx list --json
uv run pctx list --all --json
```

Prints the versioned `people-context-person-index` document instead of the table: one entry per person carrying
the stable id, canonical name, alias values, summary, `is_self`, and an explicit `deleted` flag. It exists so an
integration can address a person by id rather than by display name, and it carries no facts, interactions,
traits, or reminders at any sensitivity level.

Soft-deleted people are excluded unless `--all` is supplied, and then they are marked by the `deleted` field
rather than by a suffix on the name. `--limit` bounds how many people are read, exactly as it does for the table;
the entries that survive it are ordered by normalized name and then id, so the document does not depend on the
database's own collation. An empty store still produces a complete, parseable document.

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

## vCard export

```bash
uv run pctx export-vcard
uv run pctx export-vcard --output ~/people-context.vcf
uv run pctx export-vcard --version 3.0 --output ~/people-context.vcf
```

Writes one card per active person, in a fixed property order, with RFC-conformant escaping, 75-octet folding,
and CRLF line endings, so re-exporting unchanged data on the same date produces byte-identical output. People
are ordered by normalized canonical name and then by id. `--version` selects the dialect and accepts `3.0` or
`4.0`; `4.0` is the default.

The mapping is deliberately non-heuristic, and everything it emits reads back through the bundled vCard importer
unchanged:

- `FN` is the canonical name, and `N` repeats that whole name in the family-name component
  (`N:<canonical name>;;;;`). The store never recorded a given/family boundary, so splitting the name on
  whitespace would invent one.
- `NICKNAME` comes from nickname aliases and `EMAIL` from handle aliases that actually parse as mail addresses.
  A handle that is not an address — `@alice`, say — is not exported as one.
- One `ORG`/`TITLE` pair is emitted, chosen by normalized organization name, then normalized role, then
  affiliation id, because the importer reads only the first pair back. Affiliations are evaluated as of today,
  so an expired one is not exported at all; additional *active* ones are counted as omitted.
- One full-date `BDAY` is emitted, chosen by highest confidence, then newest `recorded_at`, then fact id.

Only complete `YYYY-MM-DD` birthdays are portable. The project's recurring `--MM-DD` values are counted as
skipped rather than written, because that spelling is not what a conforming vCard 4 partial date looks like and
has no vCard 3 form at all; a birthday value that is not a real calendar date is counted separately. The full
date is written in the extended `YYYY-MM-DD` form in both dialects rather than vCard 4's basic `YYYYMMDD`
calendar date: the importer keeps `BDAY` text verbatim, so the extended form is what makes a reimported birthday
identical to the exported one.

Sensitive and restricted birthday facts are invisible without `--include-sensitive`. They supply no `BDAY` and
contribute to no count, so the report never signals that an elevated record exists.

Stdout is the default, and the document goes there alone: the counts and the disclosure notice are printed on
stderr, so `pctx export-vcard > people.vcf` is a valid vCard file. With `--output` the file is published through
the same atomic private-file writer as `export`, `brief`, and `sync push` — the result is `0600`, an existing
permissive destination is replaced rather than left with its old mode, and a failed write leaves any previous
file untouched. As with the other file exports, the command refuses, without writing anything, when `--output`
names the database it is reading or one of that database's `-wal`, `-shm`, or `-journal` sidecars.

The file is plaintext personal data outside the server's disclosure controls; handing it to a contacts
application is your own disclosure decision.

## Reminder calendar export

```bash
uv run pctx reminders-ics --output ~/people-context-reminders.ics
```

Serializes one `VTODO` per active reminder into a single `VCALENDAR` file, written through the same atomic
private-file writer as `export` and `sync push`: the result is `0600`, an existing permissive destination is
replaced rather than left with its old mode, and a failed write leaves any previous file untouched.

The output is deterministic. `DTSTAMP` comes from the reminder's stored `created_at` rather than wall-clock
time, entries are ordered by `(due_at, id)`, and text is escaped and folded per RFC 5545, so re-exporting
unchanged data produces byte-identical output.

Reminder timestamps are not yet required to be timezone-aware, and this read path never guesses one. A reminder
is reported and omitted when it has no `due_at` (`Skipped N reminder(s) without a due date.`) or when either
`due_at` or `created_at` is naive (`Skipped N reminder(s) whose stored timestamps have no timezone.`).

Recurrence maps only for the exact stored values `yearly`, `monthly`, and `weekly`. A mapped rule is anchored by
a `DTSTART` at the stored instant and carries no `DUE`: RFC 5545 builds the recurrence set from `DTSTART`, an
unqualified `FREQ` takes its month and day-of-month from that anchor, and `DUE` would have to be strictly later
than it. Anchoring on the deadline's own calendar fields is what makes a monthly first-of-month reminder repeat
on the first rather than on the last day of the preceding month. A non-recurring reminder is unchanged and
carries `DUE` only. Any other non-empty recurrence value still exports one dated occurrence with its `RRULE`
omitted and counted.

The command refuses, without writing anything, when `--output` names the database it is reading or one of that
database's `-wal`, `-shm`, or `-journal` sidecars: publication replaces the destination's directory entry while
SQLite still holds the old file open, so such a write would destroy the store. The file it does write is
plaintext personal data outside the server's disclosure controls — keep it on encrypted storage and hand it to a
calendar application deliberately.

## Changelog watch

```bash
uv run pctx watch
uv run pctx watch --interval 0.5 --from-start
```

Follows the local replayable changelog and prints one canonical JSON object per entry, in replication order,
until you interrupt it with `Ctrl-C`. Each line is a complete changelog entry — the same fields `sync-log`
shows, plus the full replay payload — with sorted keys and no interior spacing, so the stream pipes directly
into `jq` or a line-oriented reader. Each line mirrors one stored changelog row, including the row's own
`schema_version`; the stream is not one of the frozen JSON documents in
[compatibility.md](compatibility.md#machine-readable-json).

By default the tail reports only what happens from now on: it reads the current newest entry once, adopts it as
its starting cursor, and emits no history. `--from-start` begins before the first entry instead and replays
everything already recorded before following new writes.

Polling is deterministic and bounded. `--interval` is the pause between polls, in seconds, accepted between
`0.1` and `3600`; one poll reads at most 200 entries, and a poll that fills its batch is followed immediately by
the next one, so a long replay drains at full speed and only an idle tail waits. The cursor advances only to an
entry that has actually been printed, so an empty poll changes nothing and no entry is skipped. The cursor is
the entry's complete `(hlc_physical_ms, hlc_logical, device_id, op_id)` key, which keeps the tail exact when two
devices mint the same HLC pair. Nothing is persisted between invocations: every run establishes its own cursor.

`watch` records nothing and makes no network call, but the payloads it prints carry personal data at every
sensitivity level. They go to local stdout only; a notice on stderr says so before the first entry, and
redirecting the stream elsewhere is your own disclosure decision.

## Database location resolution

The CLI and server use the same first-match order:

1. explicit `--db`/server argument;
2. `PEOPLE_CONTEXT_DB`;
3. `db_path` in `{XDG_CONFIG_HOME or ~/.config}/people-context/config.toml`;
4. `OPENCLAW_WORKSPACE`, then `~/.openclaw/workspace`, storing `people-context/people.db`;
5. `{XDG_DATA_HOME or ~/.local/share}/people-context/people.db`.

Paths are expanded and parent directories are created only when SQLite opens the selected database.
The dedicated `demo` path is the documented exception: it is deliberately isolated from this resolution chain,
and from `--encrypted` — the fictional demo database is always plain SQLite.

## Direct SQLite access

The file is plain SQLite by default (an `--encrypted` database is not readable by these tools without the
SQLCipher key) and may be inspected with DB Browser, Datasette, or `sqlite3`. Prefer CLI/MCP writes:
direct SQL bypasses audit/changelog capture and can stale FTS/semantic derived indexes. Repair person FTS with
`reindex`; repair semantic vectors with `reindex --semantic`. Directly inserted legacy relationship types may be
made canonical and replayable with `normalize-relationships --apply`.

## Server transport flags

`people-context-mcp` is stdio by default. `--http --host 127.0.0.1 --port 8765` selects unauthenticated loopback
Streamable HTTP at `/mcp`; no other bind host is accepted. `--encrypted` applies the same opt-in SQLCipher
connection as the CLI flag, refusing to start without `PEOPLE_CONTEXT_DB_KEY`.
