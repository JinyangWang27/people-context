# M9 — Cold start & onboarding

Status: Planned. See [docs/roadmap.md](../roadmap.md#m9--cold-start--onboarding).

## Motivation

Every read-only tool this project ships is only interesting once there is data behind it: a brand-new install
has an empty `persons` table, so `resolve_person`, `get_relationship_graph`, and `find_connection` all return
empty/not-found results on the very first call a curious user or agent makes. M8 gets people to install the
server in one line; this milestone gets them from an empty database to a working demonstration in the same
sitting, and broadens the existing extract-and-stage import pipeline
([docs/import.md](../import.md)) to the two contact sources most people can actually export today beyond email
and vCard: calendar attendee lists and a LinkedIn connections export.

The code-level opportunity here is unusually favorable. `ImportContent`
(`src/people_context/app/import_content.py:307`) is already source-agnostic: it calls an injected
`ImportExtractor.extract(source_type, ...)` (`src/people_context/ports/imports.py:55`) and stages whatever
`ExtractedImport.candidates` comes back through the same `CandidateStager`
(`src/people_context/app/import_content.py:157`) used by every other source. The vCard extractor
(`src/people_context/adapters/vcard_import.py:55`) already demonstrates the pattern this milestone reuses
end-to-end: it never populates `ExtractedImport.people`/`.interactions`; it builds `candidates` dicts directly
in the same `person`/`interaction`/`affiliation`/`fact` vocabulary that `CandidateStager._validate`
(`src/people_context/app/import_content.py:187`) already accepts. Adding a source is therefore "one extractor
class plus a router branch," exactly as already documented in
[docs/import.md](../import.md#importers-are-adapters) and
[docs/architecture.md](../architecture.md#how-new-transports-and-importers-slot-in).

## Scope

In scope:

- `people-context init`: interactive CLI onboarding (optional vCard import, self-person seeding, initial
  communication philosophy);
- `people-context demo`: a sample dataset seeded into a dedicated, non-default database;
- `.ics` calendar-attendee import (`source_type="ics"`);
- LinkedIn connections-export import (`source_type="linkedin"`);
- relocating `ImportExtractorRouter` out of `adapters/vcard_import.py` into its own module before a third
  source type lands there, so source dispatch no longer lives inside one source's module.

Non-goals:

- OAuth/API-based calendar or LinkedIn integration — both new sources stay file-based, matching the existing
  "file-based in v1, no live connection" rule for email
  ([docs/import.md](../import.md#email-and-mbox));
- any new candidate type, `import_staging` schema change, or change to `review_import`/`commit_import` — both
  new sources reuse the four existing candidate types unchanged;
- retaining event titles, calendar descriptions, or LinkedIn message/note fields — see Security below;
- `people-context demo` writing into the user's real, resolved database under any circumstance.

## Design

### `people-context init`

A new CLI subcommand in `cli.py`, wired the same way every other subcommand is: parsed in `build_parser()`,
dispatched in `main()`, backed by `_open_context()`'s existing `CliContext` composition
(`src/people_context/cli.py:90`). Interactive steps:

1. Optionally prompt for a vCard export path (Google Takeout, Apple/macOS Contacts "Export vCard") and run it
   through the existing `ImportContent` → `ReviewImport` → `CommitImport` use cases, unchanged — this is the
   same three-call flow already exposed as MCP tools in
   `adapters/mcp/tools/imports.py:register`, just driven from the CLI instead of an agent.
2. Seed a self person via the existing `RememberPerson` use case
   (`src/people_context/app/record.py`, exported from `people_context.app`) with
   `RememberPersonInput(name=..., is_self=True, source="cli")` — `is_self` and the single-self invariant
   (`SelfAlreadyExistsError`) already exist and are exercised today through the `remember_person` MCP tool
   (`src/people_context/adapters/mcp/tools/people.py:120`).
3. Optionally prompt for a one-line communication philosophy and store it via the existing
   `SetCommunicationPhilosophy` use case (`src/people_context/app/set_communication_philosophy.py`), the same
   use case backing the `set communication_philosophy` CLI command
   (`cli.py:_cmd_set`) and the `set_communication_philosophy` MCP tool.

No new port or app-layer class is required; `init` is purely a new CLI composition of four existing use cases.

### `people-context demo`

The command always uses a dedicated demo database and refuses reseeding without `--reset`. Its deterministic
fictional dataset is runtime product data, so it must ship in installed artifacts: implement it procedurally under
`src/people_context/` or declare package data there. Production code must not read from `tests/fixtures`, which is
not included by the current wheel configuration. On success it prints the absolute demo database path, an
installed-package `people-context-mcp` launch command explicitly targeting that path, and copy-pasteable
`resolve_person`, `get_relationship_graph`, and `find_connection` tool-call examples using known fictional seed
identities. Acceptance builds and installs the wheel in a clean environment, runs `people-context demo --reset`,
and asserts that all of those path-targeted examples are present in stdout.

### `.ics` calendar import

New adapter `adapters/ics_import.py::IcsImportExtractor`, implementing the existing `ImportExtractor` Protocol
(`src/people_context/ports/imports.py:55`) with the same `extract(source_type, *, content, path,
self_addresses)` signature every other extractor implements. Parsing walks `BEGIN:VEVENT`/`END:VEVENT` blocks
(RFC 5545 uses the same line-folding rules as vCard's RFC 6350, so the unfolding logic already private to
`adapters/vcard_import.py` — `_unfold_lines`, `_split_cards`-equivalent block splitting, `_parse_property`,
`_decode_text`/`_decode_raw`/`_unescape_text` — is a strong candidate to extract into a small shared
line-folding helper module used by both extractors, rather than duplicated). For each event:

- `ATTENDEE` properties (`mailto:` value, optional `CN` parameter for display name) become `person` candidates
  **deduplicated by normalized email address across the entire file**, exactly as the email extractor already
  keeps one person per normalized address and accumulates differing display names into `alternate_names`
  (`adapters/email_import.py:45-83`). This is required, not optional: `CandidateStager` allocates a distinct id
  per person candidate and matches only against people already persisted before the batch — it does not
  deduplicate within a batch — so an attendee appearing in several events (especially under different `CN`
  spellings) would otherwise commit as duplicate people. Every event's interaction candidate references the one
  shared per-address candidate. Email values are staged as `handle` aliases, the same way vCard's `EMAIL`
  values are (`adapters/vcard_import.py:_card_candidates`);
- one `interaction` candidate per event with `channel="calendar"`, `date` from `DTSTART`, and participant refs
  built from the event's attendees — mirroring exactly how the email importer stages one interaction per
  message today (`app/import_content.py:352`);
- `SUMMARY` (the event title) is **not** persisted as the interaction summary — see Security below.

### LinkedIn connections-export import

New adapter `adapters/linkedin_import.py::LinkedInImportExtractor`, also implementing `ImportExtractor`. LinkedIn's
"Connections.csv" export (First Name, Last Name, URL, Email Address, Company, Position, Connected On) maps onto
the existing candidate vocabulary without any new field:

- one `person` candidate per row (name from First+Last; `EMAIL` → `handle` alias when present, same as vCard);
- one `affiliation` candidate per row when Company and Position are both present, using the existing
  `AffiliationCandidateInput` shape (`org`, `role`) — identical in spirit to vCard's `ORG`+`TITLE` handling
  (`adapters/vcard_import.py:145`);
- an optional `fact` candidate (`predicate="linkedin_connected_on"`, `value=<Connected On date>`) using the
  existing `FactCandidateInput` shape, the same pattern vCard already uses for `BDAY` → a `birthday` fact
  (`adapters/vcard_import.py:152`).

The profile `URL` column is not staged as an alias or fact; a URL is not a name-shaped value the identity
matcher (`normalize_name`) is meant to compare against, and it adds no resolvable identity signal beyond the
email address already captured.

### Router relocation

The current router delegates non-vCard sources to `EmailImportExtractor`, which already accepts both `email` and
`mbox`. There are therefore three accepted source values before M9. Move dispatch to `adapters/import_router.py`
and make it explicit: `email`/`mbox` → email extractor; `vcard` → vCard extractor; later branches add `ics` and
`linkedin`; every other value raises `ImportExtractionError("invalid_source_type", ...)`. Preserve `mbox`'s
path-only validation and existing output exactly.

## Migration needs

None. No schema change; both new sources produce rows through the existing `import_staging` table and the
existing commit path into `persons`/`affiliations`/`facts`/`interactions`.

## CLI / MCP surface changes

New CLI commands only; no MCP tool changes (`import_content(source_type="ics"|"linkedin", content|path)` reuses
the existing `import_content` MCP tool signature — `source_type` is already a free string parameter, not an
enum, in both the tool registration (`adapters/mcp/tools/imports.py:29`) and the port).

```text
uv run people-context init
uv run people-context demo [--reset]
```

`import_content` MCP tool response shape is unchanged (`ImportBatchResult`: `batch_id`, `candidate_count`,
`skipped_message_ids`, `skipped_without_id`, `skipped_cards`) — both new extractors reuse `skipped_cards` for
their own per-item skip reporting (one-based index + reason string), the same convention vCard already uses,
rather than inventing per-source skip fields.

## Security / privacy considerations

- **No raw content retention**, unchanged: `.ics` `SUMMARY`/`DESCRIPTION` and LinkedIn free-text fields (e.g. a
  connection "note," where present in some export variants) are attacker/other-party-controlled text and must
  never be persisted or returned to the model, exactly as the email importer already treats the `Subject`
  header ([docs/import.md](../import.md#never-persist-raw-content),
  [docs/privacy-and-safety.md](../privacy-and-safety.md#no-raw-emails-conversations-or-transcripts)). The
  `.ics` extractor stages a fixed neutral interaction summary (e.g. `"Calendar event"`), the same pattern as
  the email importer's fixed `"Email correspondence"` string.
- `review_import`/`commit_import` remain the mandatory approval gate for both new sources — nothing from either
  source reaches `persons`/`affiliations`/`facts`/`interactions` without an explicit accept, unchanged from
  every existing import source.
- `people-context init` and `people-context demo` are CLI-only, matching the existing rule that vault/file
  operations and now onboarding flows stay CLI-only, human-operated actions — no MCP tool triggers a full
  contacts import or seeds a self person implicitly.
- `people-context demo`'s hard separation from the resolved real database is a safety property, not merely a
  convenience: an agent or script that runs `demo` must not be able to corrupt or intermix fictional data with
  a user's real dataset, so the command intentionally ignores `--db`/`PEOPLE_CONTEXT_DB` for its target and
  only ever resolves its own fixed demo path.
- LinkedIn/`.ics` file parsing is local and offline, matching the existing "no surprise network activity" rule;
  neither extractor makes an outbound request.

## Testing strategy

- App tests cover `init` and deterministic demo seeding.
- Adapter tests cover ICS/LinkedIn independence, self filtering, deduplication, and raw-content exclusion.
- Router tests cover all five accepted source values (`email`, `mbox`, `vcard`, `ics`, `linkedin`) plus unknown.
- MCP tests exercise the two new sources; E2E retains explicit `mbox` coverage and adds one new-source flow.
- CLI tests prove demo isolation/reset behavior.
- Packaging acceptance builds and installs the wheel in a clean environment and runs `people-context demo --reset`.

## Open questions

1. Should `init` be allowed to run against a non-empty database (adding to existing data) or should it, like
   `demo`, refuse to run a second time without an explicit flag?
2. Is a shared RFC 5545/6350 line-folding helper module worth extracting now (reducing duplication between
   `vcard_import.py` and `ics_import.py`), or is copying the small helper functions once acceptable given the
   project's preference for narrow, independent adapters?
3. LinkedIn's exact CSV column set and header-preamble format have changed across export-tool versions; should
   the extractor validate a known header set strictly (failing closed on drift) or tolerate a superset of
   expected columns?
4. Should the packaged fictional dataset use Python constants or declared JSON package data under `src/people_context/`? Either is acceptable; `tests/fixtures` is not.
