# M11 — Sync bundle export and trusted bootstrap restore

Status: Planned. See [docs/roadmap.md](../roadmap.md#m11--sync-bundle-export-and-trusted-bootstrap-restore).

## Motivation

M6 added a persisted device identity, hybrid logical clock, and replayable changelog written atomically with
ordinary mutations. It deliberately did not add exchange, pairing, replay, or bootstrap restore. Copying a live
SQLite file can duplicate one active device identity, while `people-context export` omits devices, vocabulary,
the HLC watermark, and the changelog. This milestone ships only the low-risk bootstrap slice: export one complete,
point-in-time bundle and restore it into a freshly initialized database. Incremental replay between independently
modified databases remains deferred.

## Scope

In scope:

- `people-context sync push`: write one complete bootstrap bundle;
- `people-context sync pull`: restore that bundle into a fresh, still-empty database only;
- a single-snapshot `BundleReader`;
- strict, versioned bundle DTOs and fail-closed validation;
- a narrow verbatim `BootstrapRestorer` port;
- an additive `Changelog.list_entries(limit=None)` read;
- a shared atomic private-file writer for personal-data exports.

Non-goals:

- incremental/two-way replay, conflict resolution, acknowledgements, pairing, relay transport, or multi-user
  ownership;
- an MCP sync tool;
- bundle encryption;
- semantic-vector transfer.

## Design

### Single-snapshot bundle reader

Add `ports/sync_bundle.py::BundleReader` and `adapters/sqlite/bundle_reader.py::SqliteBundleReader`. One reader
call opens one SQLite read transaction and returns, from the same snapshot:

- the complete portable domain snapshot in the existing `ExportSnapshot` row shape;
- both relationship-vocabulary tables, including custom rows;
- every changelog entry in deterministic ascending comparison-key order;
- every device referenced by the changelog, plus the active origin device;
- the origin device id and current HLC watermark.

`Changelog.list_entries` widens from `limit: int = 100` to `limit: int | None = 100`; `None` means all rows.
Existing callers retain the same default and behavior. Bundle export must not call independent readers that can
observe different WAL snapshots.

Every collection has an explicit stable order. With the same database snapshot and injected clock,
`ExportSyncBundle` produces byte-identical canonical JSON.

### Strict bundle envelope

Add explicit Pydantic DTOs for the envelope and every nested row accepted by restore. They are restore contracts,
not loose `dict[str, Any]` aliases:

- top-level `format: Literal["people-context-sync-bundle"]`;
- top-level `version: Literal[1]`;
- `ConfigDict(extra="forbid")` on the top-level model and every nested envelope, snapshot-row, vocabulary-row,
  device-row, watermark, and changelog-row DTO;
- required fields have no silent defaults on the restore-input model;
- timestamps must be timezone-aware and normalized to UTC;
- ids and operation ids must be non-blank.

Example envelope:

```json
{
  "format": "people-context-sync-bundle",
  "version": 1,
  "created_at": "2026-07-19T12:00:00Z",
  "origin_device_id": "...",
  "watermark": {"hlc_physical_ms": 1755000000000, "hlc_logical": 3},
  "devices": [{"id": "...", "display_name": "...", "public_key": null,
               "created_at": "...", "retired_at": null,
               "hlc_physical_ms": 1755000000000, "hlc_logical": 3}],
  "snapshot": {"people": [], "organizations": [], "affiliations": [], "relationships": [],
               "facts": [], "observations": [], "traits": [], "interactions": [], "reminders": [],
               "user_preferences": [], "audit_log": []},
  "relationship_vocabulary": {"types": [], "synonyms": []},
  "changelog": []
}
```

Parsing and document-level validation happen before preview, user confirmation, transaction acquisition, or any
write. Wrong format, unsupported version, missing required fields, unknown fields, malformed nested rows, and
duplicate primary/op ids all fail with a structured `invalid_bundle` error.

Document-level cross-field validation also requires:

- `origin_device_id` names exactly one row in `devices`, and that source row is non-retired;
- every changelog `device_id` exists in the bundle's `devices` collection;
- all primary keys, device ids, vocabulary keys, and `op_id` values are unique in their collections;
- the watermark is greater than or equal to every bundled changelog `(hlc_physical_ms, hlc_logical)` pair and
  every bundled device's persisted HLC pair;
- relationship-vocabulary references and every domain foreign-key reference resolve within the bundle snapshot
  or to the seeded destination vocabulary as explicitly allowed.

The source `PRAGMA user_version` may be added to a later envelope version; whether to require exact migration
parity remains an open question. Format/version and structural validation are not optional or deferred.

### Export use case

`ExportSyncBundle(BundleReader, Clock)` builds the strict envelope. The reader supplies the consistent database
snapshot; `clock.now()` supplies deterministic/testable `created_at`. The use case is read-only and depends on no
adapter.

### Verbatim bootstrap restore

Add `ports/bootstrap_restore.py::BootstrapRestorer` and
`adapters/sqlite/bootstrap_restore.py::SqliteBootstrapRestorer`. Restore must preserve original ids, timestamps,
audit rows, changelog rows, and provenance without invoking ordinary creation use cases and without minting new
audit/changelog entries. This is the sole exception to the normal `audit_mutation` seam.

A fresh destination already has one local device row and seeded relationship vocabulary. Every imported device is
historical: restore writes all bundled device rows with `retired_at` forced set, preserving their other metadata.
The destination's own device remains the sole active device. If any bundled device id equals the destination's
local active device id, reject the bundle before inserting anything; never retire or overwrite the destination
identity.

Restore opens `BEGIN IMMEDIATE` before target-emptiness checks so no concurrent writer can interleave between
validation and insertion. The complete sequence is:

1. acquire the immediate write reservation;
2. verify the target has no person rows, including soft-deleted rows, and no changelog entries;
3. reject a bundled-device/local-device id collision;
4. reconcile vocabulary, types before synonyms: identical seeded rows skip, differing rows under the same key
   reject the whole bundle, new rows insert;
5. insert all bundled device rows as retired historical devices;
6. insert every portable domain row, including `audit_log`, verbatim;
7. insert changelog rows verbatim;
8. rebuild FTS through `PersonSearchIndexer.rebuild_person_search()`;
9. advance the destination HLC past the validated bundle watermark through `HybridLogicalClock.observe()`;
10. commit.

Any failure rolls the transaction back to the pre-restore freshly initialized state. The optional semantic reindex
stays outside the transaction because it is rebuildable cache data and may require a model download.

### Atomic private-file writer

M11.2 introduces `adapters/filesystem/private_file.py::atomic_write_private_text(path, text)`, and migrates the
existing JSON `_cmd_export` path to it. Later personal-data file outputs reuse the same helper.

The helper:

- creates a unique temporary file in the destination directory with `O_CREAT | O_EXCL` and mode `0o600`;
- explicitly applies `fchmod(..., 0o600)` where supported before publication;
- writes, flushes, and `fsync`s the temporary file;
- atomically replaces the destination with `os.replace`, replacing a destination symlink entry rather than
  following it to truncate an unexpected target;
- cleans up the temporary file after any failure and leaves an existing valid destination unchanged;
- optionally fsyncs the parent directory where supported.

Passing `0o600` only to `os.open(..., O_TRUNC)` is not sufficient because an existing permissive file retains its
old mode. Tests therefore pre-create a `0o644` destination, overwrite it, and assert the final file is `0o600` on
POSIX.

### CLI commands

```text
uv run people-context sync push --output DIR
uv run people-context sync pull --input PATH [--yes]
```

`push` writes `DIR/people-context-sync-bundle.json` through the private-file helper and prints path, entity counts,
changelog count, and watermark. `pull` accepts that file or a directory containing it, parses and validates the
complete document before preview, previews counts, and requires `--yes` or interactive confirmation. A non-empty
target or invalid bundle returns exit code 1 with a structured, actionable message and performs no write.

## Migration needs

None. All restored rows already have schema homes. Relationship-vocabulary reconciliation handles the rows seeded
by the existing migration.

## CLI / MCP surface changes

CLI-only. No MCP tool is added or changed.

## Security and privacy

- The bundle is plaintext and contains high-fidelity personal data, audit data, and replay payloads. Help and
  privacy documentation must say to transport/store it only through an already encrypted medium or encrypted
  filesystem.
- Push/pull remain human-operated CLI actions; no model-callable tool writes or restores a full bundle.
- Forgotten-record redaction travels verbatim. Restore never reconstructs or enriches redacted payloads.
- Strict validation protects integrity and compatibility; it does not authenticate the sender. Authenticity and
  encrypted transport remain future protocol work.
- Empty-target-only restore prevents the blunt verbatim writer from running against independent local history.

## Testing strategy

- Strict-model tests: correct version-1 document; wrong format; unsupported version; missing required field;
  unknown top-level and nested fields; malformed timestamps; duplicate ids/op ids. Every invalid case fails before
  preview/confirmation and leaves the target untouched.
- Cross-field tests: missing origin device, retired origin, dangling changelog device, dangling domain reference,
  watermark below a changelog/device HLC, and bundled/local device-id collision.
- Reader tests: every collection comes from one transaction and has deterministic ordering; unchanged snapshot +
  fake clock produces byte-identical JSON.
- Restore tests: every table round-trips; seeded vocabulary skips; differing vocabulary rejects; all imported
  devices retire; destination identity remains active; HLC advances past all imported history; forced failure at
  every phase, including FTS/HLC finalization, rolls back.
- Concurrency tests: a writer committed before `BEGIN IMMEDIATE` is observed by emptiness checks; a writer started
  after the reservation blocks/times out and never interleaves.
- Private-file tests: new file mode, overwrite of pre-existing `0o644`, destination-symlink replacement without
  modifying the symlink target, cleanup after failure, and preservation of an existing valid file after failed
  replacement.
- CLI tests: push summary, pull preview/confirmation, invalid-bundle refusal before prompt, non-empty refusal,
  owner-only output, and direct-file/directory input forms.
- E2E: A→B restore preserves portable content and custom vocabulary; a later B write uses B's device id and an HLC
  after all imported entries; B→C carries all historical device rows forward.
- `uv run ruff check .` and `uv run pytest -q` fully green.

## Open questions

1. Should a future bundle version carry `PRAGMA user_version` and require exact migration parity, or define a
   compatibility range?
2. Should bundle encryption be designed with the later sync transport rather than as a one-off CLI flag?
3. Should bootstrap bundles remain a permanent format distinct from future incremental batches?
