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

### Alternate-entry-point representation

A client assembles the run command as `<runtimeHint> <runtimeArguments> <identifier> <packageArguments>` — the
package `identifier` is inserted as the executed command between the runtime arguments and the package arguments.
The MCP server console script (`people-context-mcp`) differs from the primary distribution name (`people-context`),
whose own `people-context` console script is the human CLI, not the server. So the entry uses:

- `identifier`: `people-context-mcp` — the console-script command `uvx` executes;
- `runtimeHint`: `uvx`;
- `runtimeArguments`: `--from people-context==<server version>` — installs the active primary distribution pinned
  to the server version, whose `people-context-mcp` script is the server.

This reconstructs the documented invocation, pinned for reproducibility:

```
uvx --from people-context==0.1.1 people-context-mcp
```

The command identifier must not also be repeated in `packageArguments`; doing so would assemble
`uvx --from people-context people-context people-context-mcp`, which runs the `people-context` CLI with a stray
argument instead of the server.

**Reproducible version selection.** A Registry entry is a versioned snapshot, so the primary requirement is pinned
with `==<server version>` rather than left to resolve to the latest release. This keeps a client that selects the
historical `0.1.1` Registry entry on `people-context` `0.1.1` code and contracts, honouring the same-version
package contract in [docs/specs/m8-distribution-and-reach.md](specs/m8-distribution-and-reach.md) and the
synchronization assumptions in [docs/specs/m12-trust-stability-v1.md](specs/m12-trust-stability-v1.md). Because the
console-script name differs from the distribution name, the same-version guarantee is expressed through this pinned
`--from` primary (whose installed version equals the server version) rather than through a `version` on the command
`identifier` — pinning `people-context-mcp==<server version>` would not resolve, since the compatibility
distribution carries an independent version line. The pinned `--from` value, the reconstructed command, the single
stdio PyPI entry, and the ownership markers are asserted — and kept in lockstep with the server version — both in CI
(`.github/workflows/mcp-registry-validate.yml`) and in `tests/test_registry_metadata.py`. **Every server version
bump must update this `==` pin together with the top-level `version`.**

Because the Registry links a PyPI package to the server through a `mcp-name:` marker in that package's published
README, the marker is committed to both the repository-root [README.md](../README.md) (the `people-context`
long description) and the identifier package's
[compat/people-context-mcp/README.md](../compat/people-context-mcp/README.md).

## Pinned validator

Validation uses the Registry's own `mcp-publisher` CLI, pinned to an exact reviewed release (`v1.8.0`). Because
release tags are mutable, the `.github/workflows/mcp-registry-validate.yml` workflow verifies the downloaded archive
against a repository-pinned immutable content digest (`MCP_PUBLISHER_SHA256`) — not the release's own checksums
file, which an asset swap could rewrite in lockstep — before executing the binary and running
`mcp-publisher validate server.json`. No floating `latest` CLI is installed. Bumping the validator is a reviewed
change to both `MCP_PUBLISHER_VERSION` and `MCP_PUBLISHER_SHA256` in that workflow.

## Publication (manual, account-owner step)

Actual Registry publication is a manual release step that requires interactive GitHub authentication and therefore
is **out of scope for automation in this PR** (M8.2 excludes live publication/approval).

**Prerequisite — publish a marker-bearing `people-context-mcp` artifact first.** The Registry validates the package
named by `identifier` (`people-context-mcp`) by fetching its published PyPI README and confirming the `mcp-name:`
marker. `docs/releasing.md` uploads artifacts only when the GitHub Release workflow runs, and the currently
published `people-context-mcp` `0.1.0.post1` artifact predates this ownership marker. Crucially, bumping only the
root `project.version` does **not** republish the compatibility package: its version in
[compat/people-context-mcp/pyproject.toml](../compat/people-context-mcp/pyproject.toml) is a fixed line, and
`release.yml` uploads it with `skip-existing: true`, so an unchanged version is skipped and the old marker-less
README stays on PyPI. `mcp-publisher publish` would then still fail package validation. Successful PyPI publication
of a marker-bearing `people-context-mcp` release must therefore precede publication:

1. Bump `project.version`; **also bump the `version` in `compat/people-context-mcp/pyproject.toml`** so
   `skip-existing` uploads a fresh compatibility artifact; and **update `server.json` in the same commit** — set the
   top-level `version` and the `--from people-context==<version>` pin to the new release. (The synchronization
   assertions in `tests/test_registry_metadata.py` fail the build if `server.json` is left behind.) Keep the
   `mcp-name:` marker in both the `people-context` and `people-context-mcp` packaged READMEs. Merge.
2. Cut the GitHub Release so `release.yml` uploads the new marker-bearing artifacts — including the bumped
   `people-context-mcp` — to PyPI, and wait for that publication to complete.
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
| **MCP Registry** | https://github.com/modelcontextprotocol/registry (`docs/`) | Repository metadata + `mcp-publisher` | [`server.json`](../server.json) (schema `2025-12-11`) and the `mcp-name:` marker in both packaged READMEs | PyPI `stdio` entry; `uvx --from people-context==<server version> people-context-mcp` | GitHub OAuth via `mcp-publisher login github` proves `io.github.jinyangwang27` | Manual `mcp-publisher publish` after the marker-bearing PyPI artifact is live |
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
