# Obsidian vault export

M7 provides a human-operated CLI export for a relationship-oriented Obsidian vault:

```bash
uv run people-context export-vault --output /path/to/vault
```

There is no MCP vault-export tool in v1. Writing an arbitrary directory from an MCP call requires a separate
risk and approval design.

## Ownership marker and destructive safety

Every successful export writes `.people-context-vault` at the output root. The destination must be one of:

- nonexistent;
- an empty directory; or
- a directory already containing the marker file.

A non-empty unmarked directory is refused before any file changes. In a marked directory, the exporter owns
exactly three generated paths: `.people-context-vault`, `People/`, and `Organizations/`. Re-export removes and
regenerates only those paths. Obsidian state such as `.obsidian/`, user-created notes, attachments, and every
other root path are preserved unchanged. Files manually placed inside `People/` or `Organizations/` are treated
as generated output and are removed on the next export.

The output is deterministic: ordering is stable and generated files contain no export timestamps. Re-exporting
unchanged data produces byte-identical marker, person-note, and organization-note files; preserved user files
are outside that determinism guarantee because the exporter does not manage them.

## Layout and filenames

```text
.people-context-vault
People/<Canonical Name>.md
Organizations/<Organization Name>.md
```

Organization notes are graph hubs. Filenames replace path separators, control characters, and characters
illegal on Windows/macOS; leading dots and trailing dots/spaces are removed. Unicode is preserved, so names
such as `小明` remain `小明.md`. When two names sanitize to the same filename, each receives
` (<first 6 characters of ULID>)`.

Wikilinks always use the same collision-aware sanitized note stem as the target file.

## Front matter

Person notes use Obsidian's native `aliases` key and stable tags/id:

```yaml
---
aliases:
  - "Xiaoming"
tags: [people-context/person]
people-context-id: 01...
---
```

Organization notes use `aliases: []`, `tags: [people-context/organization]`, and their organization id.

## Person-note body

The body contains:

- summary;
- active relationships as perspective-rendered Dataview inline fields and plain wikilinks;
- active affiliations as organization wikilinks, role, and period prose;
- durable facts, including validity prose when bounded;
- active reminders.

Relationship example:

```markdown
- reports_to:: [[Chen Wei]]
```

Affiliation example:

```markdown
- role:: [[Acme]] — Engineer; active since 2025-01-01
```

The same line is useful to Obsidian's vanilla graph view and to Dataview. Organization notes link back to their
active people with the same `role::` convention.

Observations, traits, and interactions are deliberately not exported in v1. The vault is the relationship
graph plus durable facts and reminders, not a full dossier. Soft-deleted people are excluded. The `is_self`
person is included and typically becomes the graph hub.

## Sensitivity

By default, facts marked `sensitive` or `restricted` are excluded. Include them only through explicit operator
intent:

```bash
uv run people-context export-vault --output /path/to/vault --include-sensitive
```

**Exporting moves data outside the server's disclosure controls.** Once Markdown files exist, filesystem
permissions, backup/sync software, Obsidian plugins, and any service that indexes the vault govern disclosure.
The server can no longer enforce MCP sensitivity gates or forget semantics on copied files. Protect or delete
exported files separately.
