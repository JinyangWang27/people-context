# people-context MCPB bundle (native UV)

This directory builds the [`.mcpb`](https://github.com/modelcontextprotocol/mcpb) Desktop bundle for the
people-context MCP server. It is a **native-UV** bundle: it ships no interpreter and no vendored virtual
environment. The host application's UV runtime installs the pinned `people-context` release declared in
[`pyproject.toml`](pyproject.toml) and runs [`server/main.py`](server/main.py), which delegates to the same
stdio server as `people-context-mcp`.

## Contents

| File | Role |
|---|---|
| [`manifest.json`](manifest.json) | MCPB manifest (`server.type="uv"`, `entry_point="server/main.py"`). |
| [`pyproject.toml`](pyproject.toml) | Bundle project pinning `people-context==<release>`; `[tool.uv] package = false`. |
| [`server/main.py`](server/main.py) | Thin entry point delegating to `people_context.adapters.mcp.server:main`. |
| [`build.sh`](build.sh) | Validates, packs, and lists the archive with a pinned MCPB CLI. |
| [`.mcpbignore`](.mcpbignore) | Excludes tooling, docs, and transient UV/Python state from the archive. |

## Build

Requires Node.js (for `npx`) and `uv`.

```bash
mcpb/build.sh            # writes mcpb/dist/people-context.mcpb and lists its contents
```

The script uses an exact reviewed MCPB CLI version (`@anthropic-ai/mcpb@2.1.2`) — never a floating latest —
and prints the archive contents so they can be inspected before the bundle is attached to a GitHub Release.

## Versioning

`manifest.json.version` and the `people-context==<version>` pin in `pyproject.toml` track the application
release (`project.version` in the root [pyproject.toml](../pyproject.toml)). The schema `manifest_version`
is independent and follows the MCPB tooling. `tests/test_mcpb_bundle.py` asserts these stay synchronized.

## Local permissions

Installing this bundle runs local Python with your own filesystem permissions. It is **not** a sandbox: the
SQLite database is plaintext, so rely on filesystem permissions and full-disk encryption. Elevated
sensitive-context and full-export tools stay disabled unless you opt in through process environment flags.

See [docs/desktop-and-editors.md](../docs/desktop-and-editors.md) for installation and editor configuration.
