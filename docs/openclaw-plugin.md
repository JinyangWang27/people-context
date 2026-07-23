# OpenClaw plugin

The repository includes a native OpenClaw tool plugin under [`openclaw-plugin/`](../openclaw-plugin/). It connects
OpenClaw to a locally running `people-context-mcp` Streamable HTTP server and is packaged for distribution through
ClawHub. It does not require a hosted application, public endpoint, OAuth service, or shared database.

## Requirements

- OpenClaw 2026.7.1 or newer
- Node.js 22.22.3+, 24.15+, or 25.9+
- [`uv`](https://docs.astral.sh/uv/) and the `people-context` Python package or repository checkout
- A loopback `people-context-mcp` Streamable HTTP server

## Install from ClawHub

Install and inspect the published plugin with:

```bash
openclaw plugins install clawhub:openclaw-plugin-people-context
openclaw plugins inspect people-context --runtime --json
```

Restart the OpenClaw Gateway after installation or configuration changes.

## Local runtime

Start the MCP server before OpenClaw loads the plugin:

```bash
uv run people-context-mcp --http --host 127.0.0.1 --port 8765
```

The plugin connects to `http://127.0.0.1:8765/mcp` by default. Configure a different loopback endpoint under
`plugins.entries.people-context.config`:

```json
{
  "plugins": {
    "entries": {
      "people-context": {
        "enabled": true,
        "config": {
          "baseUrl": "http://127.0.0.1:8765",
          "path": "/mcp"
        }
      }
    }
  }
}
```

The Python server resolves its database through the standard chain documented in [cli.md](cli.md) and
[data-model.md](data-model.md). When an OpenClaw workspace exists, that chain can select its
`people-context/people.db`; run `people-context db-path -v` in the server environment to inspect the selected path.

The plugin exposes these OpenClaw tools:

- `people_resolve` wraps `resolve_person`;
- `people_context` wraps the ordinary-disclosure `get_person_context` path;
- `people_communication_guidance` wraps `get_communication_guidance`; and
- `people_remember` wraps `remember_person` and is optional because it writes durable data.

To make `people_remember` available to an agent or session whose tool policy uses `alsoAllow`, add it explicitly:

```json
{
  "tools": {
    "alsoAllow": ["people_remember"]
  }
}
```

## Security model

Installing the plugin executes its JavaScript with the OpenClaw process permissions. The plugin then connects to
an unauthenticated MCP HTTP server that can access a plaintext SQLite database containing personal data. Keep the
server bound to loopback, install only revisions you trust, and remember that other local processes can reach the
endpoint while it is running.

The ordinary plugin surface preserves the server disclosure boundary:

- `people_context` cannot request sensitive or restricted records;
- the plugin does not wrap `get_sensitive_person_context` or `export_data`; and
- persistent writes remain optional and require an explicit OpenClaw tool-policy allowlist entry.

OpenClaw tool policy is an additional model-facing control, not a replacement for the server's process-level
capability gates. See [Privacy and Safety](privacy-and-safety.md) for the complete threat model.

## Update

Update an installed ClawHub release, then restart the Gateway:

```bash
openclaw plugins update people-context
openclaw plugins inspect people-context --runtime --json
```

Package releases use semantic versioning. Keep `version` in `openclaw-plugin/package.json` and
`openclaw-plugin/openclaw.plugin.json` identical, rebuild `dist/`, and commit the generated output before publishing.

## Local validation

Use a current ClawHub CLI so its bundled plugin inspector and publish preflight match the registry:

```bash
cd openclaw-plugin
npm ci --no-audit --no-fund
npm run release:check
cd ..
clawhub package validate ./openclaw-plugin
clawhub package publish ./openclaw-plugin \
  --owner jinyangwang27 \
  --family code-plugin \
  --dry-run
```

For an end-to-end install before publication:

```bash
cd openclaw-plugin
npm pack
openclaw plugins install npm-pack:./openclaw-plugin-people-context-0.2.0.tgz
openclaw plugins inspect people-context --runtime --json
```

Start the local HTTP server and exercise every read tool against a temporary database. Confirm that
`people_remember` is absent until explicitly allowlisted and that no sensitive-context or export wrapper is present.

## Publish to ClawHub

The package has the ClawHub-required `openclaw.compat.pluginApi` and `openclaw.build.openclawVersion` metadata. Its
GitHub Actions workflow uses ClawHub's reusable package publisher, pins it to a reviewed commit, performs a dry run
on plugin pull requests, and limits live publication to `workflow_dispatch`.

A manual, authenticated release uses:

```bash
npm install --global clawhub@latest
clawhub --cli-version
clawhub login
clawhub whoami
clawhub package validate ./openclaw-plugin
clawhub package publish ./openclaw-plugin \
  --owner jinyangwang27 \
  --family code-plugin \
  --dry-run
clawhub package publish ./openclaw-plugin \
  --owner jinyangwang27 \
  --family code-plugin
```

Current ClawHub releases use device-code authentication. Follow the verification URL and code printed by the CLI;
opening that verification page is expected, but the login flow must not redirect an API token to a loopback
callback. If `clawhub login --help` says `Log in (opens browser or stores token)` or offers `--no-browser` without
a device flow, the installed CLI is obsolete: upgrade it with the command above, verify which binary is active,
then rerun `clawhub login`.

To publish later releases through GitHub OIDC, configure trusted publishing:

```bash
clawhub package trusted-publisher set openclaw-plugin-people-context \
  --repository JinyangWang27/people-context \
  --workflow-filename package-publish.yml
```

The package must exist before trusted publishing can be attached. Keep `CLAWHUB_TOKEN` available for break-glass
releases; once trusted publishing is configured, manually dispatched workflow releases can use GitHub OIDC.

## Publishing checklist

1. Keep both OpenClaw package and manifest versions identical.
2. Install dependencies from the committed lockfile and run `npm run release:check`.
3. Run `clawhub package validate` and resolve hard compatibility errors.
4. Review the ClawHub publish dry run, including name, owner, version, source attribution, and compatibility fields.
5. Smoke-test the packed artifact with the local server and verify the default tool boundary.
6. Publish the release, then inspect the resulting ClawHub package and scan state.
