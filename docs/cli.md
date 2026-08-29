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
| `timeline PERSON [--limit N] [--include-sensitive] [--json]` | Print one bounded page of a person's durable history, newest first; a read-only projection, not an audit dump. |
| `show PERSON` | Resolve an id/name and print identity plus context; relationships use perspective `display_type`. |
| `brief PERSON [--include-sensitive] [--json] [--output FILE]` | Compose one person's deterministic brief. |
| `doctor [--json] [--only CODES]` | Report data-quality findings; repairs nothing and exits `0` even with findings. |
| `stats [--json] [--include-path]` | Report aggregate-only counts and storage bytes; the path is redacted by default, and a target it would have to create or migrate is refused. |
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
| `import stage SOURCE PATH [--self-sender TEXT] [--label TEXT] [--external-source-id TEXT] [--force] [--json]` | Extract one local export into a reviewable staging batch; nothing is committed. |
| `import stage-candidates --source LABEL --input PATH\|- [--source-kind KIND] [--content-digest SHA256] [--extraction-fingerprint SHA256] [--label TEXT] [--external-source-id TEXT] [--json]` | Stage strict agent-extracted candidate JSON — not source text — into a reviewable staging batch. |
| `import review BATCH_ID [--json]` | Show every staged candidate in one batch with its canonical id and status. |
| `import commit BATCH_ID --all\|--accept ID... [--json]` | Commit the explicitly accepted candidates of one batch. |
| `sources [--limit N] [--cursor CURSOR] [--json]` | List local import receipts newest-first, one bounded keyset page at a time. |
| `source show SOURCE_SESSION_ID [--limit N] [--cursor CURSOR] [--json]` | Show one receipt, its aggregate candidate counts, and one bounded page of committed candidate outcomes. |

`show`, `brief`, `timeline`, `edit`, `add-alias`, and `delete` try an active id first and then
`ResolvePerson`. Unknown references exit 1; ambiguous names exit 2 and print candidates rather than
guessing.

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

## Import

```bash
uv run pctx import stage linkedin ~/exports/Connections.csv
uv run pctx import review 01J...BATCH
uv run pctx import commit 01J...BATCH --accept 01J...CANDIDATE --accept 01J...OTHER
uv run pctx import commit 01J...BATCH --all
```

`import` exposes the same stage → review → commit lifecycle as the MCP import tools (see
[import.md](import.md)). `SOURCE` is one of `email`, `mbox`, `vcard`, `ics`, `linkedin`, `outlook`, or
`whatsapp`. `--self-sender` is passed through for sources that identify you by display label rather than by
address; sources that do not use it ignore it.

Staging never commits. `commit` requires exactly one of `--all` or a repeatable `--accept CANDIDATE_ID`, and
supplying either *is* the approval — there is no second prompt and no `--yes`. Canonical candidate ids from
`import review` are the only selection interface; accepting a dependent candidate whose person was not accepted
leaves it unresolved rather than guessing, and it can be committed later.

`import review` and `import commit` also read a batch an agent staged over MCP, so they render the full
candidate vocabulary — including the `observation`, `trait`, and `relationship` types M17 added. A person
candidate in such a batch reports its match state in words: an ambiguous identity says how many existing people
it could be, rather than looking like a new one because no id was attached.

