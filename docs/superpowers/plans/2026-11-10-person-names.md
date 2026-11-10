# Structured Person Names Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan in this session.

**Goal:** Store explicit name components and give browse a consistent preferred-name-first list.

**Architecture:** Extend the existing Person aggregate and audited remember/edit paths. Add migration 011, retain all fields through export/restore and merge, and reuse the current browser field editor.

**Tech Stack:** Python, Pydantic, SQLite, existing browser JavaScript; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-10-person-names-design.md`

## Global Constraints

- Preserve existing canonical names, aliases, and ids; do not guess components.
- Ordinary mutations remain audited; no personal data or network access during checks.
- Empty optional input clears it; omitted input preserves it.
- Sync bundle version 8 is strict; integration JSON additions retain version 1.

## Review Focus

- Existing version-10 databases migrate with every old name intact.
- Duplicate preferred names paginate deterministically; edits invalidate moved cursors.
- Blank clears, omitted fields, Unicode names, and email-shaped input follow explicit rules.
- Export/restore and merges retain components without changing identity matching.
- Browser names remain text nodes, including names containing markup.

## Task 1: Names across persistence and transports

- [x] Add failing domain and real-runtime tests for fallback, validation, edit/clear, sorting, search, migration, and sync round trips.
- [x] Run focused tests and confirm missing-feature failures.
- [x] Add optional fields and migration; extend audited writes, merge, search, index/context projections, staged candidates, sync restore, CLI and MCP.
- [x] Reuse browser editors, display fallback, sorting and canonical disambiguation; preserve structured vCard components.
- [x] Run focused tests, then pytest, Ruff, mypy, build and available browser checks. Review the complete diff and prepare the PR with compatibility/privacy impact and validation.

## Verification and review

Task 1: complete. `uv run --no-sync pytest -q`: 3,804 passed, 16 skipped; Ruff and mypy passed.
Offline isolated `uv build --offline` produced both distributions. The no-build-isolation attempt could not
run because hatchling is not installed in the development environment; the normal isolated build succeeded.
Independent review found one malformed-bundle hydration defect; the fix rejects invalid optional components
without rewriting snapshot values. Nine regression cases failed before the fix and passed after it.
The existing dependency emits one AnyIO/Pytest deprecation warning.
Browser visual QA was denied by the browser approval gate; local HTTP and Node DOM checks passed.

## Task 2: Repository formatting (added at the user's request)

- [x] Run Ruff formatting with line length 120 and sort imports across the repository.
- [x] Verify formatting, lint, types, and the complete suite; commit separately from Task 1.
- Publish the requested pull request against the latest main.

Task 2 verification: Ruff sorted imports and formatted at line length 120 (90 files reformatted).
Ruff lint/format checks and mypy passed. The post-format suite passed: 3,804 tests, 16 skips,
one existing AnyIO/Pytest deprecation warning. Offline source/wheel builds passed. Python ASTs match the
feature commit across all 455 tracked Python files after ignoring docstring edge whitespace.
