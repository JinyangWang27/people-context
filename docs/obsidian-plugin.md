# Obsidian plugin

The repository includes a desktop-only, read-only Obsidian plugin under
[`obsidian-plugin/`](../obsidian-plugin/). It renders two live panes — a browsable person index
and a per-person brief — from the `pctx` command-line interface. It does not open SQLite, does
not add an MCP tool, and has no write path of any kind.

## Requirements

- Obsidian 1.5.0 or newer, on the desktop
- A persistently installed `pctx` executable, for example from `uv tool install people-context`
  or a repository checkout's virtual environment. `uvx --from people-context pctx` is not
  enough on its own: it runs the command in a throwaway environment and leaves no executable
  for the plugin to spawn. An absolute path configured as the executable setting works too
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

Under `manual`, nothing reads until you ask — including a brief tab restored from a previous
session. Such a tab keeps the person it was showing and says so, and *Refresh people-context
panes* reads it. That is what makes `manual` a genuine control over when the database is
opened, as the next section describes.

## What "read-only" means here, precisely

The plugin performs no durable record mutation: it has no write path, records nothing, and
mints no audit or changelog rows. It is a viewer.

It is not, however, a read-only *file* operation, and the distinction matters. The panes run
the ordinary `pctx` read commands, and every CLI and MCP entry point in this project resolves
its database through the shared runtime, which opens the file — **creating it, and its parent
directories, when it is absent, and applying any pending forward-only migrations before the
read runs**. That is project-wide behaviour rather than something the plugin introduces, but
the plugin is the first surface where it can happen without the user typing a command: with
the default `on-open` refresh, merely opening the People pane is enough.

Two consequences worth knowing:

- a mistyped database path creates a new, empty database at that path rather than reporting
  that nothing is there;
- opening a pane after upgrading people-context migrates the database, and migrations are
  forward-only, so an older release may no longer be able to open it afterwards.

Set **Refresh** to `manual` if you would rather decide when that happens. A genuinely
non-creating, non-migrating CLI read mode would remove the ambiguity entirely; that is a
change to the command-line interface itself and is tracked separately from this plugin.

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

Person ids are passed after a bare `--`, so an id is always read as the person and never as an
option. Ids are deliberately *not* pattern-checked: the project's identifier contract admits
any non-blank string, and a database restored from a sync bundle may legitimately carry one, so
safety comes from argument separation rather than from narrowing the data contract. Names and
other display data never become arguments at all, and the panes are painted with text nodes,
never with `innerHTML`.

Both panes report a document version newer than the plugin understands rather than failing or
silently showing a partial view.

## Encrypted databases

Encryption is an opt-in extra, not part of the base package, so the executable the plugin
spawns must have been installed with it. `uv tool install people-context` alone produces a
`pctx` that fails immediately when `--encrypted` is passed, however valid the key:

```bash
uv tool install 'people-context[encrypted]'
```

The `encrypted` extra pulls `sqlcipher3-binary`, which publishes wheels only for glibc Linux on
x86_64. Every other platform — macOS, Windows, non-x86_64, and musl-based Linux such as Alpine —
needs a locally built `sqlcipher3` in the same environment instead. See
[privacy-and-safety.md](privacy-and-safety.md#optional-at-rest-encryption).

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

The brief pane records which person it is showing in the host's workspace layout, so a restored
tab reopens on that person instead of empty. Only the opaque id is stored — no name, and none of
their records — but it lands in the vault alongside the rest of the layout, and therefore inside
the same synchronization boundary described above.


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
