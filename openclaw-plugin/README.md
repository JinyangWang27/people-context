# People Context OpenClaw Plugin

An OpenClaw tool plugin that connects your agent to
[`people-context-mcp`](https://github.com/JinyangWang27/people-context-mcp),
a local-first store for knowledge about the people in your life.

## What it adds

- `people_resolve` — resolve a name, nickname, or partial reference to known people
- `people_context` — get a minimal-disclosure context bundle for a person
- `people_communication_guidance` — get traits, friction notes, reminders, and your communication philosophy
- `people_remember` — create or update a person record

## Status

This plugin speaks a small HTTP bridge protocol while the upstream
`people-context-mcp` HTTP transport (M4) is still in development. Once M4 lands,
the plugin can be pointed directly at the official MCP HTTP server.

## Setup

### 1. Start the people-context HTTP bridge

From the `people-context-mcp` checkout, run:

```bash
uv run python openclaw-plugin/bridge.py --db people_context.db --port 8765
```

Or copy `bridge.py` next to your database and run it directly.

### 2. Install the plugin in OpenClaw

```bash
openclaw plugins install ./openclaw-plugin
```

Restart the OpenClaw gateway.

### 3. Configure (optional)

In your OpenClaw gateway config, set the bridge URL:

```json
{
  "plugins": {
    "people-context": {
      "baseUrl": "http://127.0.0.1:8765"
    }
  }
}
```

Defaults to `http://127.0.0.1:8765`.

## Development

```bash
cd openclaw-plugin
npm install
npm run build
npm run plugin:validate
npm test
```

## Publishing to ClawHub

```bash
clawhub login
clawhub package validate ./openclaw-plugin
clawhub package publish ./openclaw-plugin --owner jinyangwang27
```

After the first publish, set up trusted publishing from GitHub Actions:

```bash
clawhub package trusted-publisher set @jinyangwang27/people-context \
  --repository JinyangWang27/people-context-mcp \
  --workflow-filename package-publish.yml
```
