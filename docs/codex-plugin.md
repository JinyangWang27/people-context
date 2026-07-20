# Codex plugin

The repository is a self-hosted Codex plugin marketplace. The plugin launches `people-context-mcp` locally
over MCP stdio and stores durable data outside the installed plugin copy. It does not require a hosted server,
public endpoint, OAuth service, or shared database.

## Requirements

- Codex with plugin support
- [`uv`](https://docs.astral.sh/uv/) available on `PATH`

## Install from GitHub

Add the repository as a marketplace, then install the plugin:

```bash
codex plugin marketplace add JinyangWang27/people-context
codex plugin add people-context@people-context-plugins
```

Start a new Codex session after installation. The `people-context` MCP tools should then be available without
adding a separate MCP server to `config.toml`.

## Local runtime

Codex reads [`.codex-plugin/plugin.json`](../.codex-plugin/plugin.json), then starts the MCP server described by
the root [`.mcp.json`](../.mcp.json):

```bash
uv run --project "${CLAUDE_PLUGIN_ROOT}" people-context-mcp
```

Codex provides `CLAUDE_PLUGIN_ROOT` for plugin compatibility. It points to Codex's installed copy of this
repository. The server runs over stdio and does not listen on a TCP port.

The plugin passes no `--db` option, so plugin launches use the first matching database location below:

1. `PEOPLE_CONTEXT_DB`;
2. `db_path` in `{XDG_CONFIG_HOME or ~/.config}/people-context/config.toml`;
3. `{OPENCLAW_WORKSPACE}/people-context/people.db` when that workspace directory exists;
4. `~/.openclaw/workspace/people-context/people.db` when that workspace directory exists; or
5. `{XDG_DATA_HOME or ~/.local/share}/people-context/people.db` as the final fallback.

The selected path is outside the installed plugin copy, survives upgrades and uninstallations, and is shared
with the `people-context` CLI. Run `people-context db-path -v` to inspect the active path and its resolution
trace.

## Security model

Installing the plugin executes this repository's Python code with the current operating-system user's
permissions. It is not a sandboxed, data-only extension. Install only revisions you trust.

The durable database is plaintext SQLite. Filesystem permissions and full-disk encryption are the at-rest
security boundary. Ordinary plugin startup preserves the server's default disclosure controls:

- `get_person_context` excludes sensitive and restricted records;
- `get_sensitive_person_context` is absent unless the process starts with
  `PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE=1`; and
- `export_data` is absent unless the process starts with `PEOPLE_CONTEXT_MCP_ENABLE_EXPORT=1`.

MCP annotations are advisory metadata, not authorization. Process-level capability gates enforce these
high-disclosure boundaries. See [Privacy and Safety](privacy-and-safety.md) for the complete threat model.

## Update

Refresh the marketplace snapshot and reinstall the plugin after a release:

```bash
codex plugin marketplace upgrade people-context-plugins
codex plugin add people-context@people-context-plugins
```

Start a new Codex session so the refreshed MCP configuration is loaded. Release changes must update both
`project.version` in `pyproject.toml` and `version` in `.codex-plugin/plugin.json`.

## Local validation

From the repository root:

```bash
uv run ruff check .
uv run pytest -q tests/test_codex_plugin.py tests/adapters/test_mcp_server.py
uv run --locked people-context-mcp --help
```

For an end-to-end local install in a clean Codex profile:

```bash
codex plugin marketplace add ./
codex plugin add people-context@people-context-plugins
```

Start a new session, then exercise `resolve_person`, `get_person_context`, and `remember_person` against a
temporary database. Confirm that `get_sensitive_person_context` and `export_data` are absent from ordinary tool
discovery.

## Publishing checklist

1. Keep the Python package and Codex plugin versions identical.
2. Run the local validation commands on the intended release commit.
3. Test installation from a clean Codex profile using the GitHub marketplace commands.
4. Confirm that durable data remains outside the installed plugin directory.
5. Confirm that high-disclosure tools remain absent from default discovery.
6. Publish the release, then refresh and reinstall it from the GitHub marketplace.
