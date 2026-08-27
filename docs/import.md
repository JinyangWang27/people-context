# Import

This document describes the extract-and-stage import pipeline for bringing external content — email/mbox,
vCard, `.ics` calendar attendees, LinkedIn and Outlook contact exports, WhatsApp chat exports, and
agent-extracted notes candidates — into `people-context` without ever persisting raw source material.
Import was delivered in **M3** (see
[docs/roadmap.md](roadmap.md)); the `import_staging` table lives in the initial schema (see
[docs/data-model.md](data-model.md#import_staging)).

## Extract-and-stage model

Import is a four-step flow across four MCP tools (see [docs/mcp-interface.md](mcp-interface.md)):

```
   source/candidates      candidates staged           user review              committed
 (.eml/mbox/vCard/.ics)─►  in import_staging   ─────►  (accept/reject)  ─────►  real tables
                import_content              review_import            commit_import
```

1. **`import_content(source_type, content | path, self_sender)`** — the source adapter parses email headers,
   contact cards, calendar attendees, contact-export rows, or chat-export headers and
   deterministically extracts narrow candidates. Accepted `source_type` values are `email`, `mbox`, `vcard`,
   `ics`, `linkedin`, `outlook`, and `whatsapp`; anything else fails with `invalid_source_type`. The optional
   `self_sender` is an explicit chat-export label for the user (see
   [Self participation](#self-participation)). Candidates are written to
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

This extractor is also the contract the `pctx export-vcard` writer targets: every property that export emits is
read back here unchanged, which is why the export maps one `ORG`/`TITLE` pair, one full-date `BDAY`, and no
guessed `N` components. See [cli.md](cli.md#vcard-export).

## iCalendar (.ics) calendar attendees

`source_type="ics"` accepts exactly one UTF-8 content string or path and processes each `VEVENT`
independently using RFC 5545 line unfolding. Only attendee identities and a single start time are retained;
`SUMMARY`, `DESCRIPTION`, `LOCATION`, conference URLs, and every other free-text property are parsed
in-memory and discarded. The interaction summary is the fixed neutral string `Calendar event`.

- Only `ATTENDEE` properties carrying a non-empty `mailto:` address become external person candidates.
  Addresses are normalized and deduplicated across the whole file, differing `CN` display names accumulate as
  `other` aliases, and the address itself is staged as a `handle` alias. `ATTENDEE` lines nested inside a
  `VALARM` and non-attendee properties such as `ORGANIZER` are ignored.
- Addresses matching the stored self handles are excluded from both person candidates and participant
  references. An event with no external attendee after that filtering produces no interaction and is counted
  with a stable `no_external_attendee` skip reason; an empty participant list is never staged.
- Every accepted `VEVENT` yields at most one interaction candidate at its parsed start. The event `UID`, when
  present, is retained only as a narrow provenance reference (analogous to an email `Message-Id`).

`DTSTART` is converted to a timezone-aware UTC `datetime` using only explicit, portable forms; the host's
local timezone is never consulted:

- a UTC date-time ending in `Z` is parsed and kept in UTC;
- a local date-time with a resolvable `TZID` is attached to that `zoneinfo` zone and normalized to UTC;
- an all-day `VALUE=DATE` value is represented deterministically as `00:00:00Z` for that calendar day.

A floating date-time with neither `Z` nor `TZID` is skipped as `floating_dtstart_unsupported`. An unresolvable
`TZID` (`unknown_tzid`), a DST-ambiguous wall time (`ambiguous_dtstart`), a nonexistent spring-forward wall
time (`nonexistent_dtstart`), an impossible timestamp (`invalid_dtstart`), a missing `DTSTART`
(`missing_dtstart`), or an otherwise malformed property/event (`malformed_dtstart` / `malformed_event`) is
skipped with that stable one-based reason. `DTEND`, duration, recurrence expansion, and cancelled status are
out of scope.

## Outlook contacts CSV

`source_type="outlook"` accepts exactly one UTF-8 content string or path (a UTF-8 BOM is tolerated) and reads
only the canonical contact columns `First Name`, `Middle Name`, `Last Name`, `E-mail Address`, `Company`,
`Job Title`, and `Birthday`. All of those headers must be present; every other exported column — phone numbers,
postal addresses, `Web Page`, `Notes`, and the many locale-specific extras — is tolerated and never read, so
profile URLs and free text cannot reach a staged candidate.

- The person name is the non-empty `First Name`, `Middle Name`, and `Last Name` cells joined with single
  spaces. A valid `E-mail Address` becomes a `handle` alias. As for LinkedIn, an address is accepted only in
  the supported lowercase ASCII form, with a dot-atom local part that has no leading, trailing, or consecutive
  dots: case and compatibility forms are folded, but address characters are preserved, so an internationalized
  address such as `josé@example.com` is reported as `invalid_email` rather than silently rewritten into the
  distinct ASCII address `jose@example.com`. Stored self handles are compared the same way, so an accented self
  handle never swallows a genuinely distinct ASCII contact. A structurally malformed CSV — an unterminated
  quoted header, for example — fails with `invalid_csv`.
- `Company` plus `Job Title` produces an affiliation; either alone produces none.
- Rows are independent and reported with stable one-based indexes: a row with no name is skipped as
  `missing_name`, and a row whose non-empty email does not parse is skipped as `invalid_email`. A row whose
  email matches a stored self handle is omitted silently, exactly as for the other contact sources.
- Rows are coalesced only by normalized email; a second row for the same address contributes an `other` alias
  when its name differs. Rows without an email stay distinct even when their names match.
- Only unambiguous year-first `YYYY-MM-DD` and `YYYY/MM/DD` birthdays are accepted. A slash-separated
  Outlook birthday such as `1/23/1985` is locale ordered and cannot be resolved to a day and month without
  guessing, so it is not parsed. **Unlike the row-level reasons above, `invalid_birthday` reports a dropped
  field, not a dropped row**: the contact is still staged, only without a birthday fact.

## WhatsApp chat export

`source_type="whatsapp"` accepts exactly one UTF-8 content string or path holding a plaintext chat export.
Only the timestamp prefix and the sender label of each message are read. Everything after the sender
separator is message body — text, attachment file names, and system notices — and is never copied into a
candidate, a skip reason, a log record, or an error. The interaction summary is the fixed neutral string
`WhatsApp chat`.

- A line is treated as the start of a message only when it carries a complete, well-formed timestamp prefix in
  the bracketed form `[<date>, <time>] Sender: …` or the dash form `<date>, <time> - Sender: …`. The clock must
  be a real `HH:MM`/`HH:MM:SS` value — `0..23` hours, or `1..12` with a meridiem suffix, and `0..59` minutes
  and seconds — not merely time-shaped digits, so a body line quoting `[13/02/2025, 99:99] text:` stays a body
  continuation instead of turning quoted content into a sender. Every other line is likewise a continuation and
  is dropped without further inspection. Directional-isolate marks and narrow spaces are normalized first.
- Accepted date forms are ISO `YYYY-MM-DD` and the numeric `D/M/YY`, `D/M/YYYY`, and `D.M.YYYY` locale forms.
  A two-digit year is read as `20YY`. The meridiem is accepted as `AM`, `a.m.`, or the Spanish `a. m.`, whose
  internal space may be a narrow or non-breaking one.
- A WhatsApp export carries no UTC offset, so **only the calendar day is retained**, deterministically
  represented as `00:00:00Z` for that day — the same treatment `.ics` gives an all-day `VALUE=DATE` event.
  The host's local timezone is never consulted.
- Numeric day/month ordering is locale dependent and is inferred once for the whole file, never guessed per
  line. Only a header that is a real calendar date in exactly one reading counts as evidence, so an impossible
  header such as `31/02/2025` is reported as `invalid_timestamp` and contributes none. If the file offers no
  evidence, or offers contradictory evidence, every numeric-dated message is skipped as `ambiguous_date_order`;
  ISO-dated messages are unaffected.
- Skip entries use stable one-based indexes over *detected messages*: `invalid_timestamp` for an impossible
  calendar date, `ambiguous_date_order` as above, `no_sender` for a system notice or header without a sender
  separator, and `invalid_sender` for an implausibly long label. No reason ever carries text from the file.
- External senders are deduplicated by normalized sender identity: a display name keys on its normalized form
  and a phone number keys on its digits alone, ignoring spacing, punctuation, and an optional leading `+`. A
  phone-number label additionally stages its compact form as a `handle` alias, so `+1 555 123 4567`,
  `+15551234567`, and `15551234567` are one person. A label that differs beyond that is a different identity.
- Each calendar day with at least one external sender produces exactly one interaction candidate listing that
  day's external participants in first-appearance order.

### Self participation

The candidate contract has no self-participation field, so WhatsApp self participation is implicit exactly as
it is for email import: a message from the user produces no person candidate, and the user's label never
appears in `participant_refs`. A day containing only the user's own messages produces no interaction candidate
rather than an interaction with an empty or unknown participant list.

`ImportContent` derives the self labels from the stored self person's canonical name and every alias, and
`import_content` accepts an optional `self_sender` hint for export labels that are not stored aliases — such
as `You` or a bare phone number. The hint is matched by normalized name and, for phone-number labels, by
digits alone, so a differently formatted number — including a bare number matched against a `+`-prefixed
export label — still matches. `ImportExtractor.extract` carries these as
explicit optional `self_names` and `self_sender` keyword parameters; sources that identify people by address
accept and ignore them, and no source takes untyped keyword arguments.

## Agent candidate staging

`stage_candidates` uses extra-forbidden Pydantic discriminated models for person, interaction, affiliation,
fact, observation, trait, and relationship. Person `ref` values must be unique in the batch; all
`participant_refs`/`person_ref`/`from_ref`/`to_ref` values must resolve to one of them. Validation, matching,
staging-id assignment, reference rewriting, and the SQLite batch insert happen before or within one atomic
path, so invalid input leaves no partial rows. Dependencies on matched existing people can commit without
accepting the person candidate; dependencies on new people remain pending until that person is accepted.

### Agent-extracted knowledge (M17)

The three candidate types added in M17 exist so an agent reading a transcript, a call note, or an interview
can distil it without flattening everything into facts. They keep the project's epistemic distinction: a
**fact** is an explicit durable assertion, an **observation** is something that happened in this source, and a
**trait** is a generalization inferred from evidence. Each commits through the use case that already owns it —
`RecordObservation`, `RecordTrait`, `SetRelationship` — so an imported record carries the same validation,
provenance, audit, and changelog behavior as a directly recorded one.

- **Observation** — `person_ref`, `text`, optional `observed_at`, optional `sensitivity`. Omitting
  `observed_at` is how an agent says the source established no event time; commit then follows the released
  `RecordObservation` clock behavior rather than guessing one.
- **Trait** — `person_ref`, `category` (the existing `TraitCategory` values; M17 invents no second taxonomy),
  `value`, and — unlike a direct `record_trait` call — a **required** `evidence_note` and `confidence`. An
  inference lifted out of unstructured material is a weaker claim than one a person states directly, so the
  boundary refuses to let silence read as certainty or to accept an evidence-free generalization.
- **Relationship** — `from_ref`, `to_ref`, free-form `relationship_type`, optional `confidence`. Commit goes
  through `SetRelationship` unchanged: known vocabulary and synonyms canonicalize with inverse/symmetric
  endpoint semantics, a normalized but unregistered type stays a legal `uncategorized` edge, and only blank or
  non-word type text fails. Requiring registration would have made every currently legal uncategorized edge
  unimportable.

Relationship candidates are **ordinary-disclosure only**. The durable `Relationship` model and the graph reads
carry no sensitivity field, so there is nothing to enforce an elevated level with. Rather than accept a
candidate-only `sensitivity` that commit would discard — implying a protection that does not exist — the model
forbids it: an attempt to stage a sensitive or restricted edge fails rather than entering the graph
downgraded. Such a relationship stays out of People Context until relationship sensitivity exists as a durable
contract. Sensitive information that *is* enforceable still has a home in facts, observations, traits, and
interactions.

### Identity in an extraction batch

A staging request that uses one of the three new types also gets stronger identity handling, because the
question "is this somebody we already know?" has three answers, not two. Matching takes the union of the
active people that the candidate's canonical name and its handle aliases resolve to, and stages an explicit
`match_disposition`:

- `unmatched` — no active person matched; this is a genuinely new identity;
- `matched` — exactly one did, and `matched_person_id` names it;
- `ambiguous` — more than one did, so there is no authoritative id and a bounded `match_count` says how many.

An accepted ambiguous candidate **never falls through to `RememberPerson`**: "several people this could be" is
not evidence of a new one, and creating one would durably invent the duplicate the ambiguity warned about. It
stays in `unresolved_ids`, and every accepted dependant that needs it stays unresolved too. A later commit
resolves it only when the same deterministic match now yields exactly one active person — after a merge or a
correction, say — or the corrected candidate is re-staged. A unique hit on one token never short-circuits a
conflict on another.

### Bounds on an extraction request

An extraction request carries an agent's reading of unstructured material rather than rows out of a structured
export, so it is bounded from its first release. Any `stage_candidates` request containing at least one
observation, trait, or relationship candidate is limited to **500 candidates**, a normalized **128-character**
`source` label, **1 MiB** of canonically serialized candidate JSON, and **8 KiB for every string on every
candidate — including the legacy person, fact, interaction, and affiliation fields**, so a mixed batch cannot
smuggle a transcript through a released field. The new fields are tighter still: observation `text` at 4 KiB,
trait `value` and `evidence_note` at 2 KiB each, and relationship type and batch-local references at 256
characters. `source` is bounded as a privacy invariant as much as a resource one — `StageCandidates` copies
that label into every staged row and every later provenance record.

Every one of these is checked before validation and before any staging row exists, and a refusal names only
the limit: the rejected payload is untrusted extraction output and is never echoed back. The limits are
**conditional on purpose**. A request built only from the four released candidate types keeps the accepted
shape, source-label behavior, and pre-M17 matching it shipped with; adding candidate types does not
retroactively narrow anybody's working import.

## The `pctx import` command group

The same lifecycle is available to a person at the terminal through `pctx import stage`, `pctx import review`,
and `pctx import commit` (see [docs/cli.md](cli.md#import)). The CLI is a thin adapter over the use cases above:
it adds no source type, no candidate type, and no matching or commit policy of its own, and it keeps the review
gate as three separate commands because a staged batch is durable review state that may be inspected in a later
invocation.

What the CLI does add is a process boundary that is bounded from its first release, because a path typed at a
terminal is a much weaker promise than a file an MCP caller already chose:

- a source file is read under a **64 MiB** budget — a real bounded read rather than a reported size. The
  path-only `mbox` reader opens the file itself and scans all of it to build its table of contents before it
  yields a message, so the budget wraps the file object it reads through: the furthest offset reached is what is
  measured, which covers that scan and a mailbox still being appended to, not merely the headers parsed after it;
- one staging invocation produces at most **100,000 candidates** and at most **64 MiB** of persisted reviewable
  staging payload, measured as the UTF-8 bytes of the staged `source` plus candidate JSON. The candidate ceiling
  reaches extraction itself — a dense export packs a candidate into a few dozen bytes, so a file well inside the
  read budget can still expand into millions — and row building stops at the payload ceiling rather than
  measuring an already-complete batch;
- `review` and both `commit` forms first measure an existing batch in SQLite — `COUNT` plus byte-length
  aggregates, scanning one row past the ceiling and loading no candidate body — and refuse a batch beyond the
  same 100,000-row/64 MiB envelope before any full-batch read or mutation. An additive index on
  `import_staging(batch_id, created_at, id)` makes that measurement a seek rather than a scan of every batch ever
  staged, so the work does not grow with unrelated staging history.

Because staging applies the same measurement and the same ceilings, the CLI never creates a batch that its own
review or commit then refuses. The batches it can refuse are the ones an older uncapped `stage_candidates` call
created. Those ceilings are properties of this command: `import_content`, `review_import`, `commit_import`, and
`pctx init` keep their released, unbounded input and read contracts, and a resource refusal names only the limit,
never any part of the rejected source.

## Never persist raw content

The single hard rule for every importer: **raw source content is never persisted.** Only distilled
candidates plus a provenance reference are stored in `import_staging`, and only accepted candidates ever
reach the real tables:

- A candidate `Interaction` gets a short prose summary, not the message body. For the email/mbox importer
  this summary is the fixed neutral string `Email correspondence`, and for WhatsApp it is `WhatsApp chat`;
  the `Subject` header is attacker-controlled
  text that would otherwise be replayed into a future model's context, so it is deliberately not persisted (see
  [docs/privacy-and-safety.md](privacy-and-safety.md)). When a topical summary is wanted, an agent that has
  itself read the source can compose one and submit it through `stage_candidates`, taking responsibility for
  the wording; that path flows through the same review-and-commit approval as file imports.
- Provenance for imported records references the source narrowly — e.g. the email's `Message-Id` header and
  its date — enough to trace where a fact came from, without storing the message itself.
- Email addresses are stored as `aliases` of kind `handle` (see [docs/data-model.md](data-model.md#aliases))
  — this is treated as contact data, not raw content, since it is directly analogous to a phone number or
  a nickname the user would otherwise type in by hand.

## Importers are adapters

Import parsing lives in the source-specific modules under `adapters/importers/` and is dispatched by
`adapters/importers/router.py`. Those adapters produce candidates consumed by the models, staging, and workflow
modules under `app/imports/`. This means:

- The staging/review/commit flow, the `import_staging` schema, and the provenance rules are shared across
  every source type, including agent-side extraction.
- Adding a new source (CSV contacts, calendar exports) is purely additive — a new importer
  module plus, if needed, a new `source_type` value — and requires no change to `domain`, `app`'s use case
  contracts, or the review/commit tools. See
  [docs/architecture.md](architecture.md#how-new-transports-and-importers-slot-in) for how this fits the
  hexagonal layout generally.

## Status

Email/mbox arrived in **M3**; vCard and strict agent staging are delivered in **M4**; `.ics` calendar attendee and
LinkedIn Connections CSV imports arrived in **M9**. LinkedIn import requires the canonical `First Name`, `Last Name`,
`URL`, `Email Address`, `Company`, `Position`, and `Connected On` headers while allowing extra columns. It coalesces
rows only by normalized email, stages affiliations only when company and position are both present, and accepts
connected dates as `DD Mon YYYY` or `YYYY-MM-DD`. The export's notice preamble is discarded before the canonical
header; profile URLs, notes, and other free text are never staged.

Outlook contacts CSV and WhatsApp chat-export imports arrived in **M14**, bringing the accepted source values to
seven: `email`, `mbox`, `vcard`, `ics`, `linkedin`, `outlook`, and `whatsapp`.

Email extraction uses
only From/To/Cc/Reply-To, Subject, Date, and Message-ID headers;
correspondents are deduplicated by normalized address across a batch, self handle aliases are filtered, and
missing/invalid dates retain person candidates while omitting the interaction. Successful staging ids are
idempotent, and unresolved interactions remain pending for a later partial commit. Omitted interactions are
reported in deterministic input order through `skipped_message_ids` or `skipped_without_id`.


## M6 changelog and export boundary

Accepted import candidates reach ordinary application write use cases and therefore produce the same atomic
audit and replayable changelog entries as interactive writes. `import_staging` itself remains device-local
review state and is not captured. The version-1 `pctx export` envelope is unchanged in M6 and does
not include `devices`, `changelog`, or `sync_conflicts`; first-device bootstrap and changelog transfer require
the trusted snapshot/restore protocol deferred to M7.
