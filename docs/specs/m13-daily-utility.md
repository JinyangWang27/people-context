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

Add `ports/insights.py::RecencyReader`, its SQLite implementation, and an app use case that applies policy and
caps. The adapter returns one row per active, non-deleted person with:

- every active relationship-to-self category, deduplicated and stably ordered;
- latest ordinary-disclosure interaction timestamp;
- ordinary-disclosure interaction count.

Only `public`/`personal` interactions participate. A person whose interactions are all `sensitive`/`restricted`
looks identical to one with none: `last_interaction_at: null`, count zero. This prevents an ordinary tool from
leaking elevated interaction timing.

Parameters: optional canonical `category`, `threshold_days=90`, and `limit` capped at 100. The result is:

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

People with no ordinary interactions sort first, followed by oldest interaction, normalized name, and id.
No summaries or other content are returned.

### `upcoming_dates` / CLI report

`ListUpcomingDates` depends on `PersonContextReader`, `ListReminders`, `PersonReader`, and an injected `Clock`.
Use the inclusive interval `[today, today + window_days]`, where `today = clock.now().date()` and `window_days`
is constrained to `0..366`.

Ordinary-sensitivity facts qualify only when `predicate == "birthday"` and `value` is either `YYYY-MM-DD` or
`--MM-DD`. Both forms are annual recurrences: project month/day to the earliest actual occurrence on or after
today, rolling into the next year when needed. February 29 is never coerced to February 28 or March 1; its next
occurrence is the next actual leap day. Active reminders with `due_at` use the literal due date and the same
inclusive window.

`PersonReader` supplies names and determines whether a person is still active. Missing or soft-deleted people are
skipped deterministically. Return ordered `{person_id, name, kind, date, label}` entries plus
`skipped_unparseable`; sensitive/restricted facts do not contribute to entries or skip counts.

### Meeting preparation

Extend the M10 usage skill: resolve each attendee, fetch bounded `get_person_context` and
`get_communication_guidance`, list open reminders for each resolved person, and compose a brief. This is prompt
content only and adds no server tool.

### `people-context reminders-ics`

Serialize one `VTODO` for each active dated reminder:

- `UID` from reminder id;
- `DUE` from timezone-aware `due_at`, normalized to UTC in canonical iCalendar form;
- `SUMMARY` from escaped reminder text;
- deterministic `DTSTAMP` from the reminder's stored `created_at`, never wall-clock time;
- stable order `(due_at, id)` and canonical CRLF/folding rules.

Only dated reminders are exported; report `skipped_undated`. Map recurrence only for exact values
`yearly`, `monthly`, and `weekly`. A reminder with any other non-empty recurrence value is still exported as one
dated occurrence, but its unsupported `RRULE` is omitted and `recurrence_omitted` increments. Do not call the
reminder itself skipped when only the recurrence rule was omitted.

Write through `adapters/filesystem/private_file.py::atomic_write_private_text`, introduced by M11.2. Do not copy
the old `os.open(..., O_TRUNC, 0o600)` pattern: overwriting an existing permissive file must still result in a
private atomic file, and a failed write must preserve the prior destination.

### `people-context watch`

Add additive `Changelog.list_entries_after(cursor, limit)` returning rows strictly after the full comparison-key
cursor `(hlc_physical_ms, hlc_logical, device_id, op_id)` in ascending order. Existing descending
`list_entries` remains unchanged.

Startup semantics are explicit:

- without `--from-start`, read the current latest entry once and set it as the initial cursor without emitting
  existing history; only later entries are printed;
- with `--from-start`, start before the minimum key and replay all existing entries;
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
  writer.
- `watch` prints personal changelog payloads to local stdout only. Piping them elsewhere is the operator's own
  disclosure decision.

## Testing strategy

- App fake-port tests for recency thresholds, caps, stable category aggregation, zero-interaction ordering, and
  sensitivity filtering.
- Date tests for both inclusive boundaries, just-outside values, both birthday formats, year rollover, leap-day
  behavior, missing/deleted people, and sensitive/unparseable non-disclosure.
- SQLite tests prove recency sensitivity filtering occurs in SQL and cursor comparison handles cross-device HLC
  ties.
- MCP tests pin shapes and read-only annotations; CLI snapshots pin human and JSON output.
- iCalendar tests cover escaping/folding, UTC conversion, all supported recurrence mappings, `skipped_undated`,
  `recurrence_omitted`, and byte-identical repeated export.
- Private-file tests pre-create a `0o644` destination, overwrite it, and assert `0o600` on POSIX; symlink and failed
  replacement cases reuse the M11 helper tests.
- Watch tests cover initial latest-cursor behavior, `--from-start`, empty polls, multi-batch cursor advancement,
  and one deterministic poll without a long-running subprocess.
- `uv run ruff check .` and `uv run pytest -q` fully green.

## Open questions

1. Should staleness defaults eventually vary by relationship category?
2. Should future elevated variants use the existing process gate?
3. Which additional recurring date predicates should be added after birthday usage is established?
4. Should a future `watch --once` mode be added for scripting convenience?
5. Should reminder interoperability later add `VEVENT`, or remain `VTODO` only?
