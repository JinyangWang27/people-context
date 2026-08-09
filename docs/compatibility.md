# Compatibility promise

This document states the compatibility discipline the project already follows and commits to keeping. It covers
the four surfaces integrations depend on — MCP tools and responses, the SQLite database, the `pctx` CLI, and the
machine-readable JSON documents — and it states plainly which surfaces are deliberately *not* frozen.

## Scope and versioning

The project uses [Semantic Versioning](https://semver.org). The guarantees below hold **within a major version**:
a change that violates one of them requires a new major version.

While the project remains below `1.0.0`, a breaking change advances the minor version instead of implicitly
creating `1.0.0`, as described in [releasing.md](releasing.md). The guarantees below describe the discipline
applied to every change today; the `0.x` series does not weaken them, it only changes which version component a
deliberate break advances.

This promise covers the primary `people-context` distribution: the `people-context` and `people-context-mcp`
server commands and the `pctx` CLI.

Integration packages are not covered here, and they do not share one versioning rule:

- the Claude Code (`.claude-plugin`) and OpenClaw packages carry their own version, released on their own policy
  and independent of the server version;
- the Codex package (`.codex-plugin/plugin.json`) is currently **coupled** to the primary release version: the
  release automation rewrites it with every primary release and packaging tests assert the two match. Treat its
  version as a restatement of the server version, not as an independent signal about the plugin.

## MCP tools and responses

Within a major version:

- an existing tool is not removed or renamed;
- an existing required parameter is not removed, renamed, or given a narrower accepted type;
- a new parameter is optional and has a default that preserves the previous behavior;
- an existing response field is not removed and its meaning is not repurposed;
- a new response field is additive, and a client that ignores unknown fields keeps working.

Tool annotations describe behavior rather than disclosure. `readOnlyHint=true` tools stay read-only, and a tool
does not gain `destructiveHint=true` behavior without a major version. Operator-elevated tools
(`get_sensitive_person_context`, `export_data`) stay gated behind their process environment flags and stay absent
from ordinary discovery; a model cannot enable them through arguments.

Sensitivity gating is part of the contract, not an implementation detail: a response that is ordinary-disclosure
today does not start including sensitive or restricted material in a later minor version.

See [mcp-interface.md](mcp-interface.md) for the tool surface itself.

## Database and migrations

Migrations are **forward-only and additive**. Each migration is a numbered SQL file applied in ascending order
and recorded in SQLite's `PRAGMA user_version`; a released migration file is never edited or renumbered after
publication, and there is no down-migration path.

Within a major version, no migration drops or narrows a column, table, or index that shipped application code
reads. Adding tables, adding nullable or defaulted columns, adding indexes, and seeding reference vocabulary are
all in scope for a minor or patch release.

A newer release opens an older database and migrates it in place. The reverse is not promised: after a newer
release has migrated a database, an older release may refuse it or read it incompletely. Take a copy or a
`pctx sync push` bundle before downgrading.

## CLI

Within a major version:

- an existing command or subcommand is not removed or renamed;
- an existing flag is not removed, and its accepted values are not narrowed;
- a new flag is additive and defaults to the previous behavior, so an existing invocation keeps its meaning;
- a command that does not write today does not begin writing;
- the database-location resolution order documented in [cli.md](cli.md) is stable.

Exit statuses keep their meaning: zero for success and non-zero for refusal or failure. Confirmation prompts on
destructive commands are not removed, and a flag that skips a prompt (`--yes`) never becomes the default.

Human-facing output is not covered — see [Human-readable formats](#human-readable-formats).

## Machine-readable JSON

A JSON document that this repository documents as a stable interface carries an explicit `format` string and an
integer `version`, and follows the same additive rule as MCP responses: existing fields are not removed or
repurposed, and new fields are additive.

| Document | `format` | `version` | Produced by |
|---|---|---:|---|
| Portable dataset export | `people-context-export` | `1` | `pctx export`, `export_data` |
| Person brief | `people-context-brief` | `1` | `pctx brief --json` |
| Person index | `people-context-person-index` | `1` | `pctx list --json` |
| Bootstrap sync bundle | `people-context-sync-bundle` | `1` | `pctx sync push` |

The documents differ in how a field addition is classified, because only one of them is read back by this
project:

- **Portable dataset export**, **person brief**, and **person index** are producer-only: nothing in this
  repository consumes them, and external readers are expected to ignore unknown fields. A new field is additive
  and does not advance `version`; a removal or a repurposing does.

  The brief's disclosure labelling is part of its contract, not decoration: `disclosure.guidance` stays
  `ordinary` in every mode, and `disclosure.context` is `sensitive` only when the operator passed
  `--include-sensitive`. A later release does not start putting elevated records in a document whose labels say
  ordinary.
- **Bootstrap sync bundle** is read back by `pctx sync pull`, which validates the whole document — including every
  nested object — against an exact format and version with unknown fields forbidden. A reader from an older
  release therefore cannot tolerate *any* added field, so for this document a field addition is an incompatible
  change and advances `version`. The bundle is deliberately not additively extensible within a version.

That strictness is the point: a bundle a release does not fully understand fails closed before preview or writes
rather than restoring partial state. A bundle is restorable by releases that implement its declared version.

JSON emitted by a command that is not listed above, or not explicitly documented as a stable interface, is
human-facing output and may change.

## Human-readable formats

The following are deterministic but **not frozen**, including at `1.0`:

- CLI tables, prose, prompts, and log lines;
- the Obsidian vault export Markdown layout, file naming, and front-matter shape
  ([vault-export.md](vault-export.md)).

Vault export re-runs are byte-deterministic over unchanged data, which makes diffs meaningful, but the layout may
change in a minor release. Build integrations on the MCP tools or the versioned JSON documents above, not on
scraped Markdown or table output.

## What this promise does not include

- **A deprecation window.** The project has not historically operated one, and this document does not invent one.
  Removals and renames are major-version events; there is no promised number of releases during which a removed
  surface keeps working first.
- **Optional extras.** Behavior that depends on an optional dependency extra (for example semantic retrieval)
  is available only when that extra is installed, and its models and pins may change.
- **Third-party surfaces.** MCP client behavior, editor configuration formats, registry metadata schemas, and
  container base images follow their own upstream compatibility policies.
- **Integration packages.** The Claude Code and OpenClaw plugin manifests version independently; the Codex plugin
  manifest tracks the primary release version, as described under [Scope and versioning](#scope-and-versioning).

## Reporting a compatibility regression

A change that breaks one of the guarantees above without a major version is a bug. Report it through the issue
process in [CONTRIBUTING.md](../CONTRIBUTING.md), including the release that worked, the release that did not,
and the tool, command, or document involved.
