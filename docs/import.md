# Import

This document describes the extract-and-stage import pipeline for bringing external content — email/mbox,
vCard, and agent-extracted notes candidates —
into `people-context-mcp` without ever persisting raw source material. Import was delivered in **M3** (see
[docs/roadmap.md](roadmap.md)); the `import_staging` table lives in the initial schema (see
[docs/data-model.md](data-model.md#import_staging)).

## Extract-and-stage model

Import is a four-step flow across four MCP tools (see [docs/mcp-interface.md](mcp-interface.md)):

```
   source/candidates      candidates staged           user review              committed
 (.eml/mbox/vCard) ─────►  in import_staging   ─────►  (accept/reject)  ─────►  real tables
                import_content              review_import            commit_import
```

1. **`import_content(source_type, content | path)`** — the source adapter parses email headers or vCards and
   deterministically extracts narrow candidates. Candidates are written to
   `import_staging` as `candidate_json`, grouped by `batch_id`. The raw source is parsed **in-memory only**
   and discarded once candidates are extracted — it is never written to any table. Its result includes
   `skipped_message_ids` for dateless messages with IDs, `skipped_without_id` for dateless messages without
   IDs, and one-based `skipped_cards` entries for independently skipped vCards.
2. **`stage_candidates(source, candidates)`** — an agent can submit the same strict candidate vocabulary
   after extracting concise facts from user-provided notes. The source becomes `import/agent:<source>`; raw
   note text is not a candidate field and is never staged.
3. **`review_import(batch_id)`** — returns the staged candidates for a batch so the user (or an agent acting
   on the user's behalf) can inspect exactly what would be written before anything touches the real tables.
4. **`commit_import(batch_id, accepted_ids)`** — writes only accepted people and resolvable interactions,
   affiliations, and facts,
   tagged with provenance `source: "import/<type>"` (e.g. `"import/email"`). An accepted interaction whose
   new-person references were not accepted stays pending and is returned in `unresolved_ids`; it can be retried later.

Nothing enters the real dataset without an explicit accept step — this is the same approval-gating
philosophy applied to all writes (see [docs/privacy-and-safety.md](privacy-and-safety.md)), just staged one
level earlier because the source material (a whole mailbox) is much less trustworthy than a single explicit
`remember_person` call.

## Email and mbox

The first supported source type is email, read from local `.eml` files or `mbox` exports. Import is
**file-based** in v1 — there is no OAuth flow, no live IMAP/API connection, and no background sync with an
email provider. The user exports or points at files they already have locally; the importer never reaches
out over the network.

Rationale:

- Keeps the tool local-first and avoids OAuth scope creep and the associated security surface.
- Keeps the no-raw-content rule enforceable: a file-based importer's entire lifetime (open, parse, extract,
  discard) is a single, bounded, offline operation, easy to reason about and to audit.

Messages with external correspondents but invalid/missing Date still retain person candidates. If a
Message-ID exists it is appended to `skipped_message_ids`; otherwise `skipped_without_id` increments.

## vCard 3.0/4.0

`source_type="vcard"` accepts exactly one UTF-8 content string or path and supports multiple cards, standard
line unfolding, grouped/parameterized properties, quoted-printable values, and escaped separators. Cards
are independent: one malformed card never blocks valid neighbors, including in large batches.

- Missing `FN` → `missing_fn`; unsupported `VERSION` → `unsupported_version`; structural parse failure →
  `malformed_card`. Reports use stable one-based card indexes and never echo raw field values.
- `FN` is canonical. A distinct structured `N` becomes an `other` alias, `NICKNAME` values become
  `nickname` aliases, and every `EMAIL` becomes a `handle` alias. Existing people match by emails first,
  then names.
- `ORG` plus `TITLE` produces an affiliation using the first organization component. Nonempty `BDAY`
  produces a `birthday` fact.
- `NOTE`, `PHOTO`, `ADR`, `TEL`, and X-properties are discarded before decoding/staging. If every card is
  skipped, no batch is created and `no_candidates` carries `skipped_cards`.

## Agent candidate staging

`stage_candidates` uses extra-forbidden Pydantic discriminated models for person, interaction, affiliation,
and fact. Person `ref` values must be unique in the batch; all `participant_refs`/`person_ref` values must
resolve to one of them. Validation, matching, staging-id assignment, reference rewriting, and the SQLite
batch insert happen before or within one atomic path, so invalid input leaves no partial rows. Dependencies
on matched existing people can commit without accepting the person candidate; dependencies on new people
remain pending until that person is accepted.

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

Import parsing lives in `adapters/email_import.py` and `adapters/vcard_import.py`, which produce candidates consumed
by the shared app-layer import use cases. This means:

- The staging/review/commit flow, the `import_staging` schema, and the provenance rules are shared across
  every source type, including agent-side extraction.
- Adding a new source (CSV contacts, calendar exports) is purely additive — a new importer
  module plus, if needed, a new `source_type` value — and requires no change to `domain`, `app`'s use case
  contracts, or the review/commit tools. See
  [docs/architecture.md](architecture.md#how-new-transports-and-importers-slot-in) for how this fits the
  hexagonal layout generally.

## Status

Email/mbox arrived in **M3**; vCard and strict agent staging are delivered in **M4**. Email extraction uses
only From/To/Cc/Reply-To, Subject, Date, and Message-ID headers;
correspondents are deduplicated by normalized address across a batch, self handle aliases are filtered, and
missing/invalid dates retain person candidates while omitting the interaction. Successful staging ids are
idempotent, and unresolved interactions remain pending for a later partial commit. Omitted interactions are
reported in deterministic input order through `skipped_message_ids` or `skipped_without_id`.
