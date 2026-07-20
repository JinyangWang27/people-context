# M14 — Ecosystem & interoperability

Status: Planned. See [docs/roadmap.md](../roadmap.md#m14--ecosystem--interoperability).

## Motivation

M8 puts the server where MCP users look; this milestone reaches users who are *not* in an MCP client yet, by
meeting three adjacent ecosystems on their own terms. A compact person brief delivers value inside any chat
surface with zero setup — a wedge that converts to full MCP use later. vCard export closes the portability
loop the importer opened, making the store a credible primary address book rather than a one-way sink. New
importers widen the funnel (every "import X into a personal CRM" search is a landing opportunity). And the
Obsidian ecosystem — already half-served by the deterministic vault export — gets a live plugin, whose
community directory is itself a discovery channel like the M8 registries.

The repository already has the precedent this milestone needs for a TypeScript deliverable: `openclaw-plugin/`
is an in-repo TS package with its own `package.json`, `vitest` tests, built `dist/`, and a dedicated publish
workflow (`.github/workflows/package-publish.yml`).

## Scope

In scope:

- `people-context brief PERSON [--include-sensitive]` (CLI-only markdown brief);
- `people-context export-vcard [--output FILE]` (CLI-only deterministic vCard export);
- two new import sources through the (M9-relocated) `adapters/import_router.py`: Outlook/Exchange contacts
  CSV (`source_type="outlook"`) and WhatsApp chat export (`source_type="whatsapp"`);
- an Obsidian community plugin under `obsidian-plugin/`, following the `openclaw-plugin/` packaging precedent.

Non-goals:

- CardDAV or any synchronization protocol — export is one-way file output (CardDAV stays a post-roadmap
  candidate);
- a `brief` MCP tool — agents already have `get_person_context`/`get_communication_guidance`; `brief` exists
  for humans pasting into non-MCP surfaces, and keeping it CLI-only preserves the rule that bulk-disclosure
  formatting stays human-operated;
- parsing WhatsApp message bodies, attachments, or media — participants and timestamps only (see Security);
- Signal import — current Signal backups are encrypted and have no stable plaintext export; revisit if that
  changes;
- an Obsidian plugin that writes to the database, or that opens the database file directly at all — it is a
  read-only consumer of the CLI's disclosure-gated output (see Design).

## Design

### `people-context brief`

App use case `app/compose_person_brief.py` composing three existing reads — `GetPersonContext`
(`app/get_person_context.py`), `GetCommunicationGuidance` (`app/get_communication_guidance.py`), and
`ListReminders` (`app/list_reminders.py`) — into one deterministic markdown document: identity and aliases,
relationships with perspective `display_type`, current affiliations, durable facts, communication guidance
signals, and open reminders. The third dependency is required, not optional: context and guidance both
deliberately include only `communication_note` reminders, so "open reminders" (scheduled `follow_up` and
`occasion` kinds included) is reachable only through `ListReminders`' person filter. Sensitivity behaves
exactly like vault export: elevated-sensitivity material requires the explicit `--include-sensitive` flag,
and the brief's footer states that the exported text is outside server disclosure controls. Output goes to
stdout by default (it is meant to be piped/pasted) — as markdown, or as a stable JSON document via `--json`
(the machine form the Obsidian plugin below consumes) — or to a file via `--output` with the `0o600` export
convention.

### `people-context export-vcard`

New filesystem adapter `adapters/filesystem/vcard_writer.py` mirroring the vault writer's determinism rules:
stable person ordering, stable property ordering, byte-identical re-export over unchanged data. Field mapping
emits `FN`/`N` from the person name, `NICKNAME` from `nickname` aliases, `EMAIL` from `handle` aliases that parse as
addresses, and `BDAY` from `predicate="birthday"` facts. The existing importer consumes only the first `ORG` and
first `TITLE`, so this milestone deliberately exports at most one active affiliation per person: choose it by
normalized organization name, normalized role, then affiliation id, and report additional active rows through an
`omitted_affiliations` CLI summary count. This makes affiliation lossiness explicit while preserving a truthful
round-trip guarantee without expanding the importer in the same PR. Elevated-sensitivity facts follow the same
`--include-sensitive` gate as vault export. One `--version {3.0,4.0}` flag selects the dialect (default per Open
Questions); every emitted field must round-trip through the project's own vCard importer, and tests assert the
selected affiliation survives while omitted rows are counted deterministically.

### Outlook CSV and WhatsApp import extractors

Both are one extractor class plus a router branch, per the M9 pattern:

- `adapters/outlook_import.py::OutlookImportExtractor`: Outlook's contacts CSV export (First/Middle/Last
  Name, E-mail Address, Company, Job Title, Birthday columns among others) maps to the same candidate
  vocabulary vCard already uses — `person` (+ `handle` alias), `affiliation` (`org`, `role`), and a
  `birthday` fact. Column-set drift across Outlook versions is handled the same way the M9 LinkedIn open
  question resolves (tolerate a superset, skip rows missing required fields with per-row skip reasons).
