# M12 — Trust, stability, and v1.0

Status: Planned. See [docs/roadmap.md](../roadmap.md#m12--trust-stability-and-v10).

## Motivation

`pyproject.toml` still classifies the project `"Development Status :: 3 - Alpha"`, yet the project has already
been behaving with 1.0-grade compatibility discipline for several milestones without saying so out loud: the
README already documents a real backward-compatibility precedent — "the `merge_people` result intentionally
adds the `duplicate_relationships_removed` field... callers that ignore unknown response fields require no
changes" — and every migration to date (`001_initial.sql` through `004_curation_indexes.sql`, applied via
`db.py::_run_migrations`'s forward-only `PRAGMA user_version` mechanism) has been additive. What is missing is
not discipline but a *stated promise* a prospective adopter can rely on before integrating against this server,
plus two concrete trust gaps the source analysis names directly: an at-rest encryption option
(already flagged as a deferred future option in the existing
[threat model notes](../privacy-and-safety.md#threat-model-notes)), and an explicit comparison
against the cloud-hosted memory tools (mem0, Zep, and similar) that a privacy-conscious user is likely
evaluating this project against.

## Scope

In scope:

- a written MCP response-contract and DB-schema compatibility promise;
- a `pyproject.toml` version bump to `1.0.0` and the accompanying release-checklist update;
- opt-in SQLCipher at-rest encryption;
- a threat-model comparison section contrasting local-first storage with cloud memory tools;
- README polish (a short demo funnel, built on M9's `people-context demo`).

Non-goals:

- any behavior change to existing tools, response fields, or CLI commands — this milestone documents and
  hardens what exists; it does not redesign it;
- making encryption the default — SQLCipher stays strictly opt-in, so the plain-SQLite trust story described in
  [docs/decisions/0002-sqlite.md](../decisions/0002-sqlite.md) and
  [docs/cli.md](../cli.md#direct-sqlite-access) remains the default for every existing user and every existing
  test;
- key management UX beyond "read a key from an environment variable" — rotation, OS-keychain integration, and
  multi-key support are explicitly deferred past this milestone;
- solving the deferred sync/multi-user items from [docs/design/sync.md](../design/sync.md) — this milestone's
  compatibility promise applies to what M0–M11 actually ship, not to unbuilt future protocol surfaces.

## Design

### Compatibility promise

A new section — either appended to [docs/mcp-interface.md](../mcp-interface.md) or a new
`docs/compatibility.md` linked from both `README.md`'s docs table and `docs/mcp-interface.md` — states, in the
project's existing precise/contract-oriented voice:

- **MCP response contracts**: within a major version, existing response fields are never removed or repurposed,
  new fields are additive only (the `duplicate_relationships_removed` precedent becomes the *stated* rule, not
  just an example), and existing tool names and required parameters remain stable; optional parameters may be
  added.
- **DB schema**: migrations are forward-only and additive within a major version — no migration drops or
  narrows an existing column read by shipped application code — following the pattern every migration to date
  (`adapters/sqlite/migrations/001_initial.sql` through `004_curation_indexes.sql`) already establishes, and
  applied through the existing `PRAGMA user_version` gate in `db.py::_run_migrations`, which already skips any
  migration numbered at or below the current version.
- **CLI**: existing subcommands and flags keep working; new flags are additive with backward-compatible
  defaults, matching the pattern `reindex --semantic` already set (`reindex` alone behaves exactly as it did
  before the flag existed).
- Explicitly out of scope of the promise (and stated as such, to avoid over-claiming): the Obsidian vault export
  Markdown layout is documented as "deterministic" today
  ([docs/vault-export.md](../vault-export.md)) but not yet declared a contractually stable surface — this
  milestone should decide (see Open Questions) whether to fold it into the promise or explicitly exclude it.

### Version and release checklist

Bump `project.version` in `pyproject.toml` to `1.0.0` and its classifier from `"Development Status :: 3 -
Alpha"` to `"Development Status :: 5 - Production/Stable"`, following the exact release procedure already
documented in [docs/releasing.md](../releasing.md#publish-a-release) (update version → merge → tag → GitHub
Release → approve the `pypi` environment deployment). Add one checklist item specific to the first 1.0 release:
confirm the compatibility-promise doc is published before the tag, since it is the artifact this version number
is meant to back up.

### Opt-in SQLCipher

Add a new optional dependency extra in `pyproject.toml`'s `[project.optional-dependencies]`, alongside the
existing `semantic` extra:

```toml
[project.optional-dependencies]
semantic = ["model2vec>=0.8.2,<0.9", "sqlite-vec>=0.1.9,<0.2"]
encrypted = ["sqlcipher3-binary>=... "]  # exact binding TBD, see Open Questions
```

Add a new adapter entrypoint, `adapters/sqlite/db.py::open_encrypted_db(path, key)`, distinct from — and
additive alongside — the existing `open_db(path)` (`src/people_context/adapters/sqlite/db.py:18-46`). Every
other adapter (`SqlitePeopleRepository`, `SqliteRecordStore`, etc.) already operates on a plain
`sqlite3.Connection` and has no idea whether the underlying file is encrypted, so no adapter beyond `db.py`
changes — the connection object handed to every repository/store constructor is opaque to them, as it already
is today. `open_db`'s existing signature, default behavior, and every one of the roughly three dozen real-SQLite
tests that call `open_db(":memory:")` remain completely unaffected, since encryption is a wholly separate
function, not a new parameter on the existing one.

Wiring: `adapters/mcp/server.py::build_server()` and `cli.py::_open_context()` both gain an explicit
`--encrypted` flag (server) / equivalent CLI flag, which — when set — requires `PEOPLE_CONTEXT_DB_KEY` to be
present in the process environment and calls `open_encrypted_db` instead of `open_db`; the flag's absence
leaves both entrypoints' existing wiring untouched.

### Threat-model comparison

A new subsection appended to the existing "Threat model notes" heading in
[docs/privacy-and-safety.md](../privacy-and-safety.md#threat-model-notes), matching that section's existing
bullet-list style, comparing this project's local-first model (data never leaves the device except the one
explicit, announced model-download path; the server has no account, no hosted database, no telemetry) against
the operating model of cloud-hosted memory/context tools (mem0, Zep, and similar), specifically on: where data
is stored at rest, what a vendor breach or subpoena can expose, whether the tool can function fully offline,
and what "delete my data" means in each model versus this project's hard `forget` semantics
([docs/privacy-and-safety.md](../privacy-and-safety.md#forget-vs-soft-delete)). This is a documentation-only
deliverable.

### README polish

A short "Demo" section near the top of `README.md`, between "Why" and "Quick start," walking through
`people-context demo` (from M9) end to end with either a terminal-recording GIF or a small screenshot sequence,
giving a prospective user something to look at before they read the architecture section.

## Migration needs

None for the compatibility promise, version bump, threat-model doc, or README polish. SQLCipher requires no
new *migration file*, since the existing `001`–`004` migrations already run correctly against an
`sqlcipher`-backed connection (SQLCipher is wire-compatible with the SQLite C API once the key is set); it
requires a new *connection-open path*, not new schema.

## CLI / MCP surface changes

- `people-context-mcp --encrypted` (server): requires `PEOPLE_CONTEXT_DB_KEY`; server refuses to start with a
  clear error if `--encrypted` is set without a key present, rather than silently falling back to plaintext.
- `people-context --encrypted ...` (CLI): same requirement, applied in `_open_context()`.
- No MCP tool changes. No response-shape changes.

## Security / privacy considerations

- The encryption key is sourced **only** from `PEOPLE_CONTEXT_DB_KEY` in the process environment, never from a
  CLI flag value — the same reasoning that already puts `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE` and
  `PEOPLE_CONTEXT_MCP_ENABLE_EXPORT` behind environment variables rather than tool arguments
  (`adapters/mcp/security.py:process_elevation_enabled`) applies doubly here: a CLI flag value is visible in
  shell history and process listings (`ps`), which a database encryption key must never be.
- The key must never be logged, echoed, or included in any error message, audit payload, or changelog payload —
  consistent with the existing `AGENTS.md` rule ("never log private values") and the existing precedent of
  `set_communication_philosophy` deliberately recording only text *length* in the audit log
  ([docs/design/sync.md §2.1](../design/sync.md#21-payloads-are-intentionally-lossy)).
- Because SQLCipher is opt-in, the default install's existing threat model — "the database file is plaintext
  SQLite... rely on OS-level disk encryption"
  ([docs/privacy-and-safety.md](../privacy-and-safety.md#threat-model-notes)) — remains completely accurate and
  unchanged for every user who does not opt in; this milestone must not imply encryption is now the default
  posture anywhere in the docs.
- An encrypted database's `-wal`/`-shm` companion files must also be confirmed encrypted (SQLCipher encrypts
  WAL pages by default in recent versions, but this must be verified against the chosen binding rather than
  assumed) — a plaintext WAL file next to an encrypted main database file would silently leak recent writes.
- The threat-model comparison section must stay factual and sourced, not marketing copy — see Open Questions.

## Testing strategy

- New adapter test module `tests/adapters/test_sqlite_encryption.py`: `open_encrypted_db` requires a key,
  rejects an empty/missing key with a clear error, rejects the wrong key on a previously-encrypted file, and a
  smoke check that a plain `sqlite3.connect()` (unkeyed) cannot read table contents from an encrypted file
  produced by `open_encrypted_db` — this last check is the actual verification that encryption is doing
  anything, not just that the code path runs.
- Every existing adapter test continues to run unmodified against `open_db` — this milestone must not require
  touching any of the roughly three dozen files under `tests/adapters/` that already call `open_db(":memory:")`
  or `open_db(tmp_path / "...")`.
- CLI test for `--encrypted` without `PEOPLE_CONTEXT_DB_KEY` set, asserting a clear refusal rather than a
  silent plaintext fallback or an unhandled exception.
- No new tests are needed for the compatibility-promise doc, version bump, threat-model section, or README
  polish beyond a documentation link-check (every new cross-reference added to `README.md`'s docs table
  resolves to a real file), consistent with this being a docs-only slice of the milestone.

## Open questions

1. Which SQLCipher Python binding should the project standardize on — `sqlcipher3-binary` (prebuilt wheels, but
   narrower platform coverage), `pysqlcipher3` (older, less maintained), or shelling out to a system-installed
   `sqlcipher` CLI/library the user must provide themselves? This determines both the exact `encrypted` extra's
   dependency list and how much platform-coverage risk this milestone takes on.
2. Should the vault export Markdown layout be folded into the compatibility promise (giving external tooling —
   e.g. Obsidian plugins built against it — a stability guarantee) or explicitly excluded as "deterministic but
   not yet a frozen contract"?
3. Should the compatibility promise commit to a specific deprecation-window policy (e.g. "a field is never
   removed before the next major version, announced at least one minor version in advance"), or stay looser
   given the project currently has one maintainer and no other declared consumers to coordinate with?
4. How should the mem0/Zep comparison be kept from going stale as those products' own architectures evolve —
   a dated "as of" note, a narrower comparison scoped to categories of risk rather than product specifics, or a
   periodic-review commitment tied to future releases?
5. Should `1.0.0` also require import-linter or `ruff` banned-import enforcement of the dependency rule
   (currently "enforced by convention and code review today," per
   [docs/architecture.md](../architecture.md#dependency-rule)), given that a 1.0 stability promise is a natural
   point to mechanize a rule that has so far been manual?
