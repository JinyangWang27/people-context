# Desktop bundle and editor configuration

This document covers the native-UV MCPB Desktop bundle and the Cursor, Windsurf, and VS Code stdio
configurations for `people-context`. It delivers checklist item **M8.3** of
[docs/specs/pr-plan.md](specs/pr-plan.md); see
[docs/specs/m8-distribution-and-reach.md](specs/m8-distribution-and-reach.md) for the binding milestone
specification.

The canonical tool inventory and response contracts live in [docs/mcp-interface.md](mcp-interface.md); this
document does not restate them. Every path here runs the same local stdio server as
`people-context-mcp` with no behavior change.

## Local permissions (read first)

Every integration below runs local Python with your own filesystem permissions. **None is a sandbox.** The
SQLite database is plaintext, so rely on filesystem permissions and full-disk encryption. Ordinary discovery
excludes elevated sensitive context and full export; those tools require process environment flags and cannot
be enabled through tool arguments.

## Native-UV MCPB Desktop bundle

An [MCPB bundle](https://github.com/modelcontextprotocol/mcpb) (`.mcpb`) is a ZIP archive with a root
`manifest.json` that MCP-aware desktop hosts (such as Claude Desktop) install with one click. This bundle is
**native UV**: it ships no interpreter and no vendored virtual environment. The host's UV runtime installs the
pinned `people-context` release from PyPI and runs the bundled entry point.

The bundle sources live in [`mcpb/`](../mcpb):

- [`mcpb/manifest.json`](../mcpb/manifest.json) — `server.type="uv"`, `entry_point="server/main.py"`. Its
  semantic `version` tracks the application release; the schema `manifest_version` is independent and follows
  the MCPB tooling.
- [`mcpb/pyproject.toml`](../mcpb/pyproject.toml) — pins `people-context==<release>` and sets
  `[tool.uv] package = false` so the host UV runtime installs only the dependency.
- [`mcpb/server/main.py`](../mcpb/server/main.py) — a thin entry point delegating to
  `people_context.adapters.mcp.server:main`.

### Build

Requires Node.js (for `npx`) and `uv`:

```bash
mcpb/build.sh
```

The script validates the manifest, packs `mcpb/dist/people-context.mcpb`, and lists the archive contents for
inspection. It uses an exact reviewed MCPB CLI version (`@anthropic-ai/mcpb@2.1.2`) — never a floating latest.
The packed archive contains only `manifest.json`, `pyproject.toml`, and `server/main.py`; build tooling and
docs are excluded by [`mcpb/.mcpbignore`](../mcpb/.mcpbignore).

### Install (manual, user-operated)

Released builds are attached to the matching GitHub Release (see [releasing.md](releasing.md)). To install:

1. Download `people-context.mcpb` from the release, or build it locally with `mcpb/build.sh`.
2. Open your desktop MCP host and install the `.mcpb` file (in Claude Desktop: **Settings → Extensions →
   Advanced settings → Install Extension**, or drag the file onto the window).
3. Enable the extension. On first launch the host's UV runtime installs the pinned `people-context` release.

Installing and enabling the bundle in a desktop host is a manual step that cannot be automated from the
repository. Acknowledge the local-permission note above before enabling it.

## Editor / IDE stdio configuration

Every ordinary client uses one canonical invocation:

```text
uvx --from people-context people-context-mcp
```

Only the config file path and root key differ per editor. (The MCPB bundle keeps its native manifest shape and
is deliberately **not** rewritten as this command.)

### Cursor

Add to `.cursor/mcp.json` (project scope) or `~/.cursor/mcp.json` (global scope):

```json
{
  "mcpServers": {
    "people-context": {
      "command": "uvx",
      "args": ["--from", "people-context", "people-context-mcp"]
    }
  }
}
```

### Windsurf

Add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "people-context": {
      "command": "uvx",
      "args": ["--from", "people-context", "people-context-mcp"]
    }
  }
}
```

### VS Code

Add to `.vscode/mcp.json` (workspace scope). VS Code uses the `servers` key and an explicit `type`:

```json
{
  "servers": {
    "people-context": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "people-context", "people-context-mcp"]
    }
  }
}
```

Each snippet is verified by a manual clean install in its editor. To pin a specific release, replace
`people-context` in `--from` with `people-context==<version>`.
