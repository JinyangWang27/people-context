# Import

This document describes the extract-and-stage import pipeline for bringing external content — email first —
into `people-context-mcp` without ever persisting raw source material. Import is planned for **M3** (see
[docs/roadmap.md](roadmap.md)); the `import_staging` table already exists in the M0 schema (see
[docs/data-model.md](data-model.md#import_staging)).

## Extract-and-stage model

Import is a four-step flow, split across three MCP tools (see [docs/mcp-interface.md](mcp-interface.md)):

```
   source file            candidates staged           user review              committed
  (.eml / mbox)   ─────►   in import_staging   ─────►  (accept/reject)  ─────►  real tables
                import_content              review_import            commit_import
```

1. **`import_content(source_type, content | path)`** — an importer adapter
   (`adapters/importers/`) parses the source file, and the client LLM or a heuristic extractor proposes
   candidate person/alias/fact/interaction-summary records from it. Candidates are written to
   `import_staging` as `candidate_json`, grouped by `batch_id`. The raw source is parsed **in-memory only**
   and discarded once candidates are extracted — it is never written to any table.
2. **`review_import(batch_id)`** — returns the staged candidates for a batch so the user (or an agent acting
   on the user's behalf) can inspect exactly what would be written before anything touches the real tables.
3. **`commit_import(batch_id, accepted_ids)`** — writes only the accepted candidates into the real tables
   (`persons`, `aliases`, `facts`, `interactions`, etc.), tagged with provenance `source: "import/<type>"`
   (e.g. `"import/email"`). Rejected candidates are simply never committed.

Nothing enters the real dataset without an explicit accept step — this is the same approval-gating
philosophy applied to all writes (see [docs/privacy-and-safety.md](privacy-and-safety.md)), just staged one
level earlier because the source material (a whole mailbox) is much less trustworthy than a single explicit
`remember_person` call.

## Email first (.eml / mbox)

The first supported source type is email, read from local `.eml` files or `mbox` exports. Import is
**file-based** in v1 — there is no OAuth flow, no live IMAP/API connection, and no background sync with an
email provider. The user exports or points at files they already have locally; the importer never reaches
out over the network.

Rationale:

- Keeps the tool local-first and avoids OAuth scope creep and the associated security surface.
- Keeps the no-raw-content rule enforceable: a file-based importer's entire lifetime (open, parse, extract,
  discard) is a single, bounded, offline operation, easy to reason about and to audit.

## Never persist raw content

The single hard rule for every importer: **raw source content is never persisted.** Only distilled
candidates plus a provenance reference are stored in `import_staging`, and only accepted candidates ever
reach the real tables:

- A candidate `Interaction` gets a short prose summary, not the message body.
- Provenance for imported records references the source narrowly — e.g. the email's `Message-Id` header and
  its date — enough to trace where a fact came from, without storing the message itself.
- Email addresses are stored as `aliases` of kind `handle` (see [docs/data-model.md](data-model.md#aliases))
  — this is treated as contact data, not raw content, since it is directly analogous to a phone number or
  a nickname the user would otherwise type in by hand.

## Importers are adapters

Import parsing lives entirely under `adapters/importers/`, one module per source format, each producing the
same candidate shape consumed by the shared `Importing` use case (`app/importing.py`). This means:

- The staging/review/commit flow, the `import_staging` schema, and the provenance rules are shared across
  every source type.
- Adding a new source (vCard/CSV contacts, notes apps, calendar exports) is purely additive — a new importer
  module plus, if needed, a new `source_type` value — and requires no change to `domain`, `app`'s use case
  contracts, or the review/commit tools. See
  [docs/architecture.md](architecture.md#how-new-transports-and-importers-slot-in) for how this fits the
  hexagonal layout generally.

## Status

Planned for **M3**, alongside `merge_people`, `forget`, `export_data`, and the full CLI curation command set
(see [docs/roadmap.md](roadmap.md)). In M0, `import_content`, `review_import`, and `commit_import` are
registered as stub MCP tools returning `{"status": "not_implemented", "planned_milestone": "M3"}`.
