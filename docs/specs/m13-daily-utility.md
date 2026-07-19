# M13 — Daily utility & proactive signals

Status: Planned. See [docs/roadmap.md](../roadmap.md#m13--daily-utility--proactive-signals).

## Motivation

M8–M12 fix reach, activation, and trust; none of them gives a user a reason to consult the store *today*. The
data for daily utility already exists and only lacks read paths: every `Interaction` carries `occurred_at`
(`domain/interaction.py`), every `Reminder` carries `due_at` and a `status` lifecycle
(`domain/reminder.py`), birthday facts are already staged by the vCard importer as `predicate="birthday"`
rows (`Fact` in `domain/fact.py` with `predicate`/`value`/`period`), relationship rows carry a canonical
`category` through the M7 vocabulary, and the M6 changelog is a complete, HLC-ordered event stream that today
has exactly one consumer (`sync-log`). Recency reports ("who am I losing touch with"), date awareness
("whose birthday is coming"), meeting preparation, calendar-visible reminders, and a local automation feed
are all queries — no new recorded data, no new write surface.

These are also the features that personal-CRM users screenshot and share, which makes this milestone an
adoption feature disguised as a query pack.

## Scope

In scope:

- read-only `get_stale_relationships` MCP tool + `people-context stale` CLI report;
- read-only `upcoming_dates` MCP tool + CLI report;
- a meeting-preparation section in the M10 skill (plugin content only);
- `people-context reminders-ics --output FILE` (CLI-only iCalendar export);
- `people-context watch` (CLI-only JSON-lines changelog tail).

Non-goals:

- any new recorded data, table, or write tool — this milestone is read-only over existing rows;
- a notification daemon or any background process — `watch` runs only while the user runs it, and the
  reminders export is pull-based (the daemon remains a post-roadmap candidate);
- push integrations with third-party task managers (Todoist etc.) — the `.ics` feed is the integration point;
- scoring or "relationship health" heuristics beyond simple recency — no opaque scores, only observable facts
  (last interaction date, counts), keeping outputs explainable like `resolve_person`'s staged explanations.

## Design

### `get_stale_relationships` / `people-context stale`

New narrow read port `ports/insights.py::RecencyReader` (one method returning per-person latest-interaction
rows), implemented in `adapters/sqlite/recency_reader.py` with one SQL query joining active, non-deleted
persons against their latest `interactions.occurred_at` (via `interaction_participants`) and their
relationship-to-self category where one exists. App use case `app/get_stale_relationships.py` applies
thresholds and caps; SQL stays in the adapter, policy in the app, per the dependency rule.

Two contract rules are load-bearing:

**Recency respects the ordinary sensitivity boundary.** `Interaction` rows carry `sensitivity`
(`domain/interaction.py`), and the ordinary `get_person_context` discloses only `public`/`personal` records
(`_can_disclose` in `app/get_person_context.py`); the elevated variant is absent without
`PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE`. A recency date computed over *all* interactions would leak the
existence and timing of sensitive/restricted interactions through an ordinary tool. The recency query
therefore counts and dates only `public`/`personal` interactions; a person whose only interactions are
elevated reports `last_interaction_at: null` exactly like a person with none. An operator-elevated variant
behind the existing process gate is deliberately deferred (see Open Questions) rather than shipped by
default.

**Category is a list, not a scalar.** Storage permits multiple active relationship rows between the same two
people — uniqueness is scoped to `(subject, object, type)` in the relationship store — so one person can be
simultaneously `friend_of` (social) and `colleague_of` (professional). A scalar `category` would force a join
that duplicates people and corrupts `limit`/`truncated`. The contract is one row per person with
`categories: [...]`, and the `category` filter matches any element.

Parameters: optional `category` (canonical vocabulary category, matches any of a person's categories),
optional `threshold_days` (default 90), `limit` capped at 100 with a `truncated` flag — the same
explicit-caps convention as the M7 graph tools.

Response contract (stable, additive-only per the M12 promise):

```json
{
  "people": [{
    "person_id": "A",
    "name": "Alice",
    "categories": ["professional", "social"],
    "last_interaction_at": "2026-03-01T00:00:00Z",
    "days_since": 140,
    "interaction_count": 12
  }],
  "truncated": false
}
```

People with zero ordinary-disclosure interactions appear with `last_interaction_at: null` and sort first.
Names, categories, and recency metadata only — no summaries, facts, or interaction content; disclosure stays
minimal like graph nodes.

### `upcoming_dates` / CLI report

App use case `app/list_upcoming_dates.py` over two existing reads: facts via the `ContextReader` port's
`list_facts`, and reminders via the existing `ListReminders` use case (`app/list_reminders.py`). A fact
qualifies only when its `sensitivity` passes the same ordinary `public`/`personal` boundary
`get_person_context` applies — the fact's stored `value` *is* the date, so "only the date is disclosed" is
not a mitigation, and a sensitive or restricted birthday fact must be entirely invisible to this ordinary
tool — and when its `predicate` is date-like (initially exactly `birthday`) and its `value` parses as an ISO
date or a recurring `--MM-DD` form; unparseable values are skipped and counted, never guessed. Reminders
qualify when `status` is active and `due_at` falls inside the window. Parameters: `window_days` (default 30,
capped at 366), optional `person_id`. Response lists `{person_id, name, kind: "birthday"|"reminder", date,
label}` entries ordered by date, with a `skipped_unparseable` count.

### Meeting preparation (skill content, no server change)

The M10 skill gains a section instructing the agent, when the user asks to prepare for a meeting or a
calendar import (M9 `.ics`) is in play: resolve each attendee (`resolve_person`), fetch bounded context
(`get_person_context`) and `get_communication_guidance`, list open reminders per attendee, and compose a
brief. Purely prompt content in the plugin; zero new tools, matching M10's zero-server-code stance.

### `people-context reminders-ics`

CLI-only subcommand serializing reminders (via the existing `RecordStore.list_reminders` filters) into one
deterministic iCalendar file: one `VTODO` per exported reminder (`DUE` from `due_at`, `SUMMARY` from the
reminder text, `UID` from the reminder's ULID id), sorted by `(due_at, id)`, written with the `0o600`
owner-only pattern `_cmd_export` already uses. `Reminder.due_at` is optional and active listings deliberately
include undated communication notes, so the export contract must say what happens to them: **only dated
reminders are exported**, and the command reports a `skipped_undated` count (an undated `VTODO` renders as
noise or not at all in most calendar clients; undated notes already surface through
`get_communication_guidance`). `Reminder.recurrence` (free-text today) maps to `RRULE` only for an explicit
supported vocabulary (`yearly` → `FREQ=YEARLY`, `monthly` → `FREQ=MONTHLY`, `weekly` → `FREQ=WEEKLY`);
reminders with any other non-empty recurrence value are exported as single dated occurrences and counted in
`skipped_unmapped_recurrence`, never guessed into a rule. Determinism mirrors vault export: identical data
yields byte-identical output (fixed `DTSTAMP` derived from each reminder's own timestamps, never wall-clock
time).

### `people-context watch`

CLI-only polling tail over the changelog. The changelog's deterministic ordering key already exists:
`ChangelogEntry.comparison_key()` returns `(hlc_physical_ms, hlc_logical, device_id, op_id)`
(`ports/changelog.py`). Add one additive port method `Changelog.list_entries_after(cursor, limit)` returning
entries strictly after a cursor tuple in ascending key order (the existing `list_entries` orders descending
for `sync-log` and is unchanged). The command polls at `--interval` seconds (default 2), emits one JSON line
per entry, and persists no state — the cursor lives in process memory, and `--from-start` replays from the
beginning. Output goes to stdout only; the command makes no network calls.

## Migration needs

Probably none. If `EXPLAIN QUERY PLAN` shows table scans for the recency query or the ascending changelog
cursor, an additive index migration (`005_...`) may be added — forward-only and additive, consistent with the
M12 compatibility promise. No new tables or columns.

## CLI / MCP surface changes

New MCP tools (both `readOnlyHint=true`, registered through `ToolDeps` in `build_server()`):

| Tool | Main parameters | Result |
|---|---|---|
| `get_stale_relationships` | `category?`, `threshold_days=90`, `limit` | Recency rows + `truncated`. |
| `upcoming_dates` | `window_days=30`, `person_id?` | Ordered date entries + `skipped_unparseable`. |

New CLI commands:

```text
uv run people-context stale [--category C] [--threshold-days N] [--limit N]
uv run people-context upcoming [--window-days N]
uv run people-context reminders-ics --output FILE
uv run people-context watch [--interval SECONDS] [--from-start]
```

## Security / privacy considerations

- Both new MCP tools are ordinary-surface tools and therefore apply the ordinary sensitivity boundary
  themselves, not just structural minimalism: recency is computed exclusively over `public`/`personal`
  interactions, and date facts with `sensitive`/`restricted` sensitivity are entirely invisible — their
  existence, count, and timing must not be inferable from either tool's output. This matches the
  `_can_disclose` rule the ordinary `get_person_context` already enforces; elevated variants, if ever added,
  go behind the existing `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE` process gate, never a tool argument.
- Beyond the sensitivity gate, both tools disclose only names plus recency/date metadata — no summaries,
  facts' free-text values, traits, or interaction content — following the minimal-disclosure posture of the
  M7 graph tools.
- `reminders-ics` writes a file outside server disclosure controls — same caveat and owner-only permissions as
  JSON export and vault export; CLI-only, matching the rule that file-writing operations are never
  model-callable.
- `watch` emits changelog payloads, which are intentionally lossy by design
  ([docs/design/sync.md §2.1](../design/sync.md#21-payloads-are-intentionally-lossy)) but still personal data:
  output is local stdout only, and the command must never add a network sink. The docs must state that piping
  `watch` into third-party automation is the user's own disclosure decision.

## Testing strategy

- App layer: fake-port tests for staleness thresholds/caps/zero-interaction ordering and for date parsing
  (ISO, `--MM-DD`, unparseable-skip counting) against `tests/app/fakes.py`-style fakes.
- Sensitivity boundary (both tools): a person whose *latest* interaction is `restricted` reports the latest
  `public`/`personal` date instead (and `null` when none exists), with the restricted row affecting neither
  date nor count; a `restricted` birthday fact never appears in `upcoming_dates` output nor in
  `skipped_unparseable`. A person with multiple relationship types to self returns one row with all
  categories and is counted once against `limit`.
- Adapter layer: `tests/adapters/test_sqlite_recency_reader.py` (soft-deleted people excluded; participants
  via `interaction_participants` resolve to the correct latest date; sensitivity filtering happens in SQL,
  not post-hoc) and ascending-cursor coverage for `list_entries_after` in
  `tests/adapters/test_sqlite_changelog.py`, including cross-device HLC ties.
- MCP layer: in-memory server tests for both new tools' contracts and annotations, extending the annotation
  assertions in `tests/adapters/test_mcp_server.py`.
- CLI layer: `stale`/`upcoming` snapshot tests; `reminders-ics` byte-determinism (two runs, identical
  bytes), `0o600` permission check, `skipped_undated`/`skipped_unmapped_recurrence` counting, and the
  supported recurrence-vocabulary `RRULE` mapping; `watch` emits exactly the entries written after its cursor
  in one poll cycle.
- E2E: one stdio case recording interactions/reminders, then asserting `stale` and `upcoming` CLI output
  against the same data through MCP context reads.

## Open questions

1. Should staleness default thresholds vary by relationship category (family vs. professional feel different
   at 90 days), and if so, is that a config-file setting or per-call parameters only?
2. Should operator-elevated variants of `get_stale_relationships`/`upcoming_dates` (recency over *all*
   interactions, elevated date facts included) ship behind `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE`, or is the
   ordinary surface enough until someone asks?
3. Should `upcoming_dates` recognize additional date-like predicates beyond `birthday` (e.g. `anniversary`)
   from day one, or start with exactly one predicate and widen after real usage?
4. Should `watch` offer `--follow=false` (one batch then exit) as scripting sugar, or is composing with
   standard shell tools enough?
5. Is a `VTODO`-based feed the right iCalendar mapping for reminders, or do more consumers (Google/Apple
   Calendar) render `VEVENT` more reliably?
