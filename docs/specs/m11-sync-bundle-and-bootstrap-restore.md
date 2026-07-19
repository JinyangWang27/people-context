# M11 — Sync bundle export and trusted bootstrap restore

Status: Planned. See [docs/roadmap.md](../roadmap.md#m11--sync-bundle-export-and-trusted-bootstrap-restore).

## Motivation

M6 built the local half of replication — a persisted device identity, a hybrid logical clock, and a full
replayable `changelog` written atomically alongside every mutation — and deliberately stopped there: "M6
deliberately added no exchange, pairing, relay, peer cursor, replay engine, bootstrap restore, or MCP sync
tool" ([docs/roadmap.md](../roadmap.md), M6 section). M7 confirmed the same boundary for its own scope. The
result is that, today, a user with two devices or a desire for a backup has exactly two options, both
explicitly discouraged or incomplete: copying the raw SQLite file (which
[docs/data-model.md](../data-model.md#sync-foundation-tables-migration-002) warns against for anything beyond a
one-time move, because two live copies interleave one `devices` row's persisted HLC state), or
`people-context export`, which is a JSON snapshot of primary domain rows only — no changelog, no device
identity, no HLC watermark — and which [docs/design/sync.md](../design/sync.md#65-first-sync-bootstrap)
explicitly says "is a useful snapshot shape" but "must not be fed through `import_content`, `stage_candidates`,
or review staging" as a restore path, because none of those exist for that purpose.

This milestone closes the gap between "nothing" and the full sync design in
[docs/design/sync.md](../design/sync.md) by shipping only the lowest-risk slice that design already calls out:
bundle export (§6.5's "export the portable primary dataset... include the changelog/device metadata needed to
identify watermark H") and bootstrap restore onto a *fresh* device. It deliberately does not attempt
incremental, two-way replay between two already-diverged devices — the source analysis names that explicitly
as "the risky part" that "can be phased," and section 4 of the sync design (conflict resolution, LWW policy,
`sync_conflicts` review) is a substantial, separate piece of work this milestone does not touch.

## Scope

In scope:

- `people-context sync push`: write one JSON bundle file containing the portable snapshot, the complete
  changelog, and the originating device's HLC watermark, to a user-chosen directory;
- `people-context sync pull`: read a bundle file and restore it into a **freshly initialized, still-empty**
  database only;
- the minimal port/adapter surface needed for both, built on top of the existing `ExportReader` and
  `Changelog` ports;
- an additive widening of `Changelog.list_entries`'s `limit` parameter.

Non-goals (all explicitly deferred, matching [docs/design/sync.md](../design/sync.md)'s own phasing):

- incremental replay into a database that already has independent local history — `sync pull` refuses this
  outright rather than attempting any merge;
- conflict detection/resolution, `sync_conflicts` table usage, field-level last-writer-wins — all of section 4
  of the sync design;
- pairing, device registration UX, relay transport, or any encrypted batch-exchange protocol — section 6 of the
  sync design;
- multi-user ownership/sharing — section 7 of the sync design;
- an MCP tool for any of this. Bundle push/pull are file-writing operations and stay CLI-only, exactly like
  `export-vault` is today ([docs/mcp-interface.md](../mcp-interface.md#operator-elevated-reads): "M7
  intentionally adds no MCP tool that writes arbitrary directories").

## Design

### Bundle contents and envelope

The bundle carries the same row shapes two already-verified read paths produce today, but must **not** be
assembled by calling them independently: the CLI is explicitly supported beside a concurrently writing server,
and `SqliteExportReader.read_export()` itself issues many separate table queries without opening a read
transaction, so two independent reads can observe different database states — a changelog operation whose
primary row is absent from the snapshot, or snapshot state beyond the claimed watermark. A new narrow port,
`ports/sync_bundle.py::BundleReader` (one method), implemented in `adapters/sqlite/bundle_reader.py`, performs
every read inside one `SqliteUnitOfWork` transaction so the domain snapshot, the relationship vocabulary, the
complete changelog, the origin device row, and the HLC watermark all come from the same SQLite snapshot:

- the domain-collection reads reuse the row/serialization shape of `ExportReader.read_export()` →
  `ExportSnapshot` (`src/people_context/ports/export.py:10-23`), already backing `people-context export` and
  `ExportData` (`src/people_context/app/export_data.py`);
- the changelog read reuses the row shape of `Changelog.list_entries` → `list[ChangelogEntry]`
  (`src/people_context/ports/changelog.py:36-41`), the same read already backing `sync-log`
  (`cli.py:_cmd_sync_log`), but unbounded.

`Changelog.list_entries`'s current signature defaults `limit: int = 100`
(`src/people_context/ports/changelog.py:41`, implemented in
`src/people_context/adapters/sqlite/changelog.py:74-82`) — bundle export needs every entry, not the most recent
100, so this milestone widens the port to `limit: int | None = 100`, where `None` means unbounded. This is an
additive, backward-compatible Protocol change: every existing caller (`sync-log`'s CLI command) passes an
explicit int or relies on the unchanged default, and the SQLite implementation only needs to skip the `LIMIT`
clause when `limit is None`. The bundle reader may reuse `SqliteChangelog` internally for this read, since
`SqliteUnitOfWork` joins an already-open outer transaction rather than starting its own.

`ExportSnapshot` contains no `relationship_types` or `relationship_type_synonyms` collections, and M7's
add-only curation (`AddRelationshipType`, `relationship-types add`) means those tables can carry user-authored
rows that exist nowhere else. Restoring relationships without them would strand custom type strings with no
vocabulary row behind them, silently breaking synonym resolution, inverse canonicalization, and perspective
`display_type` rendering on the restored device. The bundle therefore includes both vocabulary tables (seeded
and custom rows alike; restore writes them by primary key, so rows matching the freshly-seeded defaults are
harmless). The same omission affects the plain `people-context export` document today and should be fixed
there too, but that is follow-up work outside this milestone's scope.

The bundle is a single JSON file, following the same `format`/`version`/timestamp envelope convention already
used by `ExportDocument` (`src/people_context/app/export_data.py:14-30`) and already sketched for a sync batch
in [docs/design/sync.md §6.3](../design/sync.md#63-batch-envelope):

```json
{
  "format": "people-context-sync-bundle",
  "version": 1,
  "created_at": "2026-07-19T12:00:00Z",
  "origin_device_id": "...",
  "watermark": {"hlc_physical_ms": 1755000000000, "hlc_logical": 3},
  "snapshot": { "people": [...], "organizations": [...], "...": "...same shape as ExportSnapshot" },
  "relationship_vocabulary": {"types": [...], "synonyms": [...]},
  "changelog": [{"op_id": "...", "device_id": "...", "hlc_physical_ms": 0, "...": "...same shape as ChangelogEntry"}]
}
```

This is deliberately **not** the `transactions`/`tombstones`/`acknowledgements` batch shape sketched in
[docs/design/sync.md §6.3](../design/sync.md#63-batch-envelope) — that shape is designed for incremental,
per-origin-cursor exchange between multiple already-paired devices, which is out of scope here. This bundle is
closer to the bootstrap-snapshot sketch in [§6.5](../design/sync.md#65-first-sync-bootstrap): a complete,
point-in-time hand-off, not an incremental delta.

### New app-layer use cases

`app/sync_bundle.py` (new module, following the existing one-use-case-per-module convention):

- `ExportSyncBundle` — takes a `BundleReader` and returns a `SyncBundle` Pydantic model matching the envelope
  above. This is a pure read, "a read-only changelog consumer like the existing sync-log CLI," per the source
  analysis — no mutation; the single-transaction consistency guarantee lives in the adapter, not here.
- `RestoreSyncBundle` — takes a `PersonReader` (to check the target is empty, via the existing
  `list_people(include_deleted=True, limit=1)`), a `Changelog` (to check it is empty, via
  `list_entries(limit=1)`), and a new narrow port (below) that performs the actual verbatim bulk write. Refuses
  with a structured error (mirroring `ImportPipelineError`'s `code`/`message`/`details` shape used elsewhere in
  this codebase) when the target already has primary data or changelog history.

### New port and adapter for verbatim bulk restore

Every existing writer port (`PersonWriter.save_person`, the record-store writers behind `RecordFact` /
`RecordInteraction` / etc.) is shaped for *use-case-driven creation*: it mints new audit entries, and several
call sites generate new ids or normalize input. Bootstrap restore needs the opposite: insert exactly the rows
the bundle contains, verbatim, including original ids, timestamps, and — per
[docs/design/sync.md §9 decision 8](../design/sync.md#decisions) ("bootstrap restore include the accountability
audit log? Leaning: yes") — the original `audit_log` rows too, without minting new ones. This is a distinct
capability, not a reuse of `RememberPerson`/`RecordFact`/etc.

Add `ports/bootstrap_restore.py::BootstrapRestorer` (narrow Protocol, one method) and implement it in
`adapters/sqlite/bootstrap_restore.py::SqliteBootstrapRestorer`. The adapter writes every table in the snapshot
(`persons`, `aliases` embedded in each person, `organizations`, `affiliations`, `relationships`, `facts`,
`observations`, `traits`, `interactions`/`interaction_participants`, `reminders`, `user_preferences`,
`audit_log`), the two relationship-vocabulary tables, and the `changelog` rows.

**Imported device identities are historical, never active.** `SqliteHybridLogicalClock._device_row()` selects
the first non-retired device ordered by `created_at, id` (`src/people_context/adapters/sqlite/hlc.py:57-64`) —
there is no other local-device marker in the schema. If restore inserted source device A's row as non-retired,
device B could silently start writing under A's identity (A's `created_at` predates B's), recreating the exact
shared-identity HLC hazard this milestone exists to avoid. Restore therefore writes one `devices` row per
distinct `device_id` seen in the changelog **with `retired_at` set** (original `created_at` and final HLC
state preserved for provenance), leaving the destination's own freshly created device (from
`db.py::_ensure_local_device`) as the sole non-retired row and the only identity the clock can ever select.

**All of restore is one transaction, including finalization.** A restore that commits primary rows first and
finalizes afterward is unretryable: the destination is no longer empty, so a second `sync pull` refuses, and a
failed HLC advancement would let future local writes sort before the restored history. The entire operation —
primary rows, vocabulary, changelog, retired device history, the FTS rebuild via the existing
`PersonSearchIndexer.rebuild_person_search()` port method (`src/people_context/ports/repository.py:47`, the
same "repair path" `reindex` documents for any direct-SQL change,
[docs/cli.md](../cli.md#direct-sqlite-access)), and the local device's HLC advancement past the bundle's
watermark via `HybridLogicalClock.observe()` (`src/people_context/ports/hlc.py:30`, implemented in
`src/people_context/adapters/sqlite/hlc.py:37-55`) — runs inside one outer `SqliteUnitOfWork`
(`src/people_context/adapters/sqlite/unit_of_work.py`). This works without any new transaction machinery
because `SqliteUnitOfWork` joins an already-open outer transaction instead of starting its own, so the inner
unit-of-work uses inside `observe()` and the indexer compose cleanly. A failure at any point rolls the
still-empty database back to empty, and a retry is always safe. Only the optional semantic reindex
(`reindex --semantic`) stays outside the transaction: it is cache-only derived data, explicitly rebuildable at
any time, and must not couple a network/model dependency into the restore transaction.

### CLI commands

`cli.py` gains a `sync` subparser with two subcommands, following the existing nested-subcommand pattern
already used for `relationship-types add` (`cli.py:178-194`):

```text
uv run people-context sync push --output DIR
uv run people-context sync pull --input PATH [--yes]
```

`push` writes `DIR/people-context-sync-bundle.json` with owner-only permissions, following the exact pattern
`_cmd_export` already uses for the plain export file (`cli.py:366-376`,
`os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)`). `pull` accepts either a direct file path
or a directory containing that fixed filename, previews what would be restored (people/organization/interaction
counts, mirroring `PreviewForget`'s existing preview-before-destructive-action pattern in
`app/forget.py`/`cli.py:_cmd_delete`), and requires `--yes` or an interactive confirmation before writing,
exactly like `delete` does today.

## Migration needs

None. No new table, no new column. Every row this milestone reads or writes already has a home in the schema
introduced by migrations `001_initial.sql` and `002_sync_foundations.sql`.

## CLI / MCP surface changes

CLI-only; no MCP tool is added or changed.

```text
uv run people-context sync push --output DIR
```

Prints a summary on success: bundle path, entity counts, changelog entry count, and the HLC watermark.

```text
uv run people-context sync pull --input PATH [--yes]
```

Refuses (exit code 1, no writes) when the target database already contains any person or changelog entry,
printing a message pointing at this being bootstrap-only and at the still-deferred incremental-sync work. On
success, prints the same summary `push` prints, for the now-restored device.

## Security / privacy considerations

- The bundle file is plaintext JSON, exactly like `people-context export` today — this milestone does not add
  encryption. [docs/design/sync.md §9 decision 1](../design/sync.md#decisions) already settles this for the
  local `changelog` table itself ("plaintext... use end-to-end encryption for exported batches"); this
  milestone's bundle *is* an exported batch in that sense, so shipping it unencrypted is a real, stated
  trade-off, not an oversight — it should be called out prominently in the CLI's help text and in
  [docs/privacy-and-safety.md](../privacy-and-safety.md), with the same guidance already given for the database
  file itself: rely on the transport (a pre-encrypted volume, an already-encrypted Syncthing/iCloud/Dropbox
  folder, or OS-level disk encryption at both ends), not on this tool.
- `push`/`pull` are CLI-only and human-operated. [docs/mcp-interface.md](../mcp-interface.md#operator-elevated-reads)
  already states the equivalent rule for vault export ("M7 intentionally adds no MCP tool that writes arbitrary
  directories") — this milestone follows the same posture: no MCP tool triggers a full-dataset file write,
  keeping high-fidelity data movement outside model-callable tool surface.
- Restore includes the accountability `audit_log`, unchanged and unredacted from the source device, per
  [docs/design/sync.md §9 decision](../design/sync.md#decisions) — a forgotten person's redacted `{"redacted":
  true}` audit/changelog payloads travel exactly as redacted on the source device; restore must not
  "un-redact" anything, since it copies payloads verbatim.
- Bootstrap restore explicitly bypasses `import_content`/`stage_candidates`/review staging, by design, per
  [docs/design/sync.md §6.5](../design/sync.md#65-first-sync-bootstrap) ("must not feed a full export through"
  those tools) — this is not a staging-gate regression, because a bootstrap restore is not new externally
  sourced data requiring review; it is the same user's own prior data, verified structurally (matching schema
  shapes) rather than reviewed record-by-record, the same trust boundary an OS-level file restore already
  carries.
- The empty-target-only refusal is itself a safety control, not merely a scoping convenience: it prevents this
  milestone's necessarily blunt, verbatim bulk-write path from ever running against a database that has its
  own independent history, which is exactly the scenario that needs the conflict-resolution machinery this
  milestone does not implement.

## Testing strategy

- App layer: fake-port tests for `ExportSyncBundle` (envelope shape, driven through a fake `BundleReader`) and
  `RestoreSyncBundle` (refusal on non-empty target via fake `PersonReader`/`Changelog`), added to
  `tests/app/` alongside the existing use-case tests, using `tests/app/fakes.py`'s existing fake stores.
- Adapter layer, bundle read consistency: `tests/adapters/test_sqlite_bundle_reader.py` asserting the snapshot,
  vocabulary, changelog, and watermark come from one transaction (e.g. a write from a second connection during
  the read must not appear partially).
- Adapter layer, restore: `tests/adapters/test_sqlite_bootstrap_restore.py`, modeled on the existing
  `tests/adapters/test_sqlite_export.py` and `tests/adapters/test_sqlite_changelog.py`, covering: full
  round-trip write of every table including both vocabulary tables; atomicity of the *entire* operation — a
  forced failure in any phase, including the FTS rebuild and the HLC advancement, leaves an empty DB, verified
  the same way `tests/adapters/test_sqlite_unit_of_work.py` verifies rollback; imported device rows are all
  retired and the destination's own device remains the single non-retired row, with
  `SqliteHybridLogicalClock.device_id` returning the destination's id (not the bundle origin's) after restore;
  and correct `HybridLogicalClock` advancement past the bundle's watermark.
- Widened `Changelog.list_entries(limit=None)`: extend `tests/adapters/test_sqlite_changelog.py` to cover
  unbounded listing alongside the existing bounded case.
- CLI layer: new tests in `tests/test_cli.py` for `sync push`/`sync pull`, including the non-empty-target
  refusal path and the 0o600 permission check already established as a pattern by the existing `export`
  command's tests.
- E2E: a `tests/adapters/test_stdio_e2e.py` case that builds a real device A (via the stdio server), adds a
  custom relationship type with an inverse and a synonym (`relationship-types add`), records a few
  people/relationships/interactions including one relationship using the custom type, runs `sync push`, opens
  a fresh device B, runs `sync pull`, and asserts: `people-context export` output is content-equal between A
  and B (excluding volatile fields like the file's own `exported_at`); the custom vocabulary behaves
  identically on B (synonym resolution on write, inverse `display_type` in `get_person_context`, canonical
  edges in `get_relationship_graph`); and a subsequent write on B produces a changelog entry whose HLC sorts
  after every entry pulled from A and carries B's own `device_id`, not A's — following the same round-trip
  verification pattern already used by `test_real_stdio_graph_then_cli_vault_export_uses_matching_links`.

## Open questions

1. Should `sync pull` validate that the source bundle's schema/migration version (`PRAGMA user_version` at
   export time) matches the destination's current migration level, and refuse or warn on mismatch, rather than
   assuming forward compatibility?
2. Should the currently-unused `sync_conflicts` table (present since migration `002_sync_foundations.sql`) be
   left untouched by this milestone, or should bootstrap restore write a zero-row marker into it to make the
   "not yet used" state explicit and queryable?
3. Should bundle files support at-rest encryption (e.g. age or GPG) as an opt-in flag in this milestone, or
   should that wait and be designed once alongside SQLCipher (M12), so the project has one at-rest-encryption
   story instead of two independent ones?
4. Once incremental replay (the deferred "risky part") is eventually designed, does this milestone's bundle
   format need to be versioned/extended, or does it stay a permanently separate "bootstrap-only" format
   alongside a future incremental-batch format?
