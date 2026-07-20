# M8 — Distribution & reach

Status: Planned. See [docs/roadmap.md](../roadmap.md#m8--distribution--reach).

## Motivation

The project already publishes the primary `people-context` PyPI distribution, a compatibility
`people-context-mcp` shim, a Claude Code plugin, and an OpenClaw package. M8 reduces the distance from discovery
to a working local stdio server: zero-clone execution, Registry/directory metadata, a native-UV Desktop bundle,
common editor configuration, and an optional container image. All paths retain the existing local-process threat
model and MCP behavior.

## Scope

In scope:

- verify and lead with `uvx --from people-context people-context-mcp`;
- official MCP Registry metadata and ownership proof;
- a current submission/metadata matrix for Smithery, PulseMCP, mcp.so, and Glama, including required in-repo files;
- a native-UV `.mcpb` Desktop extension;
- Cursor, Windsurf, and VS Code snippets using the canonical `uvx` invocation;
- an optional non-root stdio Docker image and GHCR release job.

Non-goals:

- domain/app/port/tool changes;
- hosted service, authenticated remote transport, or HTTP-default container;
- vendored interpreter/virtualenv inside MCPB;
- live community-directory submission where human/account approval is required.

## Design

### Zero-clone PyPI path

The primary distribution is `people-context`; `people-context-mcp` is compatibility-only. Verify on a clean
machine:

```text
uvx --from people-context people-context-mcp --help
```

and one real stdio remember/resolve/context round trip. Put this path before tool installation and source checkout
in the README. Record exact commands and environment evidence in the implementation PR.

### MCP Registry and community directories

Add root `server.json` following the current official Registry schema:

- top-level server `version`;
- a `packages` entry for PyPI identifier `people-context` with the same version;
- stdio package transport, not an arbitrary command/args description;
- packaged README `mcp-name:` ownership marker.

Use the Registry's own `mcp-publisher` for validation/publication. Pin the reviewed publisher version in CI and
release automation; do not install an unbounded latest CLI. The namespace decision is recorded because it becomes
public identity.

In the same PR, verify the current requirements of Smithery, PulseMCP, mcp.so, and Glama from their primary
submission documentation. Produce a table recording, for each directory:

- external/manual submission versus repository metadata;
- any required metadata filename/schema;
- package/transport representation;
- ownership/authentication step;
- whether live publication remains manual.

Add required static repository files where applicable and validate them with the directory's pinned official
validator when one exists. Reuse canonical descriptions from `docs/mcp-interface.md`; do not create divergent tool
inventories. Actual listing approval/submission may remain a documented release/manual step.

### Native-UV MCPB bundle

An MCPB is a ZIP with root `manifest.json` and bundled local server files, not a Claude Desktop `mcpServers`
command block. Add:

- `mcpb/manifest.json` with `server.type="uv"` and `entry_point="server/main.py"`;
- root bundle `mcpb/pyproject.toml` pinning the matching `people-context` release;
- thin `mcpb/server/main.py` delegating to `people_context.adapters.mcp.server:main`;
- a build script using an exact reviewed MCPB CLI version;
- release attachment and archive-content inspection.

MCPB semantic `manifest.json.version` follows the application release. `manifest_version` is a separate schema
version validated against the pinned tooling. The host manages Python/dependency installation; do not vendor an
interpreter or virtual environment.

### Editor/IDE snippets

Add Cursor, Windsurf, and VS Code stdio configurations beside the existing client documentation. Every ordinary
client snippet uses one canonical invocation:

```text
uvx --from people-context people-context-mcp
```

MCPB retains its native manifest shape and is not rewritten as that command.

### Optional Docker image

Add a multi-stage image built with `uv`, pinned base-image digests, non-root runtime, and stdio default entrypoint.
Document an explicit bind-mounted data directory plus `PEOPLE_CONTEXT_DB`; do not invent container-only database
resolution. Runtime behavior makes no outbound request except the already explicit semantic-model download command.

Publish to GHCR on tags with pinned Actions and least-privilege `GITHUB_TOKEN` permissions. Do not add a long-lived
registry secret when GitHub's scoped token is sufficient.

## Migration needs

None.

## CLI / MCP surface changes

None. Packaging wraps the existing entrypoints.

## Security and privacy

- Every integration runs local Python with the launching user's filesystem permissions; none is presented as a
  sandbox.
- Metadata contains public project information only and adds no analytics/telemetry.
- Container stdio remains default; container loopback is not treated as equivalent to host-only loopback.
- All Actions, base images, and external validation/release CLIs are pinned to reviewed immutable references.
- New credentials use short-lived, workflow-scoped, least-privilege mechanisms.

## Testing strategy

- clean-machine `uvx` help and real stdio round trip;
- pinned `mcp-publisher validate` for `server.json` and version-equality assertion;
- directory metadata/schema validation and link checks against the recorded matrix;
- pinned MCPB validate/pack, archive inspection, semantic-version synchronization, Desktop smoke test;
- manual clean installs for each editor snippet;
- Docker build/help/stdio round trip with a temporary mounted database;
- `uv run ruff check .` and `uv run pytest -q` remain green.

## Open questions

1. Which Registry namespace is the durable public identity?
2. Which community listings require account-owner manual approval after repository metadata lands?
3. Should a later image add an explicitly documented authenticated HTTP deployment profile?
