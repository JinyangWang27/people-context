# People Context OpenClaw Plugin

An OpenClaw tool plugin that connects agents to
[`people-context`](https://github.com/JinyangWang27/people-context),
a local-first store for durable knowledge about the people in your life.

## Tools

- `people_resolve` — resolve a name, nickname, or partial reference
- `people_context` — retrieve a bounded public/personal context bundle
- `people_communication_guidance` — retrieve communication-relevant traits, history, and reminders
- `people_remember` — create or update a person record

`people_remember` changes persistent data, so it is an optional tool and is not
exposed to the model unless the operator explicitly allowlists it.

## Requirements

- OpenClaw `2026.7.1` or newer
- Node.js `22.22.3+`, `24.15+`, or `25.9+`
- A running `people-context-mcp` Streamable HTTP server

## Install from ClawHub

```bash
openclaw plugins install clawhub:openclaw-plugin-people-context
openclaw plugins inspect people-context --runtime --json
```

Restart the Gateway after installation or configuration changes.

## Start the MCP server

From a `people-context-mcp` checkout:

```bash
uv run python -m people_context --http --db people_context.db --port 8765
```

The default endpoint is `http://127.0.0.1:8765/mcp`.

## Configure OpenClaw

Plugin settings belong under `plugins.entries.people-context.config`:

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

The plugin sends person data to the configured MCP endpoint. Keep the endpoint
local or use a transport you trust.

### Enable persistent writes

To expose `people_remember`, add it to the effective tool allowlist used by the
agent or session. For example, where an existing tool policy already uses
`alsoAllow`:

```json
{
  "tools": {
    "alsoAllow": ["people_remember"]
  }
}
```

Read-only tools remain available according to the normal OpenClaw tool policy.

## Security model

This plugin runs JavaScript locally and connects to a local Python MCP process that can read a plaintext SQLite
file containing personal data. Neither component is sandboxed from the user's filesystem. Install only trusted
revisions, keep the endpoint on loopback, and treat every local process as able to reach the unauthenticated
HTTP endpoint while it is enabled.

The ordinary `people_context` wrapper cannot request sensitive or restricted rows. The underlying server also
keeps full export absent from default MCP discovery; use the human-operated CLI for routine export.

## Local development

```bash
cd openclaw-plugin
npm ci
npm run plugin:build
npm run plugin:validate
npm test
npm run release:check
```

For an installation smoke test:

```bash
npm pack
openclaw plugins install npm-pack:./openclaw-plugin-people-context-0.2.0.tgz
openclaw plugins inspect people-context --runtime --json
```

## Publish to ClawHub

Install and authenticate the ClawHub CLI, then validate and dry-run a release:

```bash
npm i -g clawhub@latest
clawhub --cli-version
clawhub login
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

To publish later releases through GitHub OIDC, enable trusted GitHub Actions publishing:

```bash
clawhub package trusted-publisher set openclaw-plugin-people-context \
  --repository JinyangWang27/people-context \
  --workflow-filename package-publish.yml
```

The repository workflow performs a ClawHub dry run for pull requests and can
publish through `workflow_dispatch`. Tag-based and break-glass publishing still
require a `CLAWHUB_TOKEN` secret.
