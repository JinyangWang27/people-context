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

The bundle reuses two already-verified read ports rather than inventing new SQL:

- `ExportReader.read_export()` → `ExportSnapshot` (`src/people_context/ports/export.py:9-30`), the same
  portable-domain-collections read already backing `people-context export` and `ExportData`
  (`src/people_context/app/export_data.py`);
- `Changelog.list_entries(limit, entity_id=None)` → `list[ChangelogEntry]`
  (`src/people_context/ports/changelog.py:35-41`), the same read already backing `sync-log`
  (`cli.py:_cmd_sync_log`).

`Changelog.list_entries`'s current signature defaults `limit: int = 100`
(`src/people_context/ports/changelog.py:41`, implemented in
`src/people_context/adapters/sqlite/changelog.py:74-82`) — bundle export needs every entry, not the most recent
100, so this milestone widens the port to `limit: int | None = 100`, where `None` means unbounded. This is an
additive, backward-compatible Protocol change: every existing caller (`sync-log`'s CLI command) passes an
explicit int or relies on the unchanged default, and the SQLite implementation only needs to skip the `LIMIT`
clause when `limit is None`.

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

- `ExportSyncBundle` — takes an `ExportReader` and a `Changelog`, calls both, and returns a `SyncBundle`
  Pydantic model matching the envelope above. This is a pure read composition, "a read-only changelog consumer
  like the existing sync-log CLI," per the source analysis — no `UnitOfWork`, no mutation.
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
`audit_log`) plus the `changelog` rows and one `devices` row per distinct `device_id` seen in the changelog,
all inside one `SqliteUnitOfWork` (`src/people_context/adapters/sqlite/unit_of_work.py`), so a failure partway
through leaves the still-empty database untouched rather than half-restored. After the transaction commits, the
CLI command calls the existing `ReindexPeople.execute()` (`src/people_context/app/reindex_people.py`, already
used by `cli.py:_cmd_reindex`) to rebuild FTS, since these rows were not written through the repository's
normal `save_person` path that maintains `person_search` incrementally — the same "repair path" `reindex`
already documents for any direct-SQL change
([docs/cli.md](../cli.md#direct-sqlite-access)). It then advances the local device's HLC past the bundle's
watermark via the existing `HybridLogicalClock.observe()` port method
(`src/people_context/ports/hlc.py:22-30`, implemented in
`src/people_context/adapters/sqlite/hlc.py:37-55`), so the restored device's own future writes are guaranteed
to sort after every operation the bundle carried in.

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

- App layer: fake-port tests for `ExportSyncBundle` (envelope shape, unbounded changelog inclusion) and
  `RestoreSyncBundle` (refusal on non-empty target via fake `PersonReader`/`Changelog`), added to
  `tests/app/` alongside the existing use-case tests, using `tests/app/fakes.py`'s existing fake stores.
- Adapter layer: `tests/adapters/test_sqlite_bootstrap_restore.py`, modeled on the existing
  `tests/adapters/test_sqlite_export.py` and `tests/adapters/test_sqlite_changelog.py`, covering: full
  round-trip write of every table, atomicity (a forced failure partway through leaves an empty DB, verified the
  same way `tests/adapters/test_sqlite_unit_of_work.py` verifies rollback), and correct `HybridLogicalClock`
  advancement past the bundle's watermark.
- Widened `Changelog.list_entries(limit=None)`: extend `tests/adapters/test_sqlite_changelog.py` to cover
  unbounded listing alongside the existing bounded case.
- CLI layer: new tests in `tests/test_cli.py` for `sync push`/`sync pull`, including the non-empty-target
  refusal path and the 0o600 permission check already established as a pattern by the existing `export`
  command's tests.
- E2E: a `tests/adapters/test_stdio_e2e.py` case that builds a real device A (via the stdio server), records a
  few people/relationships/interactions, runs `sync push`, opens a fresh device B, runs `sync pull`, and asserts
  `people-context export` output is content-equal between A and B (excluding volatile fields like the file's
  own `exported_at`), and that a subsequent write on B produces a changelog entry whose HLC sorts after every
  entry pulled from A — following the same round-trip verification pattern already used by
  `test_real_stdio_graph_then_cli_vault_export_uses_matching_links`.

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
