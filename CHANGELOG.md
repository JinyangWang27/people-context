# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-07-23

### Added

- Codex plugin packaging with local marketplace metadata and validation.
- MCP Registry and community-directory metadata with reproducible `uvx` launch configuration.
- Native-UV MCPB bundle and setup guides for supported desktop clients and editors.
- Optional non-root Docker image and tag-triggered GitHub Container Registry publishing.
- ICS calendar attendee imports through the staged review and commit workflow.
- LinkedIn Connections CSV imports with preamble-aware, offline parsing.
- `people-context init` for safely importing supported contact files into a reviewed staged batch.
- `people-context demo [--reset]` for exploring an isolated database with deterministic sample data.
- A packaged usage skill that teaches agents privacy-aware People Context tool composition.
- User-invocable Claude Code workflows for `/people-context:who`, `/people-context:remember`, and
  `/people-context:reminders`.

### Changed

- Made the zero-clone `uvx` installation path the primary quick-start flow.
- Moved import extractor routing into its own adapter boundary without changing import behavior.
- Reorganized application, adapter, CLI, and persistence code by capability around a shared runtime composition root,
  with automated architecture-boundary enforcement.
- Bumped the Claude Code and OpenClaw plugins to `0.2.0` and the Registry compatibility package to `0.1.0.post2`.
- Updated development, runtime, and GitHub Actions dependencies.

### Security

- Added CodeQL analysis and strengthened dependency and workflow pinning.
- Patched vulnerable OpenClaw dependencies and replaced trailing-slash regex processing with a linear scan.

## [0.1.1] - 2026-07-19

### Changed

- Published the first release under the renamed `people-context` distribution.

## [0.1.0] - 2026-07-18

### Added

- Initial local-first People Context MCP server release.

[0.2.0]: https://github.com/JinyangWang27/people-context/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/JinyangWang27/people-context/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/JinyangWang27/people-context/releases/tag/v0.1.0
