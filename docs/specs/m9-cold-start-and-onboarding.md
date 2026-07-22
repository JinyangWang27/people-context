# M9 — Cold start & onboarding

Status: Delivered. See [docs/roadmap.md](../roadmap.md#m9--cold-start--onboarding).

## Motivation

Every read-only tool this project ships is only interesting once there is data behind it: a brand-new install has
an empty `persons` table, so identity and graph reads return empty/not-found results. M8 gets people to install the
server in one line; M9 gets them from an empty database to a working demonstration and broadens the existing
extract-and-stage import pipeline to calendar attendees and a LinkedIn connections export.

`ImportContent` is already source-agnostic: an injected `ImportExtractor` returns either existing email candidate
models or strict candidate dictionaries, all staged through the same `CandidateStager`. New sources remain
adapters and reuse the existing person/interaction/affiliation/fact vocabulary and review/commit gate.

## Scope

In scope:

- `people-context init`: self-person seeding before optional vCard import, then initial communication philosophy;
- `people-context demo`: deterministic fictional data in a dedicated non-default database;
- `.ics` calendar-attendee import (`source_type="ics"`);
- LinkedIn connections CSV import (`source_type="linkedin"`);
- relocating `ImportExtractorRouter` from the vCard module into its own adapter module.

Non-goals:

- OAuth/live calendar or LinkedIn integration;
- new candidate types, import schema, or review/commit behavior;
- retaining event titles/descriptions or LinkedIn URLs/notes;
- demo writes to the user's resolved real database.

## Design

### `people-context init`

The CLI composition is deliberately ordered so self filtering is active before any contact file is parsed:

1. Prompt for canonical name and zero or more email handles. Seed self via
   `RememberPersonInput(name=..., aliases=[AliasInput(..., kind=AliasKind.HANDLE)], is_self=True, source="cli")`.
   Existing self/same-name ambiguity errors surface before import. If vCard import is selected with no handle,
   warn that email-based self exclusion is unavailable; on a fresh database, the pre-created canonical name still
   lets candidate matching target self rather than creating a second person.
2. Optionally run the existing vCard `ImportContent` → `ReviewImport` → `CommitImport` flow. A card containing a
   seeded self handle is excluded together with its dependent affiliation/fact candidates.
3. Optionally store a one-line communication philosophy through `SetCommunicationPhilosophy`.

This is CLI composition of existing use cases; no new app port is required. The command must define its behavior
on a non-empty store: refuse before mutation unless the existing state contains one unambiguous self target and the
user explicitly confirms additive onboarding. It never guesses among same-name people.

### `people-context demo`

Always use a dedicated demo path and refuse reseeding without `--reset`. Fictional runtime data is generated under
`src/people_context/` or shipped as declared package data there; production code never reads `tests/fixtures`.
Ignore `--db` and `PEOPLE_CONTEXT_DB` for the demo target.

On success print:

- absolute demo database path;
- installed-package `people-context-mcp` launch command targeting that path;
- copy-pasteable `resolve_person`, `get_relationship_graph`, and `find_connection` calls using seeded identities.

Acceptance builds/installs the wheel in a clean environment and runs `people-context demo --reset`.

### Import router relocation

Move dispatch to `adapters/import_router.py` and make accepted values explicit:

- `email` and `mbox` → `EmailImportExtractor`;
- `vcard` → `VCardImportExtractor`;
- later M9 branches add `ics` and `linkedin`;
- every other value fails with `ImportExtractionError("invalid_source_type", ...)`.

Preserve `mbox` path-only validation, output, and skip reporting exactly.

### `.ics` calendar import

Add `IcsImportExtractor` implementing the existing Protocol. Parsing unfolds lines and processes each `VEVENT`
independently. Never persist or return `SUMMARY`, `DESCRIPTION`, location, conference URL, attachment, or other
free text; interaction summary is the constant `"Calendar event"`.

#### Attendees and batch references

- Only `ATTENDEE` properties with non-empty `mailto:` addresses become external person candidates.
- Normalize/deduplicate by email across the entire file; differing `CN` values become stable alternate names.
- Stage the email as a `handle` alias.
- Exclude addresses matching `self_addresses` from person candidates and participant refs.
- Every interaction references the one shared batch-local candidate per external address.
- An event with no external attendee after filtering produces no interaction candidate and is counted with a
  non-sensitive skip reason; never stage an empty/unknown participant list.

#### `DTSTART` policy

The domain needs a deterministic timezone-aware `datetime`; calendar inputs commonly contain several RFC 5545
forms. Support only these explicit conversions:

- UTC date-time ending `Z`: parse and normalize to UTC;
- local date-time with a `TZID`: resolve through `zoneinfo.ZoneInfo`, attach that zone, and normalize to UTC;
- `VALUE=DATE` all-day value: represent the date deterministically as `00:00:00Z`, documenting that only the
  calendar day—not an event time—was present.

A floating date-time with neither `Z` nor `TZID` has no portable timezone and is skipped as
`floating_dtstart_unsupported`; an unknown `TZID`, invalid/nonexistent timestamp, missing `DTSTART`, or malformed
property is skipped with a stable reason. Do not use the host's local timezone. For ambiguous DST wall times,
fail/skip rather than silently choosing a fold unless the parser can prove a unique offset.

`DTEND`, duration, recurrence expansion, cancelled status, and recurring-instance generation are out of scope:
one source `VEVENT` yields at most one interaction candidate at its parsed start.

### LinkedIn connections CSV import

Add `LinkedInImportExtractor` for the documented Connections CSV columns:

- one person candidate per valid row, with email as handle when present;
- optional affiliation only when company and position are both non-blank;
- optional `linkedin_connected_on` fact only for a parseable date;
- never stage profile URL or free-text notes.

Tolerate a documented superset of expected columns and skip invalid rows independently. Use stable unique batch
refs; deduplicate rows by normalized email when present, accumulating alternate names, while rows without email
remain distinct to avoid merging unrelated same-name people.

## Migration needs

None.

## CLI / MCP surface changes

New CLI commands:

```text
uv run people-context init
uv run people-context demo [--reset]
```

The existing free-string `import_content` tool accepts `ics` and `linkedin`; response shape remains
`ImportBatchResult` and both extractors reuse `skipped_cards` for one-based item/row index plus stable reason.

## Security and privacy

- Calendar free text and LinkedIn URL/note fields never enter candidates, skip details, logs, or errors.
- New imports remain local/offline and retain stage → review → commit approval.
- Self is created before vCard import; own contact data cannot create a duplicate self person.
- Floating calendar times are not interpreted through the machine's local timezone.
- Demo is hard-separated from the real database.

## Testing strategy

- Init tests: own vCard by seeded handle and dependants excluded; no-handle same-name card targets self on a fresh
  store; ambiguous/non-empty behavior refuses before mutation; communication philosophy composition.
- Demo tests: isolation, reset/refusal, deterministic seed, wheel-installed runtime data, printed path/examples.
- Router matrix: `email`, `mbox`, `vcard`, `ics`, `linkedin`, and unknown; explicit `mbox` E2E regression.
- ICS tests: cross-event email dedup/alternate names, self filtering, self-only event omission, UTC start, resolvable
  TZID conversion, all-day date mapping, floating/unknown/ambiguous/malformed start skips, per-event independence,
  and raw-content sentinels absent from candidates/logs/errors.
- LinkedIn tests: header supersets, email dedup, no-email distinct rows, row independence, date validation, and raw
  URL/note exclusion.
- App fake-port and real-SQLite tests plus in-memory MCP and one stdio E2E case.
- `uv run ruff check .` and `uv run pytest -q` fully green.

## Implementation decisions

- The independently tested iCalendar and vCard parsers remain separate; no speculative shared parser was added.
- LinkedIn accepts only the canonical documented headers while allowing arbitrary extra columns.
- Immutable typed Python constants under `src/people_context/` are the packaged demo source.