All four commands support `--json`, which writes exactly one versioned document to stdout and keeps every
diagnostic on stderr — `people-context-import-batch`, `people-context-import-review`, and
`people-context-import-commit`, all documented in [compatibility.md](compatibility.md#machine-readable-json).
Review output and those documents carry distilled personal data from your export; inspect them before
redirecting or sharing them.

Exit statuses: `2` for an unsupported source or a malformed selection, `0` for a successful stage, review, or
commit, and non-zero for an unreadable path, an extraction failure, malformed or invalid candidate JSON, a source
with no candidates, an unknown batch, a candidate outside the batch, a previously forgotten source, or a source,
request, or batch outside the size ceilings below. Diagnostics never echo
message bodies, chat text, or other discarded source content.

### Repeat imports

```bash
uv run pctx import stage linkedin ~/exports/Connections.csv --label "Work connections"
uv run pctx import stage linkedin ~/exports/Connections.csv            # reports the existing batch
uv run pctx import stage linkedin ~/exports/Connections.csv --force    # stages it again on purpose
```

`import stage` records a durable receipt for the source it read — its machine kind, a SHA-256 of the exact bytes
parsed, a fingerprint of the extraction configuration, and the optional `--label` and `--external-source-id` you
supply — and reports the receipt's id alongside the batch. Staging the same source again does not create a second
copy of everything in it: the command reports the batch that already exists and stages nothing, and the `--json`
document says so through additive `source_session_id`, `duplicate`, and `reviewable` fields. When that batch is
already fully committed — its reviewable rows cleaned up, or restored from a bundle that carries only its durable
outcomes — the report counts the committed candidates and says there is nothing left to review, rather than
pointing at a batch `import review` can no longer find.

`--force` is the explicit way to say a repeat is intentional. It creates a separate staging batch for the same
content and never weakens the duplicate rule for later invocations. What counts as the same source is content
plus extraction configuration, so a copy of one file at another path is a duplicate, while the same chat export
staged under a different `--self-sender` is not. See [import.md](import.md#source-receipts-and-repeat-imports-m181).

If a source's records were all hard-forgotten, its receipt is reduced to a non-restageable claim and staging it
again by default refuses with `source_previously_redacted`: exit status non-zero, stdout empty even under
`--json` because there is no batch to describe, and a bounded diagnostic on stderr pointing at `--force`. Nothing
is created or changed by that refusal, and it names none of the forgotten source's former metadata.

`--label` and `--external-source-id` are opaque caller metadata for your own inspection. Any later hard forget
that touches this source clears both, because wording like `Interview with Alice` cannot safely be attributed to
one of the people a source mentioned.

This CLI boundary is bounded: `import stage` refuses a source file over 64 MiB, more than 100,000 staged
candidates, or more than 64 MiB of persisted reviewable staging payload, and it stops before staging rather than
after. `import review` and both `commit` forms measure an existing batch in SQLite against the same
100,000-row/64 MiB envelope before reading it, so a batch staged through the older uncapped MCP path is refused
safely instead of being materialized. Those ceilings are limits of this command only — the MCP
`import_content`, `review_import`, and `commit_import` tools and `pctx init` keep their released behaviour. An
export larger than a ceiling can be split and staged in parts. `import stage-candidates` carries its own, much
smaller envelope, described below.

### Agent-extracted candidates

```bash
uv run pctx import stage-candidates --source "2026-08-27 planning sync" --input candidates.json
cat candidates.json | uv run pctx import stage-candidates --source "2026-08-27 planning sync" --input -
```

`stage-candidates` is the entry point for knowledge that has no structured export behind it — a meeting
transcript, a call note, an interview, a conversation log. An agent reads that material in its own
environment, distils it into the strict candidate vocabulary, and hands over the result; `--input` takes
**candidate JSON, never the source text**, and `-` reads it from stdin. This is what lets a transcript's
relationships, observations, and inferred traits reach the graph without People Context parsing prose,
running a model, or storing a word of the source.

`--source` is a free-form label naming what the candidates were distilled from, not one of the seven
`import stage` source formats. It is stored on every staged row and every later provenance record, which is
why it is bounded as tightly as it is.

`--source-kind` optionally records an import receipt for the batch, exactly as `import stage` does for a file. It
is a machine category such as `meeting_transcript` — at most 128 characters of letters, digits, `.`, `_`, `-`, or
`/` — and never a person or a title; human wording belongs in `--label`. It is required whenever any other
receipt flag is given: a digest supplied without it is refused rather than silently ignored. If you can compute a SHA-256 over the
source artifact yourself, `--content-digest` gives that receipt a duplicate claim so a repeat of the same material
is reported rather than staged twice. Without one the workflow is still valid but makes no idempotency promise:
People Context never hashes text it was not given. `--extraction-fingerprint` is optional and should be omitted
unless you have explicit, bounded semantics for it.

This command has no `--force`: that flag belongs to `import stage`, which owns the file it reads. Here the claim
exists only because you supplied `--content-digest`, so omitting that digest is how you say a repeat is
intentional — and that, rather than a flag this command does not accept, is what its duplicate report and its
`source_previously_redacted` refusal point you at.

The command stages only. `import review` and `import commit` are the same gate they are for a file import.

Person matching here is **always** the ambiguity-preserving kind described above, whichever candidate types a
batch happens to use. Over MCP that matcher is selected by the presence of an `observation`, `trait`, or
`relationship`, so that a batch predating M17 keeps the matching it shipped with. This command has no released
history to preserve, and everything reaching it is agent-extracted regardless of vocabulary: a person plus a
fact, distilled from a transcript, can name someone two existing people could equally be, and resolving that to
one of them would attach the fact to a guess.

This surface is bounded from its first release, and unconditionally: at most **1 MiB** of candidate JSON is
read at all, spent on the read itself rather than on a parsed result, and the request may then carry at most
**500 candidates**, a **128-character** `--source`, and **8 KiB** for any single string. The equivalent MCP
caps apply only to a request that opts into an `observation`, `trait`, or `relationship` candidate, because
that contract shipped before them and narrowing it would break working imports. This command has no such
history: a path or a pipe typed at a terminal is a much weaker promise about size than an array an in-process
caller already built. A refusal names the limit and never any part of the rejected payload.

A batch that fails validation reports the candidate index and field that failed — the locations an agent needs
to correct its own JSON — reconstructed from the schema rather than forwarded. A validation error is not
automatically safe to print: a rejected extra field carries its own untrusted key in the error's location, and
an error raised by the staging rules carries the offending person reference in its message. So a location part
is shown only when the candidate models declare it and is otherwise `(redacted)`, and a message is shown only
when Pydantic derived it from the schema, degrading otherwise to the error's fixed type slug. An unsupported
candidate `type` is reported as `union_tag_invalid` for exactly this reason: the message Pydantic writes for it
quotes the rejected discriminator back. The refusal line itself is always payload-independent.

Input is refused for being unparseable in the ways bytes do not catch, too. A few tens of kilobytes of nested
arrays exhaust the decoder's stack while sitting far below the 1 MiB ceiling, and a JSON escape can decode to an
unpaired surrogate — a string with no UTF-8 encoding, and so nothing that could be stored. Both end in the same
bounded refusal rather than a traceback.

## Import sources

```bash
uv run pctx sources
uv run pctx sources --limit 10 --json
uv run pctx source show 01J8... --limit 25
```

`sources` and `source show` answer *where did this come from?* over the durable receipts and candidate mappings
described in [import.md](import.md#source-inspection-m182). They are not a document browser: there is no raw
source, no file path, no extraction configuration, and no way to retrieve the material a receipt describes,
because none of it was ever stored. A non-redacted source shows its id and machine kind, its duplicate-claim
state, timestamps and status, the batch it staged, aggregate candidate counts, and one page of committed
candidate outcomes with the durable record each produced.

Both commands are pages, not dumps. `--limit` defaults to **50** and accepts **1..200**; sources are ordered
newest-first by `(created_at DESC, id DESC)` and a source's mappings by `candidate_id ASC`; and `--cursor` takes
the opaque `next_cursor` a previous page reported. The cursor is a position in the ordering, not an offset, and
the underlying query is a range seek rather than a scan, so paging cost does not grow with how far in you are and
a concurrent import cannot renumber rows underneath you.
Repeated calls traverse a source with a hundred thousand mappings without one unbounded response. The aggregate
counts are computed in SQL and describe the whole source, so they stay the same on every page.

```text
More candidates; re-run this command with --cursor eyJ... to continue.
```

The hint names the argument that changes rather than a whole command line, because `--db` and `--encrypted` are
global options that come *before* the subcommand: a printed `pctx sources --cursor ...` would silently continue
against a different database — creating and migrating the default one, or failing to open an encrypted store.

A cursor encodes the identifier of the last row a page returned, and nothing else. The store resolves that
identifier to its own sort position, so a redacted source's withheld timestamp never travels in a value you hold.
It also names the listing that issued it: a cursor from `sources`, or from another source's `source show`, is
refused rather than silently used as a boundary that would omit part of this source's provenance. Identifiers are
format-opaque and unbounded — no length or alphabet rule, because a bootstrap restore preserves them verbatim and
any ceiling would eventually refuse a cursor this surface itself issued, so a restored source keeps its
provenance traversable. A cursor this surface did not issue, one from a different listing, or
one naming a source that has since been forgotten is refused rather than guessed at (`invalid_source_cursor`,
exit 2), as is a `--limit` outside its range (`invalid_source_page_limit`, exit 2). An id that names no receipt exits 1 with
`unknown_source_session`. Under `--json` a refusal leaves stdout empty and puts a bounded diagnostic on stderr,
naming the rule rather than repeating what was supplied.

A source that a hard forget merely touched keeps its surviving mappings visible while its `--label` and
`--external-source-id` stay permanently absent. A fully forgotten claim-backed source shows only its internal id,
non-personal kind, digest/fingerprint claim state, and `redacted` status — no label, batch, timestamps, counts, or
mappings, and pagination arguments do not widen that. A fully forgotten digestless source has no row at all. A
`merged_away` outcome names no record id, because the relationship it produced was removed during a person merge.

Both commands print a short reminder to stderr whenever a result actually carries receipt metadata: your labels
are your own wording, and a digest identifies a file rather than anonymizing it. It is printed under `--json`
too, because that mode discloses the same fields; stderr is not the document, so stdout still holds exactly one
JSON object. An empty listing discloses nothing and warns about nothing.

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

## Person timeline

```bash
uv run pctx timeline "Alice Zhang" --limit 25
uv run pctx timeline 01J... --json
uv run pctx timeline "Alice Zhang" --include-sensitive
```

Prints one bounded page of what happened around one person, newest first. `PERSON` takes an id or a resolvable
name and behaves like every other person argument: an unknown name exits `1` and an ambiguous one exits `2` with
candidates. `--limit` accepts `1..200` (default 50); an out-of-range value exits `2` without printing a partial
report. When more entries exist than the page carries, the command says so.

Entries project durable records that already exist — interactions the person attended, observations about them,
facts, affiliations, relationships, and traits. It is a read: the timeline stores nothing, rewrites nothing, and
is not an audit dump. `pctx sync-log` remains the lower-level operational history.

Each row leads with the durable record's own id — the id a later `correct_record`, `forget`, or MCP read takes,
because the timeline is a projection and mints no ids of its own — then the instant the entry is placed at, the
stored field that instant came from, the record type, the record's own display text, its disclosure level, and
the import receipt it came from when one exists. The `BASIS` column is the point of the report's honesty:

- `occurred_at`, `observed_at`, and `updated_at` are the times an interaction happened, an observation was made,
  and a trait was last written;
- `valid_from` is the date a fact, affiliation, or relationship began to hold. A date has no time of day, so it
  is placed at `00:00:00Z` — the same convention the calendar importer uses for an all-day value — and the row
  still carries the date itself in `--json`;
- `recorded_at` and `created_at` mean the record asserts no start date, so it is placed at the time it was
  written down. Nothing is dropped for lacking a date and no timestamp is invented for it.

Rows are ordered newest first, then by record type and id, so two records at the same instant always print in
the same order. Ordering compares instants at the stored precision: a timestamp stored at another UTC offset is
placed by when it actually happened, a naive stored timestamp is read as UTC rather than in the machine's
timezone, and two records in the same second are separated by their microseconds.

A relationship is shown from this person's side — a stored `parent_of` reads as `child_of` on the other
person's timeline — and an edge to a deleted person is omitted. Affiliations and relationships carry no stored
disclosure level, so their `SENSITIVITY` column shows `-` rather than a level this report invented for them.

`--include-sensitive` widens the report to `sensitive` and `restricted` records, which the MCP timeline never
discloses. It prints a warning to stderr; the default stays ordinary. `--json` prints the versioned
`people-context-person-timeline` document — the whole of stdout, so a redirected run and the printed document
are byte-identical — with every value in full rather than the truncated display text. A trait entry additionally
carries the durable evidence records it rests on, filtered by *that evidence's* own level, so an ordinary run
never names a restricted observation beside a visible trait.

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

## Data-quality findings

```bash
uv run pctx doctor
uv run pctx doctor --only duplicate_handle,duplicate_alias
uv run pctx doctor --json > findings.json
```

Reports stored data-quality problems and repairs nothing. Findings are a report, not a failure: the command
exits `0` whether or not it found anything, and non-zero is reserved for an error such as an unknown code, which
exits `2` before printing a partial report.

| Code | What it means |
|---|---|
| `duplicate_handle` | Two active people share one normalized handle alias. |
| `duplicate_alias` | Two active people share other normalized name material — a canonical name or a non-handle alias. |
| `contradictory_fact` | One person holds two facts with the same predicate, different values, and overlapping validity periods. |
| `dangling_reference` | Relationships, affiliations, or interactions still point at a soft-deleted person. |

Handle collisions take precedence: a pair reported as `duplicate_handle` is not reported again as
`duplicate_alias`, and that suppression applies even when `--only duplicate_alias` hides the handle finding
itself. A third person who shares only the name is still reported. Values are compared using the same
normalization the identity index is built on, so a collision the doctor reports is one resolution would also see;
fact values are compared exactly as stored, with no case or whitespace folding invented here. Overlap uses the
domain `ValidityPeriod` semantics, so periods that merely touch on one day do overlap and a missing bound is
unbounded on that side.

Every finding carries stable ids and a **structured** suggested action — an argv list, or an MCP tool name with
an id-only argument mapping — never an interpolated shell string and never a display name. The human report
renders a copyable form of each action; `--json` preserves the structure. Nothing is executed for you, and
`pctx doctor` itself writes no rows, audit entries, or changelog entries. A `dangling_reference` suggests the
operator-gated `forget` tool rather than a command, because `pctx show` and `pctx delete` resolve active people
only.

An MCP action carries a `requires` list naming arguments the report cannot fill in, rendered as
`(you supply: ...)` in the human report. It is empty for `merge_people` and `forget`, whose mappings are complete
as written. It is `["fields"]` for `correct_record`, because that tool refuses an empty `fields` payload and
deciding which of two contradictory values should survive is adjudication — the doctor points at the record to
correct and leaves the choice to you.

`--only CODE[,CODE...]` filters to the listed codes after validating them. `--json` prints the versioned
`people-context-doctor` document as the whole of stdout and sends the disclosure notice to stderr, so a
redirected report stays byte-identical to the document. Re-running over unchanged data produces identical
findings.

The report deliberately juxtaposes stored personal values, including `sensitive` and `restricted` fact values,
because a contradiction you cannot see is one you cannot judge. It carries no interaction summaries,
relationship labels, or affiliation roles. Like every other file this CLI writes, the output is outside the
server's disclosure controls, and the disclosure notice is printed *before* the findings — a warning that
arrives after the values are already on screen cannot inform the decision it exists to inform. A clean report
prints no notice, because it exposes nothing.

## Aggregate inventory

```bash
uv run pctx stats
uv run pctx stats --include-path
uv run pctx stats --json > inventory.json
```

Reports how much is in this database without reporting what is in it. Every figure is a count, a byte total, or
a bucket name that is either schema vocabulary — an alias kind, a sensitivity level, a seeded relationship
category, a known audit operation — or a documented sentinel. No canonical name, fact value, observation,
interaction summary, or device display name crosses the read port, so there is nothing in the report to redact
after the fact.

Three bucket kinds are not vocabulary this project chooses, and none is reported verbatim. A relationship
category you invented with `relationship-types add --category` is free text, so its relationships are counted
under `custom` rather than under the words you typed. An audit operation restored from a bundle comes from
whichever installation wrote it, so anything outside this release's known operations is counted under `other`.
Both preserve the distribution's total without naming what the operator or the origin wrote.

Device ids are the third, and they are pseudonymized rather than collapsed, because telling devices apart is
what the per-device distribution is for. Only this installation's own device id is reported as itself: it was
minted here, so it is opaque and names nobody. Every imported device — `sync pull` writes them all retired, and
a bundle carries whatever its origin wrote, hostname included — keeps its own bucket under `imported-device-N`,
numbered in sorted id order so the same store reports the same pseudonyms on every run. The test is where the
id came from, not what it looks like: a well-formed identifier can still spell something its author chose.

The sections are people by lifecycle state, row counts for every documented table, the alias-kind,
fact-sensitivity, observation-sensitivity, relationship-category, audit-operation and per-device changelog
distributions, storage bytes, and the elevation gates in force. People are split into active, soft-deleted, and
self because a soft-deleted person still occupies a `persons` row, so a single table total would misstate how
many people the store knows about. Every documented table is listed even when it holds nothing: a table missing
from the list would be indistinguishable from a table with no rows. Distributions are ordered largest bucket
first and then by key, so the same data always renders in the same order, and an empty one says `(none)` rather
than vanishing. A relationship whose stored type has no vocabulary row at all is counted under `uncategorized` —
the drift `pctx normalize-relationships` exists to resolve — which stays distinct from `custom`, where the type
has a category that simply is not one this release seeds.

Every count comes from one committed snapshot, read in a single transaction, so the figures cannot contradict
each other even when the MCP server is writing to the same database while the report runs.

Storage is the main database file plus its `-wal` and `-shm` companions, reported both as components and as
their sum. WAL mode keeps recently written pages outside the main file, so the main file alone can understate
the real footprint by an arbitrary amount. A companion that has been checkpointed away contributes zero bytes.
The path is resolved before the companions are located: SQLite derives their names from the file it actually
opened, so when `--db` names a symlink they live beside the target rather than beside the link.
An in-memory database, or a path that cannot be measured, reports an explicit `storage_kind` of `memory` or
`unavailable` with `database_bytes: null`, because an unmeasurable database is not an empty one.

`Elevated MCP capabilities in this environment` reports whether `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE` and
`PEOPLE_CONTEXT_MCP_ENABLE_EXPORT` are set in the environment of *this* CLI process, using exactly the same
truthiness rule the MCP server applies. It is not a statement about a running server: a server started from a
different environment has different gates. Nothing here starts, contacts, or probes a server, and `pctx stats`
makes no network call.

The resolved database path is a real disclosure — it usually carries an account name and says where the file
lives — so it is omitted unless you pass `--include-path`. `--json` prints the versioned `people-context-stats`
document as the whole of stdout and sends the disclosure notice to stderr. The report reads only; it writes no
rows, audit entries, or changelog entries, and exits `0`.

Reading only is why `stats` is the one command that refuses a `--db` target it would have to write to before it
could measure it, exiting `1` instead. Every other command opens whatever it is given, and answering
`No people found` from a database it just created or upgraded is still a true answer. A measurement is not:
opening a store creates the file when absent, applies any pending migrations, switches the journal mode, and
registers this installation's device row — so a mistyped path was reported back as a device and a few hundred
kilobytes the report itself had just brought into existence, and a database written by an older release was
silently upgraded and then measured in its upgraded state.

Four targets are therefore refused: one with no database, one holding something that is not a readable
database, one whose schema predates this release, and one that is a valid SQLite database belonging to some
other application — `user_version` is any program's to set, so a current-looking number cannot admit a file on
its own, and opening someone else's database would rewrite its journal mode before failing on a table it never
had. The check runs over a read-only connection that creates and migrates nothing, so a refused database is
left exactly as it was — not even a parent directory is created, and a path containing `?` or `#` names the
file it means rather than being read as URI syntax.
An up-to-date store is opened exactly as every other command opens it, where migration and device registration
really are no-ops. Run `uv run pctx init` to create a database deliberately, or run any other command once
against an older one to bring it up to date, and then measure it.

Counts are not personal values, but how much you record about whom is itself revealing — inspect the output
before sharing it.

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
uv run pctx export-vcard --version 4.0 --output ~/people-context.vcf
```

Writes one card per active person, in a fixed property order, with RFC-conformant escaping, 75-octet folding,
and CRLF line endings, so re-exporting unchanged data on the same date produces byte-identical output. People
are ordered by normalized canonical name and then by id. `--version` selects the dialect and accepts `3.0` or
`4.0`; `3.0` is the default, because it is the dialect whose standard date spelling is also the one this project
stores (see the birthday note below).

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
  Birthday facts are evaluated as of today too: a fact whose validity period has closed, or has not opened yet,
  is neither exported nor counted, so a birthday corrected by closing its period cannot come back through the
  export.

Only complete calendar dates are portable. The project's recurring `--MM-DD` values are counted as skipped
rather than written, because that spelling is not what a conforming vCard 4 partial date looks like and has no
vCard 3 form at all; a birthday value that is not a real calendar date is counted separately.

Each dialect gets the date spelling its own standard defines. RFC 6350 builds a complete vCard 4.0 date from the
ISO 8601 *basic* format, so `--version 4.0` writes `BDAY:19850412`. vCard 3.0 takes its date from RFC 2425,
where the hyphens are optional, so the default writes `BDAY:1985-04-12` — the same text the store holds. That is
why 3.0 is the default: the importer keeps `BDAY` text verbatim, so only there does an exported birthday come
back byte for byte. Reimporting a 4.0 export stores the basic spelling instead, which the birthday reports do
not read as a full date.

Sensitive and restricted birthday facts are invisible without `--include-sensitive`. They supply no `BDAY` and
contribute to no count, so the report never signals that an elevated record exists.

Stdout is the default, and the document goes there alone: the counts and the disclosure notice are printed on
stderr, so `pctx export-vcard > people.vcf` is a valid vCard file. The document is written as encoded bytes
rather than through the text layer, so a redirected stream keeps the format's CRLF endings instead of having
them rewritten to the platform separator. With `--output` the file is published through
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
