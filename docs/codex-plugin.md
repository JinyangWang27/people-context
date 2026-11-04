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
[`.codex-plugin/mcp.json`](../.codex-plugin/mcp.json), with its working directory set to Codex's installed copy
of this repository:

```bash
uv run --no-dev --project . people-context-mcp
```

Codex has its own file because it does not expand `${...}` in `args` or set a plugin-root variable (checked on
Codex 0.166). It resolves a relative `cwd` against the plugin root instead. `--no-dev` installs only the runtime
dependencies: on an empty uv cache that is 31 packages and about 7 seconds, against 59 packages and about 25
seconds with development tools. `startup_timeout_sec` allows for slower networks on that first launch. Semantic
search is not installed by the plugin; without it, ordinary lexical search keeps working.

The server runs over stdio and does not listen on a TCP port.

The plugin passes no `--db` option and forwards `PEOPLE_CONTEXT_DB` and `PEOPLE_CONTEXT_DB_KEY` through `env_vars`,
so plugin launches use the first matching database location below:

1. `PEOPLE_CONTEXT_DB`;
2. `db_path` in `{XDG_CONFIG_HOME or ~/.config}/people-context/config.toml`; or
3. the shared per-user default `~/.pctx/people.db`.

The selected path is outside the installed plugin copy, survives upgrades and uninstallations, and is shared
with the `pctx` CLI. Run `pctx db-path -v` to inspect the active path and its resolution
trace.

## Troubleshooting

Three things can be true separately, so check them in order:

| State | What proves it |
|---|---|
| Skill installed | The people-context skills appear in Codex. This says nothing about the server. |
| Server starts | `pctx health` passes (command below). It uses a temporary database for the handshake and only reads yours. |
| Tools connected | A **new** Codex session can list the `people-context` tools. `codex mcp list` shows configuration only. |

Run the health check from the installed copy, replacing `<version>`:

```bash
uv run --no-dev --project ~/.codex/plugins/cache/people-context-plugins/people-context/<version> pctx health
```

| Symptom | Next action |
|---|---|
| `Project directory ${CLAUDE_PLUGIN_ROOT} does not exist` | You have a release from before this was fixed: update and reinstall the plugin. |
| `Failed to download` or `Network connectivity is disabled` | The first launch needs network access to install dependencies. Connect, then start a new session. |
| `uv` not found | Install [`uv`](https://docs.astral.sh/uv/), then restart Codex so it sees the new `PATH`. |
| The server reports a database error | Run `pctx db-path -v` to see which path was selected and why. |

If the tools still do not connect, every operation has a CLI equivalent: run the same `uv run` command with
`pctx --help` in place of `pctx health`. See [cli.md](cli.md).

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
uv run pytest -q tests/test_codex_plugin.py tests/adapters/mcp/test_server.py
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
