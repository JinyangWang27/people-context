# People Context for Obsidian

A read-only Obsidian plugin that renders the people in your
[people-context](https://github.com/JinyangWang27/people-context) database as live panes: a
browsable person index and a per-person brief.

The plugin is a viewer and nothing else. It has no write path, no editing surface, and no way
to record anything back into the database.

## How it reads your data

Everything on screen comes from two command-line reads:

| Pane | Command |
| --- | --- |
| People index | `pctx list --json` |
| Person brief | `pctx brief <person-id> --json` |

The plugin never opens the SQLite database itself, and it never asks for a person by name: it
addresses a brief with the stable id the index returned, so a contact whose display name looks
like a command or an option can only ever be text on screen.

`--include-sensitive` is never passed. The panes show ordinary-disclosure records only.

## Requirements

- a persistently installed `pctx` executable. `uv tool install people-context` puts one on
  `PATH`; `uvx --from people-context pctx` only *runs* the command in a throwaway environment
  and leaves nothing for the plugin to spawn. Any absolute path works too — set it as the
  executable in the plugin settings;
- Obsidian on the desktop. The plugin is marked `isDesktopOnly` because it starts a local
  process, which Obsidian mobile cannot do.

## Settings

| Setting | Meaning |
| --- | --- |
| people-context executable | Path to `pctx`, or a bare name resolved through `PATH`. |
| Database path | Optional explicit database file. Empty lets the CLI resolve it. |
| Encrypted database | Adds the global `--encrypted` flag. |
| Refresh | Whether opening a pane reads immediately, or only on request. |

Each setting is a typed field. There is deliberately no free-form arguments box: every
invocation is assembled as an argument array, so there is no place to inject an extra flag.

### Encrypted databases

With **Encrypted database** on, the plugin adds `--encrypted` and the CLI reads the key from
the `PEOPLE_CONTEXT_DB_KEY` environment variable that the Obsidian process already carries.
The plugin never stores, prompts for, or logs the key.

If Obsidian was not started with that variable, the plugin reports the CLI's own refusal and
stops. It does not fall back to opening the database unencrypted. Launch Obsidian from a shell
that exports the key, or set it in the desktop session your launcher uses.

## What "read-only" means here

The plugin never writes a record: no write path, nothing recorded, no audit or changelog rows.

It is not a read-only file operation, though. The panes run the ordinary `pctx` read commands,
and those open the database through the shared runtime, which creates it when absent and
applies pending forward-only migrations first. That is how every people-context command
behaves; the plugin is simply the first place it can happen without you typing one, because
**Refresh** defaults to `on-open`. So a mistyped database path creates an empty database there,
and opening a pane after an upgrade migrates the file — which an older release may then be
unable to open. Set **Refresh** to `manual` if you would rather choose the moment.

## Privacy

The database stays local and the plugin only ever reads it, but what a pane renders is still
personal data. Obsidian may synchronize whatever is cached or written into your vault, and
anything that leaves this machine has left the local-first perimeter people-context maintains.
Treat a synchronized vault as a copy of the records it displays.

## Development

Development happens in the [people-context monorepo](https://github.com/JinyangWang27/people-context)
under `obsidian-plugin/`.

```bash
npm ci --no-audit --no-fund
npm run typecheck
npm test
npm run build
```

`npm run build` writes `build/main.js`, `build/manifest.json`, and `build/styles.css` and
prints a SHA-256 checksum for each. The build is not committed: continuous integration builds
it twice from clean lockfile installations and requires identical checksums.

To try a development build, copy those three files into
`<vault>/.obsidian/plugins/people-context/` and reload Obsidian.
