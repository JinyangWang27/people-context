# M16 — First-class CLI import workflow

Status: Planned. See [docs/roadmap.md](../roadmap.md#m16--first-class-cli-import-workflow).

## Motivation

The import pipeline is already a first-class application capability, but it is not a first-class CLI capability.
`ImportContent` can extract and stage email, mbox, vCard, iCalendar, LinkedIn, Outlook, and WhatsApp exports, and
`ReviewImport` / `CommitImport` already enforce explicit acceptance before durable writes. The runtime exposes those
use cases, yet normal `pctx` users cannot complete that workflow outside the vCard-specific onboarding path.

M16 closes that product gap without designing a second import architecture. The CLI becomes a thin process-boundary
adapter over the existing stage → review → commit lifecycle and exposes stable JSON alongside human output so both
people and automation can use the same workflow.

## Scope

In scope:

- a grouped `pctx import` CLI surface for staging a supported local export, reviewing one batch, and explicitly
  committing all or selected candidates;
- all seven source types already supported by `ImportExtractorRouter`;
- bounded source-file/candidate accumulation for file staging and bounded persisted-batch reads for the new CLI
  review/commit process boundary;
- stable versioned JSON documents for stage, review, and commit results;
- reuse of shared CLI import rendering/orchestration from `pctx init` where doing so removes duplicate behavior;
- CLI, runtime, importer-matrix, privacy, resource-bound, and E2E coverage.

Non-goals:

- new source types or candidate types;
- an embedded LLM, OAuth, live APIs, scraping, IMAP, CardDAV, or network access;
- raw-text semantic extraction (M17);
- stdin/raw-content import or agent-generated candidate ingestion (M17);
- batch listing, batch deletion, retention/expiry policy, or background folder watching;
- graph/read/write parity between CLI and MCP;
- schema migrations or changes to import matching/commit policy;
- retroactively narrowing released MCP `import_content`, `review_import`, `commit_import`, or `pctx init` behavior
  merely because the new CLI is bounded.

## Design principles

The existing application workflow is authoritative. M16 must preserve these invariants:

1. raw source content is parsed in memory and is never persisted by the import pipeline;
2. staging never mutates ordinary person/record tables;
3. commit occurs only for explicitly accepted candidate ids;
4. unresolved dependencies remain unresolved rather than being guessed;
5. import provenance, audit, changelog, matching, and transaction behavior continue through existing use cases;
6. human rendering is not a machine contract;
7. stable JSON is deterministic and additive under the M12 compatibility promise;
8. ordinary commands make no network request;
9. every newly exposed CLI import process boundary is bounded before a wrong/oversized file or legacy staging batch
   can become an unbounded in-memory parse, materialization, render, or commit selection.

The CLI should expose the lifecycle, not hide it behind a one-shot command. A staged batch is meaningful durable
review state, so the human or agent may review it in a separate invocation before deciding what to commit.

## CLI surface

```text
pctx import stage SOURCE PATH [--self-sender TEXT] [--json]
pctx import review BATCH_ID [--json]
pctx import commit BATCH_ID --all [--json]
pctx import commit BATCH_ID --accept CANDIDATE_ID [--accept CANDIDATE_ID ...] [--json]
```

`SOURCE` is restricted to the router's existing accepted values:

- `email`
- `mbox`
- `vcard`
- `ics`
- `linkedin`
- `outlook`
- `whatsapp`

Prefer one reusable accepted-source declaration beside the router rather than a second drifting CLI-only list.
`--self-sender` is passed through to the existing import use case; sources that do not use it continue to ignore it.

`--all` and `--accept` are mutually exclusive and one is required for `commit`. No additional confirmation prompt or
`--yes` is required: choosing `commit --all` or supplying explicit canonical candidate ids is already an explicit
approval action. M16 must not silently expand a selected candidate into other candidates or change dependency
semantics.

### Resource bounds

The new `pctx import stage` file surface is bounded from its first release. Binding limits are:

- at most **64 MiB** of source-file bytes per invocation;
- at most **100,000 staged candidates** produced from one source file;
- at most **64 MiB** of persisted reviewable staging payload for the resulting batch, measured as UTF-8 bytes of
  the staging fields that review/commit must materialize (at minimum `source` plus canonical candidate JSON).

These deliberately differ from M17's much smaller agent-candidate JSON limits: a structured export can contain many
thousands of narrow rows without being raw prose, while the boundary must still have a finite memory/work ceiling.
An operator with a larger export can split it and stage the parts separately.

The byte limit is a real read budget, not merely a `Path.stat()` advisory check. For adapters that currently call
`read_text()` / `read_bytes()`, use a bounded loader that reads at most the limit plus one byte and rejects the source
without parsing/staging when exceeded. For the existing path-only `mbox` seam, reject an already-oversized regular
file before iteration and enforce the same byte budget while processing so growth/races cannot turn the command into
an unbounded read. M18 later strengthens file identity/hash consistency; M16's requirement here is strictly the
resource ceiling.

The candidate-count and staged-payload limits must prevent unbounded accumulation rather than validating only after
an arbitrarily large list has already been built. Extraction/staging orchestration should stop/fail once either
budget would be exceeded, and `CandidateStager` (or the narrow M16 composition seam immediately before it) must reject
an over-budget result before durable `stage_batch`. Limit failures create no staging rows and diagnostics never echo
source/candidate content.

Do not impose these limits globally on pre-existing MCP `import_content`, direct application callers, or the released
`pctx init` vCard path as an incidental refactor. M16 may introduce a bounded loader/budget abstraction reused by the
new CLI without changing those older accepted-input contracts.

### Bounded review and commit of existing batches

The new CLI can also be pointed at a batch created through the released MCP `stage_candidates` path, whose legacy
candidate count/string/source fields were not globally capped. Therefore `pctx import review` and both commit forms
must perform a **storage-level preflight before calling any full-batch `list_batch` path**.

The CLI supports a batch only when both are true:

- row count is at most **100,000**;
- total persisted reviewable staging payload is at most **64 MiB** of UTF-8 bytes, including at minimum the staged
  `source` text and candidate JSON that the existing review/commit use cases materialize.

The SQLite staging adapter exposes a narrow bounded-size/count query that computes these totals in SQL (for example
using `COUNT` plus byte-length aggregates) without loading candidate JSON/source bodies into Python. It may stop once
the limits are known to be exceeded. Because a staging batch is append-closed after creation, a successful preflight
cannot race with later candidate growth; status changes do not increase its payload.

If either bound is exceeded, the CLI fails with a stable safe `batch_too_large_for_cli`-style error **before**
`ReviewImport.execute`, `CommitImport.execute`, or any equivalent full-batch materialization and performs no mutation.
This is a bound on the newly exposed CLI adapter, not a retroactive rejection added to the released MCP
`review_import` / `commit_import` tools. M16-created batches satisfy the same count/payload envelope at staging time,
so the CLI never creates a batch that its own review/commit commands subsequently reject as oversized.

### Human output

`import stage` prints the batch id, candidate count, existing non-sensitive skip counts/details, and the next
`pctx import review <batch-id>` command. It does not dump every candidate.

`import review` renders every candidate in deterministic staging order with:

- canonical candidate id;
- status;
- candidate type;
- concise review-safe proposed content;
- matched-existing person information when already present in the staged candidate.

The renderer may improve the vCard onboarding review output, but M16 does not add a TUI, pager, ephemeral numbered
identifiers, range syntax, or interactive selection language. Canonical candidate ids remain the durable selection
interface. Rendering the complete supported batch remains finite because the CLI preflight enforces both row-count
and persisted-payload ceilings before the full review use case runs.

`import commit` reports committed, unresolved, and already-committed/skipped ids and counts. `--all` obtains the
batch rows and passes all candidate ids through the existing application commit use case; idempotent/skipped
behavior remains application policy. The same preflight guarantees `--all` never materializes an unbounded legacy
batch through this CLI.

### Exit behavior

Follow existing CLI conventions. At minimum:

- unsupported source / malformed argument selection: exit 2;
- missing or unreadable source path: non-zero with a concise safe diagnostic;
- source >64 MiB, candidate count/payload budget exceeded, or existing batch outside the CLI read envelope: non-zero
  before durable staging/full-batch materialization with a concise bounded diagnostic;
- extraction failure: non-zero;
- no candidates: non-zero because no batch exists to review;
- unknown batch or candidate outside the batch: non-zero;
- successful stage/review/commit: exit 0.

Errors must never echo raw email/chat/calendar bodies or other discarded source text.

## Stable JSON contracts

All three commands support `--json`. Successful JSON mode writes exactly one JSON document to stdout; diagnostics
remain on stderr. Use typed document models and the project's canonical deterministic serialization conventions.

### `people-context-import-batch` v1

```json
{
  "format": "people-context-import-batch",
  "version": 1,
  "batch_id": "...",
  "candidate_count": 3,
  "skipped_message_ids": [],
  "skipped_without_id": 0,
  "skipped_cards": []
}
```

### `people-context-import-review` v1

```json
{
  "format": "people-context-import-review",
  "version": 1,
  "batch_id": "...",
  "candidates": [
    {
      "id": "...",
      "source": "import/linkedin",
      "status": "pending",
      "candidate": {}
    }
  ]
}
```

The `candidate` object preserves the existing review-safe staged representation instead of introducing a lossy
CLI-specific vocabulary.

### `people-context-import-commit` v1

```json
{
  "format": "people-context-import-commit",
  "version": 1,
  "batch_id": "...",
  "committed_ids": [],
  "unresolved_ids": [],
  "skipped_ids": []
}
```

Document all three in `docs/compatibility.md` when implemented so they receive the stable additive-field promise.

## `pctx init` reuse

`pctx init` currently hand-composes vCard import, review rendering, candidate-id validation, and commit. Refactor only
what is necessary so onboarding and the general import CLI share durable CLI workflow/rendering code instead of
maintaining two subtly different implementations.

Preserve all onboarding semantics established in M9:

- self is created before vCard extraction;
- own-card filtering/matching remains correct;
- no external vCard candidates is a harmless onboarding outcome;
- unknown candidate ids are rejected;
- only explicitly selected candidates commit;
- communication-philosophy behavior is unchanged.

The new M16 file/candidate/payload budgets and legacy-batch preflight apply to the `pctx import` group; they do not
silently change the released onboarding or MCP input/read contracts while sharing rendering/selection helpers.

## Migration needs

None. The bounded batch preflight is a read-port/SQLite-query extension over existing staging state, not a new table.

## Security and privacy

- Local file imports remain offline.
- Raw source bodies are never persisted or copied into CLI diagnostics.
- Oversized/wrong files and oversized legacy staging batches fail under fixed byte/work budgets before staging or
  full-batch materialization; limit errors do not include rejected payload text.
- Review output contains distilled personal data and documentation must warn before redirecting/sharing it.
- Stable JSON is also personal data and is not a sanitized export format.
- WhatsApp body exclusion and calendar/LinkedIn free-text exclusion remain unchanged importer invariants.
- The CLI never constructs shell commands from personal values.

## Testing strategy

- Parser tests cover all seven source choices, unknown source rejection, mutually exclusive commit selectors, and a
  required commit selector.
- Resource tests cover exactly-at/over **64 MiB** for byte/text sources, a path-only `mbox` over/growing past the
  byte budget, exactly-at/over **100,000 candidates**, exactly-at/over **64 MiB staged reviewable payload**,
  early-stop/no-unbounded-accumulation behavior, and prove no staging rows survive any staging resource rejection.
- Existing-batch boundary tests create legacy MCP batches at/over the **100,000-row** and **64 MiB persisted-payload**
  ceilings and prove the SQLite preflight runs before `list_batch`; oversized `review`, `commit --all`, and selected
  commit fail safely without materializing payloads or mutating state.
- Regression tests prove those M16 CLI limits/preflights do not retroactively narrow legacy MCP `import_content`,
  `review_import`, `commit_import`, or `pctx init`.
- CLI/runtime tests cover representative LinkedIn, ICS, WhatsApp self-sender, missing path, no candidates, known and
  unknown batch, partial acceptance, `--all`, unresolved dependencies, and already-committed rows.
- Human-output tests assert useful batch/review information and raw-content sentinel absence.
- JSON tests pin literal format/version, deterministic fields, exactly-one-document stdout, and the unchanged staged
  candidate representation.
- Existing importer parser tests remain at the adapter layer; do not duplicate every format edge case at CLI level.
- Onboarding regression tests prove the shared CLI workflow does not change M9 safety behavior.
- Include at least one installed/subprocess CLI round trip through stage → review → commit → list.
- `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`, and `uv build` are fully green.

## Implementation decisions

- Stage/review/commit remain separate commands because the review gate is a product invariant, not incidental UX.
- `--all` is explicit acceptance and does not require a second confirmation prompt.
- The new structured-file CLI has a 64 MiB source budget, 100,000-candidate budget, and 64 MiB staged-payload
  budget; these are process-boundary safety limits, not a global narrowing of older import APIs.
- Review/commit additionally preflight existing batches at the storage layer against the same 100,000-row/64 MiB
  payload envelope before any full-batch materialization. Oversized legacy MCP batches remain valid for their
  released MCP surfaces but are explicitly unsupported by the new CLI adapter.
- Canonical candidate ids remain the only selection ids; richer bulk-review UX should be driven by observed usage.
- Agent-generated strict candidate ingestion is intentionally deferred to M17 so M16 stays an adapter over already
  structured import sources.
