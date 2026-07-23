# Repository Guidelines

## Project Structure & Module Organization

Application code lives under `src/people_context/` and follows a hexagonal architecture:

- `domain/` contains Pydantic entities and dependency-free business rules.
- `app/` groups focused use cases by capability and depends only on `domain/` and narrow protocols in `ports/`.
- `adapters/` contains SQLite persistence, MCP transports/tools, importers, and optional semantic integrations.
- `adapters/runtime.py` is the shared composition root; `cli/`, `config.py`, and `adapters/mcp/server.py` are
  process-boundary entry points.

Tests mirror these layers and capabilities under `tests/domain/`, `tests/app/`, `tests/adapters/`, and
`tests/cli/`. Design documentation, interface contracts, privacy rules, and ADRs live in `docs/`.

## Build, Test, and Development Commands

- `uv sync` installs the project and development dependencies.
- `uv sync --extra semantic` explicitly installs optional Model2Vec and sqlite-vec support.
- `uv run people-context-mcp` starts the default stdio MCP server.
- `uv run people-context-mcp --http --host 127.0.0.1 --port 8765` starts loopback HTTP.
- `uv run pctx db-path` shows the active SQLite database.
- `uv run pytest -q` runs the complete test suite.
- `uv run ruff check .` checks formatting-independent style and imports.
- `uv build` creates source and wheel distributions.

## Coding Style & Naming Conventions

Use four-space indentation, complete type hints, and a 120-character line limit. Ruff enforces `E`, `F`, `I`, `UP`, `B`, and `SIM` rules. Name modules and functions with `snake_case`, classes with `PascalCase`, and constants with `UPPER_CASE`. Keep functions focused and prefer explicit dependency injection through `typing.Protocol` ports. Never import `adapters`, `mcp`, or `sqlite3` from `domain/` or `app/`.

## Testing Guidelines

Use pytest and name files `test_<subject>.py` and tests `test_<behavior>()`. Test application policy against in-memory fakes in `tests/app/fakes.py`; test persistence and transport behavior separately with SQLite and subprocess/E2E tests. Add a regression test for every bug and run focused tests before the full suite.

## Code Review Guidelines

Review for material defects, not for a minimum number of comments. A clean review is valid.

Review against the linked issue, stated pull request scope, repository contracts, security invariants, and official API documentation. Report only concrete defects with a realistic path through supported behavior and meaningful user, security, data, or operational impact. Do not report style preferences, speculative edge cases, optional hardening, unrelated refactors, or clearly documented unsupported behavior. Do not treat an officially supported but non-canonical API form as a defect.

Use P2 only for a reproducible user-visible failure or broken supported workflow. Omit P3 and nit-level findings. On the first review, inspect related and symmetric code paths together and consolidate findings by root cause. On later reviews, verify prior fixes and review the new delta; do not repeatedly mine unchanged code for additional marginal findings. Do not repeat or rephrase an existing finding unless the latest change failed to address it.

When official documentation is ambiguous, state the uncertainty rather than asserting a defect. If all material findings are resolved and the latest changes introduce no regression, submit a clean review.

## Commit & Pull Request Guidelines

Use concise imperative Conventional Commit subjects, matching history: `feat: add ...`, `fix: report ...`, or `docs: mark ...`. Keep commits green and narrowly scoped. Pull requests should explain behavior and privacy impact, list verification commands, link relevant issues, and update interface or architecture documentation when contracts change.

## Security & Configuration

This repository stores sensitive personal data locally. Never persist raw import content or log private values. Keep HTTP loopback-only and unauthenticated; remote access is out of scope. Ordinary commands must not access the network—only explicit `people-context reindex --semantic` may download the pinned model.
