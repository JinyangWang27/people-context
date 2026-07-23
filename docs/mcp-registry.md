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
  release (`project.version` in [pyproject.toml](../pyproject.toml));
- a single PyPI `packages` entry;
- a `stdio` package transport (not an arbitrary command/args description).

### Package-aligned entrypoint

A client assembles the run command as `<runtimeHint> <runtimeArguments> <identifier> <packageArguments>` — the
package `identifier` is inserted as the executed command between the runtime arguments and the package arguments.
The primary distribution exposes an MCP server console script with the same `people-context` name so Registry
ownership verification and client execution resolve through one PyPI project. The human CLI uses the concise
`pctx` command, while `people-context-mcp` remains an equivalent server alias for existing configurations:

- `identifier`: `people-context` — both the PyPI distribution and the console-script command `uvx` executes;
- `runtimeHint`: `uvx`;
- `runtimeArguments`: `--from people-context==<server version>` — installs the active primary distribution pinned
  to the server version.

This reconstructs the documented invocation, pinned for reproducibility:

<!-- x-release-please-start-version -->
```
uvx --from people-context==0.3.0 people-context
```
<!-- x-release-please-end -->

The command identifier must not also be repeated in `packageArguments`.

**Reproducible version selection.** A Registry entry is a versioned snapshot, so the primary requirement is pinned
with `==<server version>` rather than left to resolve to the latest release. This keeps a client that selects the
historical Registry entry on its matching `people-context` release and contracts, honouring the same-version
package contract in [docs/specs/m8-distribution-and-reach.md](specs/m8-distribution-and-reach.md) and the
synchronization assumptions in [docs/specs/m12-trust-stability-v1.md](specs/m12-trust-stability-v1.md). The pinned
`--from` value, the reconstructed command, the single stdio PyPI entry, and the ownership marker are asserted — and
kept in lockstep with the server version — both in CI
(`.github/workflows/mcp-registry-validate.yml`) and in `tests/test_registry_metadata.py`. Release Please updates
this documented command, the top-level `version`, and the `==` package pin in the same release PR.

Because the Registry links a PyPI package to the server through a `mcp-name:` marker in that package's published
README, the marker is committed to the repository-root [README.md](../README.md), which is the `people-context`
distribution's long description.

## Pinned validator

Validation uses the Registry's own `mcp-publisher` CLI, pinned to an exact reviewed release (`v1.8.0`). Because
release tags are mutable, the `.github/workflows/mcp-registry-validate.yml` workflow verifies the downloaded archive
against a repository-pinned immutable content digest (`MCP_PUBLISHER_SHA256`) — not the release's own checksums
file, which an asset swap could rewrite in lockstep — before executing the binary and running
`mcp-publisher validate server.json`. No floating `latest` CLI is installed. Bumping the validator is a reviewed
change to both `MCP_PUBLISHER_VERSION` and `MCP_PUBLISHER_SHA256` in that workflow.

## Publication (manual, account-owner step)

Actual Registry publication requires interactive GitHub authentication and remains a manual account-owner action.
Release Please automates the synchronized version changes and the GitHub/PyPI release, but it does not perform the
Registry OAuth device flow.

**Prerequisite — publish the matching `people-context` artifact first.** The Registry validates the package named by
`identifier` by fetching its published PyPI README and confirming the `mcp-name:` marker. Publication therefore
uses this order:

1. Merge the generated Release Please PR after verifying that `pyproject.toml`, `server.json`, the documented
   invocation above, and the other release metadata all carry the same version.
2. Wait for Release Please to create the matching GitHub Release and for `release.yml` to publish the matching
   `people-context` artifact to PyPI.
3. Install the pinned publisher, verified against a reviewed immutable digest, and invoke that binary explicitly
   (it is not otherwise on `PATH`). Select the `archive`/`MCP_PUBLISHER_SHA256` pair for your platform from the
   reviewed `v1.8.0` digests below — each is the archive's own content hash, so verification does not depend on the
   release's own checksums file:

   | Platform | `archive` | `MCP_PUBLISHER_SHA256` |
   |---|---|---|
   | Linux x86-64 | `mcp-publisher_linux_amd64.tar.gz` | `1370446bbe74d562608e8005a6ccce02d146a661fbd78674e11cc70b9618d6cf` |
   | Linux arm64 | `mcp-publisher_linux_arm64.tar.gz` | `c978982c60e1b4903a976de090f04dc4fac4a320daa50704fcad2dbc93433d62` |
   | macOS x86-64 | `mcp-publisher_darwin_amd64.tar.gz` | `5350f756e8408d0e22802b7f384af941448358b503eb1e1772979a61b9b99fde` |
   | macOS arm64 | `mcp-publisher_darwin_arm64.tar.gz` | `e74f8846c3b5d0428cfeae3f9f520bbf9031d18e68224108c3760d60b6aaf2e0` |

   The Linux x86-64 row is the digest CI pins; keep it in sync with
   `.github/workflows/mcp-registry-validate.yml`.

   ```bash
   MCP_PUBLISHER_VERSION="v1.8.0"
   archive="mcp-publisher_linux_amd64.tar.gz"                                           # your platform's row above
   MCP_PUBLISHER_SHA256="1370446bbe74d562608e8005a6ccce02d146a661fbd78674e11cc70b9618d6cf"  # its matching digest
   curl -fLsS -o "$archive" \
     "https://github.com/modelcontextprotocol/registry/releases/download/${MCP_PUBLISHER_VERSION}/${archive}"
   # Portable digest check: GNU coreutils ships sha256sum, macOS ships shasum.
   actual="$( { sha256sum "$archive" 2>/dev/null || shasum -a 256 "$archive"; } | awk '{print $1}' )"
   [ "$actual" = "$MCP_PUBLISHER_SHA256" ] || { echo "digest mismatch for $archive" >&2; exit 1; }
   tar -xzf "$archive" mcp-publisher
   ```

4. Only then run the Registry publication with that pinned binary:

   ```bash
   ./mcp-publisher login github
   ./mcp-publisher validate server.json
   ./mcp-publisher publish
   ```

`mcp-publisher login github` performs the GitHub OAuth device flow that proves control of the
`io.github.jinyangwang27` namespace. `publish` is run by the account owner only after the marker-bearing PyPI
artifact is live.

## Community-directory submission matrix

Verified against each directory's primary submission documentation. Entries marked *manual* require an
account-owner action (form submission or authenticated claim) that cannot be completed from repository metadata
alone; entries marked *repository* are driven by files committed here.

| Directory | Primary documentation | Submission path | Required in-repo metadata | Package / transport representation | Ownership / auth step | Live publication |
|---|---|---|---|---|---|---|
| **MCP Registry** | https://github.com/modelcontextprotocol/registry (`docs/`) | Repository metadata + `mcp-publisher` | [`server.json`](../server.json) (schema `2025-12-11`) and the `mcp-name:` marker in the packaged README | PyPI `stdio` entry; `uvx --from people-context==<server version> people-context` | GitHub OAuth via `mcp-publisher login github` proves `io.github.jinyangwang27` | Manual `mcp-publisher publish` after the marker-bearing PyPI artifact is live |
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
