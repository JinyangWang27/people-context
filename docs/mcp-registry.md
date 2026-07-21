# MCP Registry and community-directory metadata

This document records how `people-context` presents itself to the official MCP Registry and to the community
directories, which files in this repository carry that metadata, and which steps remain manual account-owner
actions. It delivers checklist item **M8.2** of [docs/specs/pr-plan.md](specs/pr-plan.md); see
[docs/specs/m8-distribution-and-reach.md](specs/m8-distribution-and-reach.md) for the binding milestone
specification.

The canonical tool inventory and response contracts live in [docs/mcp-interface.md](mcp-interface.md). Directory
listings reuse those descriptions; this document does not restate them.

## Namespace decision

The durable public identity is the reverse-DNS namespace:

```
io.github.jinyangwang27/people-context
```

This is a deliberate, recorded choice because the Registry namespace becomes permanent public identity. The
`io.github.*` namespace is the GitHub-hosted-project namespace; ownership of `io.github.jinyangwang27/*` is proven
by GitHub authentication of the `JinyangWang27` account at publication time and by the ownership marker committed to
the packaged README:

```
<!-- mcp-name: io.github.jinyangwang27/people-context -->
```

The marker is in the repository-root [README.md](../README.md), which is the file packaged as the PyPI project's
long description, so the ownership proof ships inside the published distribution.

## Registry metadata file

[`server.json`](../server.json) at the repository root follows the pinned official Registry schema
(`https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`). It declares:

- the server `name` `io.github.jinyangwang27/people-context` and a top-level `version` that tracks the application
  release;
- a single `packages` entry for the PyPI identifier `people-context` at the same version;
- a `stdio` package transport (not an arbitrary command/args description);
- `runtimeHint: "uvx"` with `runtimeArguments`/`packageArguments` that reconstruct the canonical invocation
  `uvx --from people-context people-context-mcp`, because the MCP server console script differs from the
  distribution name.

The server `version` and the package `version` must equal the `project.version` in
[pyproject.toml](../pyproject.toml). This equality and the presence of the README ownership marker are asserted both
in CI (`.github/workflows/mcp-registry-validate.yml`) and in `tests/test_registry_metadata.py`.

## Pinned validator

Validation uses the Registry's own `mcp-publisher` CLI, pinned to an exact reviewed release
(`v1.8.0`). The `.github/workflows/mcp-registry-validate.yml` workflow downloads that immutable release, verifies the
binary against the release's own signed `checksums.txt`, and runs `mcp-publisher validate server.json`. No floating
`latest` CLI is installed. Bumping the validator is a reviewed change to `MCP_PUBLISHER_VERSION` in that workflow.

## Publication (manual, account-owner step)

Actual Registry publication is a manual release step that requires interactive GitHub authentication and therefore
is **out of scope for automation in this PR** (M8.2 excludes live publication/approval). To publish a release:

```bash
# Pinned to the reviewed release; do not use a floating tag.
mcp-publisher login github
mcp-publisher validate server.json
mcp-publisher publish
```

`mcp-publisher login github` performs the GitHub OAuth device flow that proves control of the
`io.github.jinyangwang27` namespace. `publish` is only run by the account owner after a version bump has merged.

## Community-directory submission matrix

Verified against each directory's primary submission documentation. Entries marked *manual* require an
account-owner action (form submission or authenticated claim) that cannot be completed from repository metadata
alone; entries marked *repository* are driven by files committed here.

| Directory | Primary documentation | Submission path | Required in-repo metadata | Package / transport representation | Ownership / auth step | Live publication |
|---|---|---|---|---|---|---|
| **MCP Registry** | https://github.com/modelcontextprotocol/registry (`docs/`) | Repository metadata + `mcp-publisher` | [`server.json`](../server.json) (schema `2025-12-11`) and README `mcp-name:` marker | PyPI `people-context`, `stdio` transport, `uvx` runtime hint | GitHub OAuth via `mcp-publisher login github` proves `io.github.jinyangwang27` | Manual `mcp-publisher publish` at release time |
| **Smithery** | https://smithery.ai/docs | Manual (authenticated GitHub claim; Smithery indexes the repo/README) | None required for a local `stdio` server; the canonical invocation and description come from README/`server.json` | Documented as local `uvx` stdio; Smithery's hosted-deployment model does not apply to this local-first server | Claim the server in the Smithery dashboard using the `JinyangWang27` GitHub account | Manual claim/listing by the account owner |
| **PulseMCP** | https://www.pulsemcp.com/submit | Manual (submission form / crawler) | None; PulseMCP ingests GitHub and Registry metadata | Reuses the Registry `server.json` package/transport once published | Submit the GitHub URL from the `JinyangWang27` account | Manual form submission by the account owner |
| **mcp.so** | https://mcp.so/submit | Manual (submission form; consumes Registry) | None; mcp.so consumes the published Registry entry and README | Reuses the Registry `server.json` package/transport once published | Submit the GitHub/Registry URL | Manual form submission by the account owner |
| **Glama** | https://glama.ai/mcp/servers | Repository metadata (auto-indexed) + optional claim | [`glama.json`](../glama.json) (schema `https://glama.ai/mcp/schemas/server.json`) declaring `maintainers` | Auto-indexed from the public GitHub repository and README | Claim the server in Glama using the `JinyangWang27` GitHub account | Auto-indexed; maintainer claim is manual |

### Notes

- No directory listing introduces analytics, telemetry, or a divergent tool inventory. Descriptions are reused
  from [docs/mcp-interface.md](mcp-interface.md) and the packaged project description.
- Where a directory publishes an official validator, it is pinned: the Registry `mcp-publisher` is pinned in CI.
  Smithery, PulseMCP, and mcp.so provide no repository-side validator for a local `stdio` server, so their rows are
  validated by documentation review and link checks rather than a pinned CLI. `glama.json` is a small static file
  validated as well-formed metadata in `tests/test_registry_metadata.py`.
- Live publication and per-directory approval remain manual account-owner steps and are intentionally not automated
  here.