- `adapters/whatsapp_import.py::WhatsAppImportExtractor`: WhatsApp's plain-text chat export interleaves
  `[date, time] Sender Name: message` lines. The extractor parses **only** the timestamp and sender-name
  prefix of each line: senders become `person` candidates deduplicated by normalized name across the file,
  and one `interaction` candidate per calendar day per chat (channel `"whatsapp"`) covers the conversation
  without one-candidate-per-message noise. Message text after the `: ` separator is never read into any
  candidate field.

  **Self-sender resolution widens the extractor contract explicitly.** The current `ImportExtractor.extract`
  Protocol and every concrete implementation accept only `self_addresses`; passing additional keywords to the
  existing implementations would raise `TypeError`. M14 therefore adds optional `self_names` and `self_sender`
  keyword parameters, with backward-compatible defaults, to the Protocol, the router, and every concrete
  extractor present after M9 (`EmailImportExtractor`, `VCardImportExtractor`, `IcsImportExtractor`, and
  `LinkedInImportExtractor`, plus the two new extractors). Existing sources accept and explicitly ignore the
  values they do not use; the router forwards the complete keyword set. Do not hide the contract behind
  untyped `**kwargs`.

  `ImportContent` derives `self_names` from the self person's canonical name and every alias value, normalized
  through `normalize_name`, and passes it to all extractors. Its public execution path also accepts an optional
  `self_sender` hint, exposed additively by the existing `import_content` tool for WhatsApp labels that no stored
  alias can match (`"You"`, a bare phone number). WhatsApp excludes senders matching either signal from external
  `person` candidates and marks self-participation in the day's interaction, mirroring email's `self_addresses`
  behavior. Regression tests exercise every pre-M14 accepted source value (`email`, `mbox`, `vcard`, `ics`, and `linkedin`) through `ImportContent` after the signature change.

Both stage through the unchanged `import_content` → `review_import` → `commit_import` gate.

### Obsidian plugin (`obsidian-plugin/`)

A TypeScript plugin rendering read-only "person view" panes — identity, relationships with `display_type`,
ordinary-disclosure facts, recent interactions, open reminders — that never go stale the way one-shot vault
exports do. The vault export (M7) remains the offline/portable path; the plugin is the live path, and both
follow the same naming and perspective conventions ([docs/vault-export.md](../vault-export.md)).

