from pathlib import Path


def replace_exact(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one match in {path}, found {count}: {old[:120]!r}")
    file_path.write_text(text.replace(old, new), encoding="utf-8")


PLAN = "docs/specs/pr-plan.md"

replace_exact(
    PLAN,
    '    - `server.json` must use a `packages` entry with `"registryType": "pypi"`, package identifier `people-context`, version, and `"transport": {"type": "stdio"}` — not a raw `command`/`args` invocation.\n',
    '    - `server.json` must carry both the top-level server `version` and a `packages` entry with `"registryType": "pypi"`, package identifier `people-context`, the same package version, and `"transport": {"type": "stdio"}` — not a raw `command`/`args` invocation.\n',
)
replace_exact(
    PLAN,
    '  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. New: CI job step running `mcp-publisher validate server.json` (or documented local-equivalent command if the tool isn\'t sandboxable in CI yet) must pass. No domain/app/adapter code changes, so no new unit tests are required per spec\'s Testing strategy.\n',
    '  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. New: CI runs `mcp-publisher validate server.json` and a small metadata assertion that top-level `server.json.version` equals the PyPI package entry version. No domain/app/adapter code changes, so no new unit tests are required per spec\'s Testing strategy.\n',
)
replace_exact(
    PLAN,
    '    - Keep the MCPB manifest version and its `people-context` dependency synchronized with the release version; fail the build on drift rather than publishing a bundle that installs a different server version.\n',
    '    - Keep MCPB semantic `manifest.json.version` and the bundled `people-context` dependency synchronized with the release version; validate schema-level `manifest_version` separately against the supported MCPB manifest schema.\n',
)
replace_exact(
    PLAN,
    '''- [ ] **PR M9.4 — `people-context init` and packaged `people-context demo`**
  - **Scope:** Add interactive `init` and deterministic `demo [--reset]` CLI compositions. The demo dataset must be usable from an installed wheel: generate it procedurally or ship it as declared package data under `src/people_context/`; production code must never read it from `tests/fixtures`.
  - **Touches:** `cli.py`, `config.py`, `adapters/demo_data.py` or package data under `src/people_context/data/`, CLI tests, packaging smoke coverage, and docs.
  - **Spec:** (docs/specs/m9-cold-start-and-onboarding.md, "`people-context init`" / "`people-context demo`")
    - `init` composes existing import/self/philosophy use cases; no new app port.
    - `demo` ignores the real resolved DB, uses a dedicated path, and refuses reseeding without `--reset`.
    - Fictional seed data is deterministic and present in the built wheel.
  - **Tests/validation:** Test onboarding branches, DB isolation/reset behavior, deterministic seed output, and a clean-environment wheel install followed by `people-context demo --reset`. `uv run ruff check .` and `uv run pytest -q` fully green.
  - **Out of scope:** MCP onboarding and live external integrations.

## M10 — Agent utilization## M10 — Agent utilization
''',
    '''- [ ] **PR M9.4 — `people-context init` and packaged `people-context demo`**
  - **Scope:** Add interactive `init` and deterministic `demo [--reset]` CLI compositions. The demo dataset must be usable from an installed wheel: generate it procedurally or ship it as declared package data under `src/people_context/`; production code must never read it from `tests/fixtures`.
  - **Touches:** `cli.py`, `config.py`, `adapters/demo_data.py` or package data under `src/people_context/data/`, CLI tests, packaging smoke coverage, and docs.
  - **Spec:** (docs/specs/m9-cold-start-and-onboarding.md, "`people-context init`" / "`people-context demo`")
    - `init` composes existing import/self/philosophy use cases; no new app port.
    - `demo` ignores the real resolved DB, uses a dedicated path, and refuses reseeding without `--reset`.
    - Fictional seed data is deterministic and present in the built wheel.
    - On success, print the absolute demo DB path, an installed-package server launch command targeting that path, and copy-pasteable `resolve_person`, `get_relationship_graph`, and `find_connection` tool-call examples using known fictional seed identities.
  - **Tests/validation:** Test onboarding branches, DB isolation/reset behavior, deterministic seed output, and a clean-environment wheel install followed by `people-context demo --reset`. Assert stdout contains the demo path, the path-targeted server launch command, all three tool names, and seeded example identities. `uv run ruff check .` and `uv run pytest -q` fully green.
  - **Out of scope:** MCP onboarding and live external integrations.

## M10 — Agent utilization
''',
)
replace_exact(
    PLAN,
    '''- [ ] **PR M12.2 — Bump to 1.0.0 and synchronize distribution metadata**
  - **Scope:** Bump `project.version` and classifier, update the release checklist, and synchronize all version-bearing M8 artifacts in the same commit.
  - **Touches:** `pyproject.toml`, `server.json`, `mcpb/manifest.json`, `mcpb/pyproject.toml`, `docs/releasing.md`, and a metadata-sync test.
  - **Spec:** (docs/specs/m12-trust-stability-v1.md, "Version and release checklist")
    - Project version, Registry PyPI package version, MCPB semantic `version`, and MCPB `people-context` dependency pin all become `1.0.0` together.
    - MCPB `manifest_version` is a schema version and is not coupled to the application release.
    - CI fails on semantic-version drift, preferably via one canonical check.
  - **Tests/validation:** Parse all four metadata artifacts and assert semantic synchronization while validating `manifest_version` separately. Existing packaging tests remain green.
  - **Out of scope:** cutting the release/tag, SQLCipher, or changing the MCPB schema version without an upstream requirement.
''',
    '''- [ ] **PR M12.2 — Bump to 1.0.0 and synchronize distribution metadata**
  - **Scope:** Bump `project.version` and classifier, update the release checklist, and synchronize every release-version field introduced by M8 in the same commit.
  - **Touches:** `pyproject.toml`, `server.json`, `mcpb/manifest.json`, `mcpb/pyproject.toml`, `docs/releasing.md`, and one canonical metadata-sync test.
  - **Spec:** (docs/specs/m12-trust-stability-v1.md, "Version and release checklist")
    - `pyproject.toml` project version, top-level `server.json.version`, the `people-context` Registry package entry version, MCPB semantic `manifest.json.version`, and the MCPB `people-context` dependency pin all become `1.0.0` together.
    - MCPB `manifest_version` is a schema version and is not coupled to the application release; validate it independently against the MCPB schema supported by the build tooling.
    - CI fails on any semantic-version drift through one canonical parser-based check.
  - **Tests/validation:** Parse all four metadata files, locate the Registry package entry by package identifier rather than array position, assert all five release-version values match, and validate `manifest_version` separately. Existing packaging tests remain green.
  - **Out of scope:** cutting the release/tag, SQLCipher, or changing the MCPB schema version without an upstream requirement.
''',
)
replace_exact(
    PLAN,
    '''  - **Scope:** Add `ListUpcomingDates(ContextReader, ListReminders, PersonReader, Clock)`. `Clock` anchors the date window and `PersonReader` supplies output names; expose through a read-only MCP tool and CLI.
  - **Touches:** app use case, MCP registration, CLI, and app/MCP/CLI/E2E tests.
  - **Spec:** (docs/specs/m13-daily-utility.md, "`upcoming_dates` / CLI report")
    - Use `clock.now()` only; document and test inclusive interval boundaries.
    - Facts come from `ContextReader`, reminders from `ListReminders`, and names from `PersonReader`; skip missing/deleted people deterministically.
    - Only ordinary birthday facts and active dated reminders inside the same window qualify.
  - **Tests/validation:** Fake-clock tests cover today, final included date, and just-outside boundaries, plus recurring dates/leap day, name lookup, missing people, sensitivity, MCP shape, CLI snapshot, and E2E composition. `uv run ruff check .` and `uv run pytest -q` fully green.
''',
    '''  - **Scope:** Add `ListUpcomingDates(PersonContextReader, ListReminders, PersonReader, Clock)`. `Clock` anchors the date window, `PersonContextReader` supplies facts, and `PersonReader` supplies output names; expose through a read-only MCP tool and CLI.
  - **Touches:** app use case, MCP registration, CLI, and app/MCP/CLI/E2E tests.
  - **Spec:** (docs/specs/m13-daily-utility.md, "`upcoming_dates` / CLI report")
    - Use `clock.now()` only and the inclusive interval `[today, today + window_days]`.
    - Birthday values in either `YYYY-MM-DD` or `--MM-DD` form are annual recurrences: project their month/day to the earliest valid occurrence on or after today, rolling into the next year when this year's date has passed. A February 29 birthday occurs only on an actual February 29; never coerce it to February 28 or March 1.
    - Facts come from `PersonContextReader`, reminders from `ListReminders`, and names from `PersonReader`; skip missing/deleted people deterministically. Active dated reminders use their literal due date and the same inclusive window.
    - Only ordinary birthday facts and active dated reminders inside the window qualify; the emitted `date` is the projected birthday occurrence or literal reminder due date.
  - **Tests/validation:** Fake-clock tests cover today, `today + window_days`, just-outside boundaries, year rollover, `YYYY-MM-DD` and `--MM-DD` parity, February 29 in leap and non-leap years, name lookup, missing people, sensitivity, MCP shape, CLI snapshot, and E2E composition. `uv run ruff check .` and `uv run pytest -q` fully green.
''',
)
replace_exact(
    PLAN,
    '''- [ ] **PR M14.2 — `export-vcard` deterministic writer**
  - **Scope:** New `adapters/filesystem/vcard_writer.py` mirroring `vault_writer.py`'s determinism rules, plus the `people-context export-vcard` CLI command. Inverts the existing vCard importer's field mapping. Does not add any new import source.
  - **Touches:**
    - `src/people_context/adapters/filesystem/vcard_writer.py` (new)
    - `src/people_context/app/export_vcard.py` (new, thin use case parallel to `app/export_vault.py`)
    - `src/people_context/cli.py` (`export-vcard` subcommand)
    - `tests/adapters/test_vcard_export.py` (new)
  - **Spec:** (docs/specs/m14-ecosystem-interop.md §"`people-context export-vcard`")
    - Stable person/property ordering; byte-identical re-export over unchanged data (same guarantee as `docs/vault-export.md`).
    - Field mapping: `FN`/`N` ← canonical name, `NICKNAME` ← `nickname` aliases, `EMAIL` ← `handle` aliases parsing as addresses, `ORG`/`TITLE` ← active affiliations, `BDAY` ← `predicate="birthday"` facts (`AliasKind` in `domain/person.py`).
    - `--version {3.0,4.0}` dialect flag; everything emitted must round-trip through the existing `VCardImportExtractor` — enforced by a round-trip test, not just eyeballed.
    - Elevated-sensitivity facts follow the same `--include-sensitive` gate as vault export and `brief`.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. `test_vcard_export.py`: determinism (byte-identical re-export), sensitivity gating, and a full round trip through `VCardImportExtractor` asserting people/aliases/affiliations/birthday facts survive. CLI `0o600` and determinism checks.
  - **Out of scope:** CardDAV (explicit non-goal); Outlook/WhatsApp import; any change to the importer side of vCard.
''',
    '''- [ ] **PR M14.2 — `export-vcard` deterministic writer**
  - **Scope:** New `adapters/filesystem/vcard_writer.py` mirroring `vault_writer.py`'s determinism rules, plus the `people-context export-vcard` CLI command. It emits the subset the existing vCard importer can round-trip without changing that importer.
  - **Touches:**
    - `src/people_context/adapters/filesystem/vcard_writer.py` (new)
    - `src/people_context/app/export_vcard.py` (new, thin use case parallel to `app/export_vault.py`)
    - `src/people_context/cli.py` (`export-vcard` subcommand)
    - `tests/adapters/test_vcard_export.py` (new)
  - **Spec:** (docs/specs/m14-ecosystem-interop.md §"`people-context export-vcard`")
    - Stable person/property ordering; byte-identical re-export over unchanged data (same guarantee as `docs/vault-export.md`).
    - Field mapping: `FN`/`N` ← canonical name, `NICKNAME` ← `nickname` aliases, `EMAIL` ← `handle` aliases parsing as addresses, `BDAY` ← `predicate="birthday"` facts.
    - Because the existing importer consumes only the first `ORG`/`TITLE` pair, export at most one active affiliation per person, selected deterministically by normalized organization name, normalized role, then affiliation id. Report additional active affiliations as `omitted_affiliations`; do not silently imply complete affiliation portability.
    - `--version {3.0,4.0}` dialect flag; every emitted field must round-trip through the existing `VCardImportExtractor`.
    - Elevated-sensitivity facts follow the same `--include-sensitive` gate as vault export and `brief`.
  - **Tests/validation:** `uv run ruff check .` clean, `uv run pytest -q` fully green. `test_vcard_export.py`: determinism, sensitivity gating, deterministic affiliation selection and omission count, and a full round trip asserting people/aliases/the selected affiliation/birthday facts survive. CLI `0o600` and determinism checks.
  - **Out of scope:** CardDAV; Outlook/WhatsApp import; multiple-affiliation vCard encoding; any change to the importer side of vCard.
''',
)
replace_exact(
    PLAN,
    '    - `src/people_context/adapters/sqlite/migrations/005_doctor_indexes.sql` (only if a finding query needs an additive index beyond the existing `004_curation_indexes.sql` ones — check before adding)\n',
    '    - `src/people_context/adapters/sqlite/migrations/<next-free>_doctor_indexes.sql` (only if `EXPLAIN QUERY PLAN` proves a finding query needs an additive index; determine the number at implementation time because earlier milestones may already consume `005`)\n',
)

replace_exact(
    "docs/specs/m8-distribution-and-reach.md",
    '''Add `server.json` at the repository root following the official MCP Registry server schema: a PyPI-distributed
server is represented by a `packages` entry with `"registryType": "pypi"`, the package identifier
`people-context` and its version, and `"transport": {"type": "stdio"}` — not by a raw `command`/`args`
invocation, which is not how the Registry models package-based distribution. The Registry verifies PyPI
''',
    '''Add `server.json` at the repository root following the official MCP Registry server schema. It carries a
top-level server `version` plus a `packages` entry with `"registryType": "pypi"`, package identifier
`people-context`, the matching package version, and `"transport": {"type": "stdio"}` — not a raw
`command`/`args` invocation. The two release-version fields start equal and are kept synchronized. The Registry verifies PyPI
''',
)
replace_exact(
    "docs/specs/m8-distribution-and-reach.md",
    '''The bundled `pyproject.toml` depends on the same `people-context` version as the release that carries the
artifact. The build fails if the project version, manifest version, and dependency pin drift. Packaging uses the
official MCPB CLI (`mcpb pack`, including its manifest validation) through a new `scripts/build-mcpb.*` step,
''',
    '''The bundled `pyproject.toml` depends on the same `people-context` version as the release that carries the
artifact. The build fails if the project version, MCPB semantic `manifest.json.version`, and dependency pin drift;
MCPB `manifest_version` is validated separately as a schema-version field. Packaging uses the official MCPB CLI
(`mcpb pack`, including its manifest validation) through a new `scripts/build-mcpb.*` step,
''',
)

replace_exact(
    "docs/specs/m12-trust-stability-v1.md",
    '''Bump the project and classifier to `1.0.0`/Production-Stable. In the same commit, synchronize the Registry PyPI
package version in `server.json`, MCPB semantic `version`, and the `people-context` dependency pin in the bundled
MCPB `pyproject.toml`. MCPB `manifest_version` is a schema-version field and remains independent. CI parses all
artifacts and fails on semantic-version drift. Follow the existing release procedure and add the compatibility-doc
checklist item; do not cut the tag in this PR.
''',
    '''Bump the project and classifier to `1.0.0`/Production-Stable. In the same commit, synchronize all release-version
fields introduced by M8: `pyproject.toml` project version, top-level `server.json.version`, the `people-context`
Registry package entry version, MCPB semantic `manifest.json.version`, and the `people-context` dependency pin in
the bundled MCPB `pyproject.toml`. MCPB `manifest_version` is a schema-version field and remains independent. One
parser-based CI check locates the Registry package by identifier, asserts all five release-version values match, and
validates `manifest_version` separately. Follow the existing release procedure and add the compatibility-doc
checklist item; do not cut the tag in this PR.
''',
)

replace_exact(
    "docs/specs/m9-cold-start-and-onboarding.md",
    '''The command always uses a dedicated demo database and refuses reseeding without `--reset`. Its deterministic
fictional dataset is runtime product data, so it must ship in installed artifacts: implement it procedurally under
`src/people_context/` or declare package data there. Production code must not read from `tests/fixtures`, which is
not included by the current wheel configuration. Acceptance includes building and installing the wheel in a clean
environment and successfully running `people-context demo --reset`.
''',
    '''The command always uses a dedicated demo database and refuses reseeding without `--reset`. Its deterministic
fictional dataset is runtime product data, so it must ship in installed artifacts: implement it procedurally under
`src/people_context/` or declare package data there. Production code must not read from `tests/fixtures`, which is
not included by the current wheel configuration. On success it prints the absolute demo database path, an
installed-package `people-context-mcp` launch command explicitly targeting that path, and copy-pasteable
`resolve_person`, `get_relationship_graph`, and `find_connection` tool-call examples using known fictional seed
identities. Acceptance builds and installs the wheel in a clean environment, runs `people-context demo --reset`,
and asserts that all of those path-targeted examples are present in stdout.
''',
)

replace_exact(
    "docs/specs/m13-daily-utility.md",
    '''`ListUpcomingDates` depends on `ContextReader`, `ListReminders`, `PersonReader`, and an injected `Clock`. The clock
anchors a documented inclusive date interval; person reads supply names and allow missing/soft-deleted people to
be skipped deterministically. Facts must pass ordinary sensitivity, use predicate `birthday`, and parse as ISO or
`--MM-DD`; active reminders qualify when `due_at` lies inside the same window. Tests pin both boundaries, recurring
dates, leap day, and name lookup. Output remains `{person_id, name, kind, date, label}` plus
`skipped_unparseable`.
''',
    '''`ListUpcomingDates` depends on `PersonContextReader`, `ListReminders`, `PersonReader`, and an injected `Clock`.
The inclusive interval is `[clock.now().date(), clock.now().date() + window_days]`. Person reads supply names and
allow missing/soft-deleted people to be skipped deterministically. Ordinary-sensitivity facts with
`predicate="birthday"` accept `YYYY-MM-DD` and `--MM-DD`; both forms are annual recurrences whose month/day is
projected to the earliest valid occurrence on or after today, rolling into the next year after this year's date.
February 29 is never coerced to February 28 or March 1: its next occurrence is the next actual leap-day date.
Active dated reminders use their literal `due_at` date and the same inclusive interval. Tests pin both boundaries,
year rollover, both birthday formats, leap-day behavior, and name lookup. Output remains
`{person_id, name, kind, date, label}` plus `skipped_unparseable`, with `date` set to the projected birthday
occurrence or literal reminder due date.
''',
)

replace_exact(
    "docs/specs/m14-ecosystem-interop.md",
    '''New filesystem adapter `adapters/filesystem/vcard_writer.py` mirroring the vault writer's determinism rules:
stable person ordering, stable property ordering, byte-identical re-export over unchanged data. Field mapping
inverts the existing importer: `FN`/`N` from the person name, `NICKNAME` from `nickname` aliases, `EMAIL`
from `handle` aliases that parse as addresses, `ORG`/`TITLE` from active affiliations, `BDAY` from
`predicate="birthday"` facts (`AliasKind` values in `domain/person.py`; affiliation/fact shapes per
[docs/data-model.md](../data-model.md)). Elevated-sensitivity facts follow the same `--include-sensitive`
gate as vault export. One `--version {3.0,4.0}` flag selects the dialect (default per Open Questions);
everything emitted must round-trip through the project's own vCard importer, and a round-trip test enforces
it.
''',
    '''New filesystem adapter `adapters/filesystem/vcard_writer.py` mirroring the vault writer's determinism rules:
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
''',
)

# Guard against the exact regressions this repair addresses.
plan = Path(PLAN).read_text(encoding="utf-8")
for forbidden in (
    "## M10 — Agent utilization## M10",
    "ListUpcomingDates(ContextReader",
    "migrations/005_doctor_indexes.sql",
    "Keep the MCPB manifest version",
):
    if forbidden in plan:
        raise RuntimeError(f"stale planning text remains: {forbidden}")

for required in (
    "top-level `server.json.version`",
    "PersonContextReader",
    "omitted_affiliations",
    "<next-free>_doctor_indexes.sql",
    "get_relationship_graph",
):
    if required not in plan:
        raise RuntimeError(f"required planning contract missing: {required}")
