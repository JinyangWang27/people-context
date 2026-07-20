# M14 — Ecosystem & interoperability

Status: Planned. See [docs/roadmap.md](../roadmap.md#m14--ecosystem--interoperability).

## Motivation

M14 meets adjacent ecosystems without adding a new MCP tool or database write path: deterministic person briefs
for non-MCP surfaces, one-way vCard portability, Outlook/WhatsApp file imports, and a read-only Obsidian plugin.
The repository's `openclaw-plugin/` package provides the TypeScript testing and lockfile precedent, while M11's
private-file helper provides the canonical export safety primitive.

## Scope

In scope:

- `people-context brief PERSON [--include-sensitive] [--json] [--output FILE]`;
- deterministic `people-context export-vcard`;
- Outlook contacts CSV and WhatsApp plaintext-export extractors;
- a desktop-only read-only Obsidian plugin under `obsidian-plugin/`.

Non-goals:

- CardDAV or live Outlook/WhatsApp APIs;
- a `brief` MCP tool;
- WhatsApp message-body or attachment retention;
- Signal backup parsing;
- direct SQLite access or write-back from Obsidian.

## Design

### `people-context brief`

Add `ComposePersonBrief`, composing `GetPersonContext`, `GetCommunicationGuidance`, and `ListReminders` into one
deterministic Markdown or stable JSON document. `ListReminders` is required because scheduled `follow_up` and
`occasion` rows are not supplied by the other two reads.

Sensitivity behavior is explicit:

- call `GetPersonContext.execute(..., purpose="communication", include_sensitive=flag)` so
  `--include-sensitive` widens context-backed facts, interactions, and traits;
- `GetCommunicationGuidance` remains ordinary-disclosure under its existing contract in both modes;
- label that distinction in Markdown and JSON metadata, and state that exported output is outside server
  disclosure controls.

The JSON form is a declared machine interface used by the Obsidian plugin. It has a documented schema/version and
follows the M12 additive-field compatibility rule. Ordering is stable across people-linked collections.

Stdout is the default. `--output` uses
`adapters/filesystem/private_file.py::atomic_write_private_text`; do not duplicate an `O_TRUNC` writer.

### `people-context export-vcard`

Add typed export DTOs and a `VCardWriter` Protocol under `ports/vcard.py`,
`ExportVCard(ExportReader, VCardWriter, Clock)`, and a pure filesystem serializer. The app use case excludes
soft-deleted people, evaluates active records at `clock.now().date()`, applies the sensitivity gate, and passes an
already-filtered projection to the adapter. App code never imports the filesystem adapter.

Mapping is deliberately lossless and non-heuristic:

- `FN` is the canonical name;
- `N` contains the entire escaped canonical name in the family-name component and leaves the remaining four
  components empty (`N:<canonical-name>;;;;`); do not guess given/family-name boundaries from whitespace;
- `NICKNAME` comes from nickname aliases;
- `EMAIL` comes only from handle aliases that parse as email addresses;
- at most one active `ORG`/`TITLE` pair is emitted because the current importer consumes only the first pair;
- at most one full-date `BDAY` is emitted because the current importer consumes only the first birthday.

Affiliation selection is deterministic: normalized organization name, normalized role, then affiliation id.
Report additional active affiliations as `omitted_affiliations`.

The no-importer-change round-trip promise constrains birthday portability. The current importer stores the BDAY
text verbatim, while the project's recurring `--MM-DD` form is not the same representation a conforming vCard 4
partial date would use and is not portable to vCard 3. Therefore M14 exports only full ISO `YYYY-MM-DD` birthday
values. Selection among valid full dates is highest confidence, newest `recorded_at`, then id. Report:

- additional valid full-date rows as `omitted_birthdays`;
- project recurring `--MM-DD` values as `skipped_partial_birthdays`;
- all other unparseable birthday text as `skipped_unparseable_birthdays`.

Elevated birthday facts contribute to no count unless `--include-sensitive` is active. A future importer-normalizing
change may add vCard 4 partial-date support in a separate PR; M14 must not emit a non-standard spelling merely to
preserve the current raw value.

`--version {3.0,4.0}` selects the dialect. Escaping, folding, CRLF line endings, property order, person order, and
selection rules are canonical so the same projection and clock produce identical bytes. The filesystem writer
uses the shared atomic private-file helper. Every emitted field must round-trip through the unchanged
`VCardImportExtractor`, including exact full birthday values and the selected affiliation.

### Outlook CSV and WhatsApp import extractors

Add `OutlookImportExtractor` and `WhatsAppImportExtractor` through the M9 router.

Outlook maps names, email handle, optional company/role, and a parseable birthday onto the existing candidate
vocabulary. It tolerates documented column supersets, handles row failures independently, and never stages profile
URLs or free-text notes.

WhatsApp reads only timestamp and sender prefix. It never copies text after the sender separator into a candidate,
skip reason, log, or error. External senders become person candidates deduplicated by normalized sender identity;
one neutral `interaction` per calendar day represents the chat.

Self resolution widens `ImportExtractor.extract` explicitly with optional `self_names` and `self_sender` keyword
parameters on the Protocol, router, and every concrete extractor. Existing sources accept and ignore parameters
they do not use; no untyped `**kwargs`.

`ImportContent` derives normalized self names from the self person's canonical name and all aliases and forwards an
optional explicit sender hint for labels such as `You` or a bare phone number.

The unchanged candidate contract has no separate self-participation field. Therefore WhatsApp self participation
is implicit, exactly as email import is implicit:

- a matching self sender produces no external person candidate;
- the self label is omitted from `participant_refs`;
- an interaction candidate contains only external batch-local participant references;
- a day containing only self messages produces no interaction candidate rather than an invalid zero/unknown-ref
  candidate.

Both importers retain the unchanged stage → review → commit approval gate. Regression tests exercise all seven
accepted source values: `email`, `mbox`, `vcard`, `ics`, `linkedin`, `outlook`, and `whatsapp`.

### Obsidian plugin (`obsidian-plugin/`)

The plugin renders live read-only person panes from CLI JSON. It never opens SQLite. It calls:

- `people-context list --json` for the index;
- `people-context brief <person-id> --json` for details.

The detail command always uses the stable id returned by the index, never a display name.

#### Process-execution safety

All contact data is untrusted command input. The bridge uses `child_process.spawn` or `execFile` with a separate
argument array and `shell: false`; it never constructs a shell command string. The configured binary path is the
executable field, not interpolated text. Do not provide a free-form extra-arguments string.

Apply:

- finite timeout with process-tree termination;
- bounded stdout and stderr capture with a clear oversized-output error;
- abort handling and non-zero-exit reporting;
- `windowsHide: true` where applicable;
- no logging of JSON payloads or `PEOPLE_CONTEXT_DB_KEY`.

Tests use names containing spaces, quotes, semicolons, `$()`, backticks, ampersands, pipes, percent signs, carets,
and Windows metacharacters, proving they remain inert display data and are never used as detail-command arguments.

#### Database/encryption settings

Settings are typed, not arbitrary shell fragments:

- executable path;
- optional database path;
- encrypted-database boolean;
- refresh policy.

Build the fixed argument prefix as an array: optional global `--db <path>`, optional `--encrypted`, then the
subcommand arguments. An encrypted invocation inherits `PEOPLE_CONTEXT_DB_KEY` from the Obsidian process
environment; the plugin never stores, prompts for, or logs the key. If the GUI process lacks the variable, show the
canonical CLI missing-key error plus an actionable instruction to launch/configure Obsidian with that environment;
do not fall back to plaintext.

The plugin never passes `--include-sensitive`. Anything cached/rendered in a synced vault has left the project's
disclosure perimeter, which must be documented.

#### Package and publication

Commit `obsidian-plugin/package.json` and `package-lock.json`, plus TypeScript/Vitest/build configuration. Every PR
and release build uses `npm ci --no-audit --no-fund`; no `npm install` mutation in CI. Build twice from clean
lockfile installations in the dry-run job and compare release-artifact checksums. If the monorepo chooses to
commit `dist/`, also fail when rebuilding dirties it; otherwise build artifacts only in CI.

Development remains in the monorepo. A tagged plugin release mirrors a deterministic tree to a dedicated
community-distribution repository whose root contains `manifest.json` and whose release contains `main.js`,
`manifest.json`, and optional `styles.css`. The plugin manifest declares desktop-only operation.

## Migration needs

None.

## CLI / MCP surface changes

No new MCP tool. `import_content` adds accepted `outlook`/`whatsapp` values and optional `self_sender`; response
shape remains unchanged. CLI additions:

```text
uv run people-context brief PERSON [--include-sensitive] [--json] [--output FILE]
uv run people-context export-vcard [--output FILE] [--include-sensitive] [--version 4.0]
uv run people-context list [--all] [--json]
```

`list --json` has a documented versioned/additive schema. By default it excludes soft-deleted people; `--all`
marks lifecycle state explicitly.

## Security and privacy

- Brief/vCard file output uses the shared atomic private writer and defaults to ordinary disclosure.
- WhatsApp raw-body exclusion is enforced with unique sentinel strings in bodies, logs, errors, staged rows, and
  committed context.
- Outlook and WhatsApp parsing are local and offline.
- Obsidian uses only disclosure-gated CLI JSON, never sensitive mode, raw SQLite, shell execution, or write tools.
- Plugin cache and rendered content may be synchronized by Obsidian and are outside this project's perimeter.

## Testing strategy

- Brief fake-port tests for composition, all reminder kinds, deterministic ordering, sensitive-context gating,
  and ordinary-only guidance in both modes; JSON schema/additive fixture tests.
- vCard app tests for as-of filtering, sensitivity, deterministic affiliation/full-birthday selection, all three
  birthday counters, and no adapter import; adapter tests for 3.0/4.0 canonical bytes, non-heuristic `N`, and
  unchanged-importer round trip.
- File tests pre-create `0o644` destinations, assert final `0o600` on POSIX, verify symlink replacement does not
  modify the target, and preserve the previous file after a failed write.
- Import router matrix covers all seven sources plus unknown; WhatsApp tests cover alias/hint self exclusion,
  implicit self participation, self-only days, external participants, locale formats, and raw sentinels.
- Outlook tests cover header supersets, row independence, birthday validation, and raw-field exclusion.
- Obsidian Vitest tests cover argument arrays, stable-id lookup, all metacharacter fixtures, timeout, cancellation,
  output limits, CLI-not-found, non-zero exit, database path with spaces/metacharacters, encrypted toggle, and the
  missing-key no-fallback path.
- Node workflows use the committed lockfile and verify deterministic release artifacts.
- E2E commits a WhatsApp batch and proves body sentinels never reach `get_person_context`.
- `uv run ruff check .`, `uv run pytest -q`, `npm ci`, `npm test`, and plugin build all pass.

## Open questions

1. Should vCard 4.0 or 3.0 be the default?
2. Should a later importer-normalization PR support partial birthdays in vCard 4.0?
3. Which explicitly detected WhatsApp locale formats ship first?
4. Should plugin refresh default to manual or on-open?