**Data access goes through the CLI, never raw SQLite.** A direct SQLite reader would bypass every disclosure
control this project has (no sensitivity filtering — the exact policy `brief`, vault export, and the MCP
tools all enforce) and would break outright against an M12 SQLCipher database, which only the canonical
keyed open path can read. The plugin therefore shells out to the locally installed CLI —
`people-context list --json` for the person index and `people-context brief PERSON --json` for pane content —
so disclosure policy, database-path resolution, and encryption support are inherited from the one canonical
Python path instead of reimplemented in TypeScript. The plugin never passes `--include-sensitive`; elevated
material is unreachable from Obsidian by construction. This also eliminates the WAL-snapshot problem: the
plugin holds no database handle at all, and caching is plugin-side per-pane with manual/interval refresh.
Consequences: the plugin declares itself **desktop-only** in its manifest (it spawns a local process, which
also must be disclosed per Obsidian's guidelines), and it degrades to a clear "CLI not found — install
people-context" state with a configurable binary path setting.

**Development and publication are separate layouts.** Obsidian's community submission expects
`manifest.json` at the root of a dedicated repository's default branch, with releases carrying `main.js`,
`manifest.json`, and optionally `styles.css` — an npm-style package directory inside this monorepo is not
submittable as-is. Development therefore lives here under `obsidian-plugin/` (structured like
`openclaw-plugin/`: own `package.json`, `tsconfig.json`, `vitest`), and a CI job deterministically mirrors
each tagged plugin release into a dedicated distribution repository (e.g. `people-context-obsidian`) whose
root carries `manifest.json` and whose GitHub releases carry the built artifacts. The community-directory
submission points at the mirror repository.

## Migration needs

None. No schema change; new importers write only staged candidates, exports and the plugin only read.

## CLI / MCP surface changes

No new MCP tool. `import_content` gains two accepted `source_type` values (`"outlook"`, `"whatsapp"`) and
one additive optional parameter, `self_sender` (WhatsApp source only; ignored elsewhere), with unchanged
response shape. On the CLI, the existing `list` command gains `--json` alongside the new commands, since it
serves as the Obsidian plugin's person index.

```text
uv run people-context brief PERSON [--include-sensitive] [--json] [--output FILE]
uv run people-context export-vcard [--output FILE] [--include-sensitive] [--version 4.0]
uv run people-context list [--all] [--json]
```

## Security / privacy considerations

- `brief` and `export-vcard` produce text outside server disclosure controls — both carry the vault-export
  caveat, both default to excluding elevated-sensitivity material, and both stay CLI-only so no model can
  trigger bulk formatted disclosure.
- WhatsApp import is the strictest raw-content test this project has faced: chat text is exactly the material
  [docs/privacy-and-safety.md](../privacy-and-safety.md#no-raw-emails-conversations-or-transcripts) exists to
  keep out. The extractor's contract — nothing after the sender-name separator is ever read into a candidate —
  is enforced by a sentinel test (a unique string planted in message bodies must appear in no staged
  candidate), the same `_NOTE_SENTINEL` pattern the vCard tests use.
- Both new importers are local, file-based, and offline, per the existing no-surprise-network rule.
- The Obsidian plugin holds no database handle: it consumes only the CLI's disclosure-gated JSON output,
  never passes `--include-sensitive`, and never implements a write path, keeping every mutation behind the
  audited server/CLI surfaces. Its pane cache must stay vault-local and is subject to Obsidian's own sync —
  the plugin's docs must say that anything rendered into a synced vault leaves this project's disclosure
  perimeter, the same caveat vault export carries.

## Testing strategy

- App layer: fake-port tests for `ComposePersonBrief` (composition, sensitivity gating, deterministic
  ordering, and inclusion of all open reminder kinds — a `follow_up` reminder must appear even though
  context/guidance would omit it).
- Self-sender resolution: WhatsApp fixtures where the user's own messages appear under a matching alias
  (nickname/native-script/transliteration), under "You", and under a phone number with `self_sender` set —
  asserting the self person never appears as a staged `person` candidate in any of them.
- Adapter layer: `test_vcard_export.py` — determinism (byte-identical re-export), sensitivity gating, and a
  full round-trip through `VCardImportExtractor` asserting people/aliases/affiliations/birthday facts
  survive; `test_outlook_import.py` and `test_whatsapp_import.py` modeled on `test_vcard_import.py`
  (per-row/line independence, skip reasons, cross-file dedup, raw-content sentinel).
- Router: extend the M9 `test_import_router.py` dispatch matrix with both new source types.
- CLI layer: `brief` snapshot test incl. `--include-sensitive` difference; `export-vcard` determinism and
  `0o600` checks.
- Obsidian plugin: `vitest` unit tests over the CLI-invocation layer against recorded `--json` fixtures
  (including the CLI-missing and non-zero-exit paths), following the `openclaw-plugin/` test layout;
  rendering is exercised by fixture snapshots, not a live Obsidian instance. The mirror workflow is verified
  by a CI dry run asserting the mirrored tree contains root-level `manifest.json` and the release artifacts.
- E2E: one stdio case committing a WhatsApp import and asserting the sentinel never reaches
  `get_person_context` output.

## Open questions

1. Which vCard dialect should be the export default — 4.0 (cleaner, UTF-8 native) or 3.0 (wider legacy
   importer support, notably older Outlook/Google flows)?
2. Should `brief` offer named templates (e.g. `--style meeting-prep` vs. `--style intro`) in this milestone,
   or ship one canonical layout first?
3. WhatsApp export formats vary by platform and locale (bracket styles, date order, 12/24h). Should v1
   support a fixed set of detected formats with explicit per-file failure, or accept a `--format` hint flag?
4. Should the Obsidian plugin's CLI calls target `uvx people-context` when no installed binary is configured
   (zero-setup for `uv` users, but slower cold starts), or require an explicit binary path?
5. What refresh model should panes default to — manual only, on-file-open, or a polling interval — given each
   refresh spawns a CLI process?
