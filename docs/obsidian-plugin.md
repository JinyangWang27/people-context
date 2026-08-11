# Obsidian plugin

The repository includes a desktop-only, read-only Obsidian plugin under
[`obsidian-plugin/`](../obsidian-plugin/). It renders two live panes — a browsable person index
and a per-person brief — from the `pctx` command-line interface. It does not open SQLite, does
not add an MCP tool, and has no write path of any kind.

## Requirements

- Obsidian 1.5.0 or newer, on the desktop
- A working `pctx` executable, from an installed `people-context` package or a repository
  checkout
- Node.js 20.19+ to build the plugin from source

The plugin manifest sets `isDesktopOnly: true`: it starts a local process, which Obsidian
mobile cannot do.

## Install a build

The plugin is not yet published to the Obsidian community directory. To install a build:

```bash
cd obsidian-plugin
npm ci --no-audit --no-fund
npm run build
mkdir -p "<vault>/.obsidian/plugins/people-context"
cp build/main.js build/manifest.json build/styles.css "<vault>/.obsidian/plugins/people-context/"
```

Reload Obsidian and enable **People Context** under *Community plugins*. Obsidian warns that a
community plugin runs with local permissions; this one uses that permission to start `pctx` and
for nothing else.

## What the panes read

| Pane | Command |
| --- | --- |
| People index | `pctx list --json` |
| Person brief | `pctx brief <person-id> --json` |

Both are the versioned machine documents described in
[compatibility.md](compatibility.md): `people-context-person-index` and
`people-context-brief`. The plugin ignores fields it does not know, so a newer server keeps
working, and it reports rather than fails when a document announces a newer version.

The brief is always addressed by the stable id the index returned, never by a display name.
`--include-sensitive` is never passed, and `--all` is never passed, so the panes show
ordinary-disclosure records for people who have not been soft-deleted.

## Settings

| Setting | Effect on the command |
| --- | --- |
| people-context executable | The program `spawn` is given. |
| Database path | Adds a global `--db <path>` pair. |
| Encrypted database | Adds the global `--encrypted` flag. |
| Refresh | Whether opening a pane reads immediately (`on-open`) or waits (`manual`). |

`refresh` defaults to `on-open`: a pane that opens empty and needs a second action reads as
broken, and one read is cheap. Nothing polls on a timer.

## Process-execution safety

Every value the plugin displays is untrusted personal data, so the bridge is built so that
displayed data can never become executed data:

- `child_process.spawn` with a separate argument array and `shell: false`; no command string is
  ever constructed, and the executable is the program argument rather than interpolated text;
- no free-form extra-arguments setting, so the argument array has a fixed shape;
- a finite timeout with process-group termination on POSIX;
- bounded stdout and stderr capture, with an explicit oversized-output error;
- cancellation through an `AbortSignal` when a pane is closed or superseded;
- non-zero exits reported with bounded stderr text, and a missing executable reported as such;
- `windowsHide: true`;
- no logging of JSON payloads or of `PEOPLE_CONTEXT_DB_KEY`.

Person ids are validated before they can become an argument, and a name that looks like an
option or a shell fragment stays inert display text. The panes are painted with text nodes,
never with `innerHTML`.

## Encrypted databases

With the encrypted setting on, the plugin adds `--encrypted` and the CLI reads the key from the
`PEOPLE_CONTEXT_DB_KEY` environment variable the Obsidian process already carries. The plugin
never stores, prompts for, or logs the key; there is no settings field for it.

If Obsidian did not inherit the variable, the plugin reports the CLI's own refusal —
*"Encrypted mode requires a non-empty PEOPLE_CONTEXT_DB_KEY environment variable. Refusing to
continue; plaintext is never used as a fallback."* — plus an instruction to launch or configure
Obsidian with that environment. It never falls back to opening the database unencrypted.

## Privacy boundary

The plugin only reads, and it only reads ordinary-disclosure records. What it renders is still
personal data, and Obsidian may synchronize the vault it renders into. **Anything cached or
written into a synchronized vault has left the local-first perimeter this project maintains**,
and is governed by that sync provider rather than by people-context's disclosure controls. See
[privacy-and-safety.md](privacy-and-safety.md).

## Development and validation

```bash
cd obsidian-plugin
npm ci --no-audit --no-fund
npm run typecheck
npm test
npm run build
```

`npm ci` is used everywhere, including CI; `npm install` would rewrite the committed lockfile.
The build output is not committed. `.github/workflows/obsidian-plugin-validate.yml` builds
twice from two clean lockfile installations and requires byte-identical artifact checksums.

## Release and community distribution

The plugin has its own version domain, synchronized internally between `manifest.json`,
`package.json`, and `package-lock.json`. It does not move with the server release.

Pushing a tag of the form `obsidian-plugin-v<version>` runs
`.github/workflows/obsidian-plugin-release.yml`, which verifies that the tag matches the
manifest version, type-checks, tests, builds twice, compares checksums, and uploads the mirror
tree (`main.js`, `manifest.json`, `styles.css`, `README.md`, `LICENSE`, `SHA256SUMS`) as a
build artifact.

Obsidian's community distribution expects a dedicated repository whose root holds
`manifest.json` and whose releases carry `main.js`, `manifest.json`, and `styles.css`. Mirroring
is therefore configuration-gated and is **an outstanding user-operated step**:

1. create the community-distribution repository;
2. set the repository variable `OBSIDIAN_PLUGIN_MIRROR_REPO` to `owner/repo`;
3. set the repository secret `OBSIDIAN_PLUGIN_MIRROR_TOKEN` to a token scoped to that
   repository only.

Until both are configured, the release workflow stops after publishing the verified artifact
and prints a notice; publish that tree manually if you want a release before then. The mirror
release is tagged with the bare version, with no prefix, because that is how Obsidian resolves
a community plugin.
