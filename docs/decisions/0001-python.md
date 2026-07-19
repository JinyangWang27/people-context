# 0001. Language and tooling: Python, the official `mcp` SDK, and `uv`

## Status

Accepted.

## Context

`people-context` needs a language and toolchain for a local MCP server plus a companion CLI. The main
candidates were Python (with the official `mcp` SDK's `FastMCP`), TypeScript (which has first-class support
in the broader MCP ecosystem and was the initially proposed choice), and a systems language such as Go or
Rust. The project also needs a build/dependency toolchain and a way for MCP clients to launch it.

## Decision

Use **Python ≥ 3.11**, with the official `mcp` Python SDK (`FastMCP`) for the server, `pydantic` (v2) for
domain models and tool I/O schemas, `python-ulid` for ID generation, the standard library `sqlite3` module
for persistence (no external database driver dependency), `pytest` for tests, and `ruff` for linting
(`line-length = 120`). The project is packaged and run with `uv` (`pyproject.toml`, `hatchling` build
backend); clients launch it via `uv run people-context-mcp` (or `uvx --from people-context people-context-mcp` from PyPI).

## Consequences

- Dependencies stay minimal: `mcp`, `pydantic`, `python-ulid` at runtime; `pytest`, `ruff` for development.
  No database driver, no web framework, no transliteration/fuzzy-matching library is required for the
  designed v1 feature set.
- `sqlite3` in the standard library means the persistence layer has no third-party dependency to track for
  security or compatibility — see [0002-sqlite.md](0002-sqlite.md).
- Pydantic v2 models double as both domain value objects and MCP tool I/O schemas, since the `mcp` SDK
  already depends on Pydantic — no separate schema library is needed.
- `uv` gives fast, reproducible installs and a single `pyproject.toml` source of truth for both the server
  script (`people-context-mcp`) and the CLI script (`people-context`).
- Python's dynamic typing means the project leans on `mypy`/`ruff`-style static checks and full type hints
  (enforced by convention, `from __future__ import annotations` where useful) rather than a compiler to
  catch type errors — a deliberate trade-off against Go/Rust's stronger static guarantees, in exchange for
  faster iteration and easier integration with the Python-first `mcp` SDK.

## Alternatives considered

- **TypeScript** — initially proposed, given the strong first-class support for MCP in the TypeScript SDK
  and ecosystem tooling. The user explicitly chose Python instead for this project, so this was not pursued
  further.
- **Go or Rust** — would give stronger static typing, a single compiled binary, and no runtime dependency
  installation step for end users. Rejected for v1 because it would slow down iteration on the domain model
  and MCP tool surface, and because the official `mcp` SDK's Python support (via `FastMCP`) is the most
  mature path to a working server quickly. This can be revisited if distribution as a single binary becomes
  a priority.
