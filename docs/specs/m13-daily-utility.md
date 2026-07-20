# M13 — Daily utility & proactive signals

Status: Planned. See [docs/roadmap.md](../roadmap.md#m13--daily-utility--proactive-signals).

## Motivation

M8–M12 improve reach, activation, and trust, but daily utility still requires read paths over data that already
exists: interaction timestamps, active reminders, birthday facts, relationship categories, and the HLC-ordered
changelog. M13 adds explainable recency/date reports, meeting-preparation guidance, a deterministic reminder
export, and a local changelog tail without adding recorded data or a model-callable write surface.

## Scope

In scope:

- read-only `get_stale_relationships` MCP tool and `people-context stale` CLI;
- read-only `upcoming_dates` MCP tool and CLI;
- meeting-preparation content in the M10 skill;
- CLI-only deterministic `reminders-ics` export;
- CLI-only `watch` changelog tail.

Non-goals:

- new stored data, write tools, opaque relationship-health scores, a daemon, or a network sink;
- third-party task-manager push integration;
- elevated variants of the two MCP tools.

## Design

### `get_stale_relationships` / `people-context stale`

Add `ports/insights.py::RecencyReader`, its SQLite implementation, and
`GetStaleRelationships(RecencyReader, Clock)`. The adapter returns stored aggregate signal only; the app use case
uses `clock.now()` for deterministic age/threshold policy and applies caps.

One row per active, non-deleted person contains:

- every active relationship-to-self category, deduplicated and stably ordered;
- latest ordinary-disclosure interaction timestamp;
- ordinary-disclosure interaction count.

Only `public`/`personal` interactions participate. A person whose interactions are all `sensitive`/`restricted`
looks identical to one with none: `last_interaction_at: null`, count zero. This prevents an ordinary tool from
leaking elevated interaction timing.

Parameters and policy:

- optional canonical `category` filter;
- `threshold_days` validated in `0..36500`, default 90;
- `limit` validated in `1..100`, default documented by the tool;
- include a person when ordinary `last_interaction_at` is null or signed calendar `days_since >= threshold_days`;
- compute signed `days_since = (clock.now().date() - last_interaction_at.date()).days`; future timestamps are not
  stale and are not silently clamped;
- sort null interaction first, then oldest interaction, normalized name, and id;
- apply limit after filtering/sorting and set `truncated` when additional qualifying rows exist.

The result is:

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

No summaries or other content are returned.

### `upcoming_dates` / CLI report

`ListUpcomingDates` depends on `PersonContextReader`, `ListReminders`, `PersonReader`, and an injected `Clock`.
Use the inclusive interval `[today, today + window_days]`, where `today = clock.now().date()` and `window_days`
is constrained to `0..366`.

Ordinary-sensitivity facts qualify only when `predicate == "birthday"` and `value` is either `YYYY-MM-DD` or
`--MM-DD`. Both forms are annual recurrences: project month/day to the earliest actual occurrence on or after
today, rolling into the next year when needed. February 29 is never coerced to February 28 or March 1; its next
occurrence is the next actual leap day. Active reminders with `due_at` use the stored calendar-date component and
the same inclusive window; this report does not reinterpret a naïve datetime as a timezone.

`PersonReader` supplies names and determines whether a person is still active. Missing or soft-deleted people are
skipped deterministically. Return ordered `{person_id, name, kind, date, label}` entries plus
`skipped_unparseable`; sensitive/restricted facts do not contribute to entries or skip counts.

### Meeting preparation

Extend the M10 usage skill: resolve each attendee, fetch bounded `get_person_context` and
`get_communication_guidance`, list open reminders for each resolved person, and compose a brief. This is prompt
content only and adds no server tool.

### `people-context reminders-ics`

Serialize one `VTODO` for each active reminder whose `due_at` and `created_at` are both timezone-aware:

- `UID` from reminder id;
- `DUE` from `due_at`, normalized to UTC in canonical iCalendar form;
- `SUMMARY` from escaped reminder text;
- deterministic `DTSTAMP` from `created_at`, normalized to UTC, never wall-clock time;
- stable order `(due_at, id)` and canonical CRLF/folding rules.

The current `SetReminderInput`/`Reminder` models accept ordinary Python `datetime` values without enforcing a
timezone, and M13 is a read/export milestone rather than a breaking write-contract change. Therefore do not guess
a local timezone for legacy/current naïve rows. Count and omit:

- `skipped_undated` when `due_at is None`;
- `skipped_naive_datetime` when either `due_at` or `created_at` lacks `tzinfo` or a UTC offset.

Map recurrence only for exact values `yearly`, `monthly`, and `weekly`. A valid exported reminder with any other
non-empty recurrence value is still emitted as one dated occurrence, but its unsupported `RRULE` is omitted and
`recurrence_omitted` increments. Do not call the reminder itself skipped when only the recurrence rule was omitted.

Write through `adapters/filesystem/private_file.py::atomic_write_private_text`, introduced by M11.2. Do not copy
the old `os.open(..., O_TRUNC, 0o600)` pattern: overwriting an existing permissive file must still result in a
private atomic file, and a failed write must preserve the prior destination.

### `people-context watch`

Add additive `Changelog.list_entries_after(cursor, limit)` returning rows strictly after the full comparison-key
cursor `(hlc_physical_ms, hlc_logical, device_id, op_id)` in ascending order. Existing descending
`list_entries` remains unchanged.

Startup/polling semantics are explicit:

- `--interval` is validated in `0.1..3600` seconds;
- use a fixed documented batch size in `1..1000` so one poll is bounded;
- without `--from-start`, read the current latest entry once and set it as the initial cursor without emitting
  existing history; only later entries are printed;
- with `--from-start`, start before the minimum key and replay all existing entries batch by batch;
- after each emitted batch, advance the cursor to the final emitted entry;
- an empty poll does not alter the cursor.

The command emits one canonical JSON object per line, persists no cursor, and makes no network call. Keep polling
mechanics testable by placing one-poll behavior in a small function/generator and injecting or monkeypatching the
sleep/stop seam; tests must not rely on killing a hanging subprocess.

## Migration needs

Probably none. If `EXPLAIN QUERY PLAN` proves an additive index is needed, use the next free migration number at
implementation time rather than hardcoding `005`.

## CLI / MCP surface changes

Both new MCP tools are registered `readOnlyHint=true`:

| Tool | Main parameters | Result |
|---|---|---|
| `get_stale_relationships` | `category?`, `threshold_days=90`, `limit` | Recency rows + `truncated`. |
| `upcoming_dates` | `window_days=30`, `person_id?` | Ordered entries + `skipped_unparseable`. |

```text
uv run people-context stale [--category C] [--threshold-days N] [--limit N]
uv run people-context upcoming [--window-days N] [--person PERSON]
uv run people-context reminders-ics --output FILE
uv run people-context watch [--interval SECONDS] [--from-start]
```

## Security and privacy

- Recency uses only ordinary-disclosure interactions; upcoming dates entirely hides elevated date facts.
- Results disclose names and recency/date metadata only.
- `reminders-ics` is a human-operated file export outside server controls and uses the shared private atomic
  writer; it never invents a timezone for naïve stored timestamps.
- `watch` prints personal changelog payloads to local stdout only. Piping them elsewhere is the operator's own
  disclosure decision.

## Testing strategy

- Fake-reader/clock tests pin threshold boundaries, signed future timestamps, caps/truncation, stable category
  aggregation, null-interaction ordering, and sensitivity filtering.
- Date tests cover both inclusive boundaries, just-outside values, both birthday formats, year rollover, leap-day
  behavior, missing/deleted people, and sensitive/unparseable non-disclosure.
- SQLite tests prove recency sensitivity filtering occurs in SQL and cursor comparison handles cross-device HLC
  ties.
- MCP tests pin shapes, numeric validation, and read-only annotations; CLI snapshots pin human and JSON output.
- iCalendar tests cover escaping/folding, UTC conversion, all supported recurrence mappings, `skipped_undated`,
  `skipped_naive_datetime`, `recurrence_omitted`, and byte-identical repeated export. Include aware non-UTC offsets,
  naïve `due_at`, and naïve `created_at`; no output event may be produced for the naïve cases.
- Private-file tests pre-create a `0o644` destination, overwrite it, and assert `0o600` on POSIX; symlink and failed
  replacement cases reuse the M11 helper tests.
- Watch tests cover interval validation, bounded batches, initial latest-cursor behavior, `--from-start`, empty
  polls, multi-batch cursor advancement, and one deterministic poll without a long-running subprocess.
- `uv run ruff check .` and `uv run pytest -q` fully green.

## Open questions

1. Should a future write-contract hardening PR require timezone-aware reminder datetimes at creation time?
2. Should staleness defaults eventually vary by relationship category?
3. Should future elevated variants use the existing process gate?
4. Which additional recurring date predicates should be added after birthday usage is established?
5. Should a future `watch --once` mode be added for scripting convenience?
6. Should reminder interoperability later add `VEVENT`, or remain `VTODO` only?
