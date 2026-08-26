# M20 — Streaming importer parsing under a parser-work budget

Status: Planned. See [docs/roadmap.md](../roadmap.md#m20--streaming-importer-parsing).

## Motivation

M16 gave the `pctx import` boundary four ceilings: 64 MiB of source bytes, 100,000 staged candidates, 64 MiB of
persisted reviewable staging payload, and the same row/payload envelope for reading an existing batch. Those
ceilings hold. Candidates are accounted as they accumulate, including inside a single calendar event's attendee
fan-out and a single message's recipient fan-out, so no source can build an unbounded *candidate* list.

What no ceiling covers is the work between those two points: every extractor turns the whole in-budget source
into intermediate Python objects *before the first candidate exists*, and a candidate budget cannot meter work
that happens before there are candidates to count. Three review findings on
[#95](https://github.com/JinyangWang27/people-context/pull/95) named three instances of this, and they are three
symptoms of one property rather than three defects:

- `email.py` — `list(mbox)` materializes a parsed `Message` for every message in the mailbox;
- `email.py` — `getaddresses()` builds every address tuple in one message's `To`/`Cc` before any is inspected;
- `whatsapp.py` — `_detect_messages` builds a `_Message` per detected line and `_resolve_dates` adds
  deferred-resolution state for every one of them.

An audit for this spec found the same shape in the remaining sources, so M20 addresses the property rather than
the reported sites.

This is not a regression introduced by M16 — before it, every one of these parsers read its file with no size
cap at all, and M16 strictly improved them by putting 64 MiB in front. But "bounded by a constant multiple of
64 MiB" is a weaker promise than an import command advertising a 100,000-candidate ceiling implies, and the
multiplier is not small: 64 MiB of short mbox messages is on the order of a million `email.message.Message`
objects. M20 closes the gap deliberately, once, across every source and both surfaces.

## Current materialization inventory

Each source's whole-file intermediates, as of the M16 implementation. Every entry is bounded by the caller's
source budget and by nothing else.

| Source | Whole-file intermediates built before the first candidate |
|---|---|
| `email` | `getaddresses()` over each header list |
| `mbox` | `list(mbox)` — every parsed `Message`; `mailbox`'s own table of contents |
| `vcard` | `_unfold_lines` → `list[str]`; `_split_cards` → every card's line list |
| `ics` | `_unfold_lines` → `list[str]`; `_iter_events` → every `_Event` |
| `linkedin` | `_csv_from_canonical_header` → `text.splitlines(keepends=True)`; the decoded source text |
| `outlook` | the decoded source text behind `io.StringIO` |
| `whatsapp` | `_detect_messages` → every `_Message`; `_resolve_dates` deferred state |

`linkedin` and `outlook` already stream *rows* through `csv.DictReader`; their remaining cost is the decoded
text and, for `linkedin`, one extra whole-file line list.

## Scope

In scope:

- a narrow parser-work budget seam and a bounded streaming source reader, used by every extractor;
- converting all seven sources to stream their records rather than materialize whole-file intermediates;
- extending the resulting bound to the released MCP `import_content` path, without narrowing which inputs it
  accepts;
- preserving every documented extraction semantic, or renegotiating one explicitly and in writing;
- resource, semantic-equivalence, and regression coverage.

Non-goals:

- new source types, candidate types, or schema changes;
- changing the M16 source-byte, candidate, staged-payload, or batch-read ceilings, or the values they take;
- changing which candidates a given source yields, their order, or their skip reasons;
- retroactively adding *rejection* thresholds to released MCP inputs (see below);
- network access, embedded models, or raw-content retention of any kind.

## Design principles

1. **Streaming fixes memory without narrowing input.** The distinction is the heart of this milestone. A
   rejection cap on the MCP path would refuse sources `import_content` accepts today, which the compatibility
   promise forbids within a major version. Streaming bounds the *work* while accepting exactly the same inputs,
   so it is the mechanism M20 uses for the released surface. Explicit refusal stays a property of the newer
   `pctx import` boundary and its existing ceilings.
2. **Extraction output does not change.** For any source that both the old and new parser accept, the staged
   candidates, their order, their refs, and the skip reasons and indexes must be identical. This is the
   milestone's primary correctness obligation and the reason for the equivalence tests below.
3. **The budget meters retained records, not bytes read.** Byte budgets already exist and belong to the caller.
   What is missing is a ceiling on how many parsed records a parser holds live at once.
4. **One seam, seven users.** A single bounded reader and budget, in `adapters/importers/`, reused by every
   extractor — not seven ad-hoc rewrites.
5. **Skips stay free.** A record that is skipped must not retain memory proportional to the number of skips. The
   WhatsApp finding is exactly this case: candidate-free malformed input currently accumulates a `_Message` per
   line and produces no candidate to account for.
6. Raw source content is still never persisted, logged, or echoed, and refusals still name only limits.

## The parser-work budget

Add a narrow budget alongside the existing `SourceReadBudget` and `CandidateBudget`:

- it bounds **live retained parsed records**, not cumulative records seen, so a million streamed-and-discarded
  lines cost O(1);
- its default is `None`, meaning unbounded, so an unbudgeted caller keeps today's behavior exactly;
- `pctx import` passes a concrete value through the existing `ImportBudget`; the MCP path passes `None` and
  relies on streaming alone for its bound.

The concrete ceiling is an implementation decision for M20.1, chosen so that no source that fits the existing
64 MiB/100,000-candidate envelope can reach it. It is a backstop against a parser retaining more than it should,
not a second input limit.

## Streaming source reader

Extractors currently receive a fully decoded `str` from `read_source_text`. M20 adds a line/record-oriented
counterpart that decodes incrementally under the same byte budget and the same encoding rules, yielding units
without holding the whole file. `read_source_text` remains for callers that genuinely need the whole text.

The reader must preserve the decoding behavior M16 established: universal-newline handling identical to
`Path.read_text(encoding=...)`, BOM handling for `utf-8-sig`, and the `undecodable_source` refusal — with the
added requirement that a decoding failure is reported at the same point in the stream regardless of chunking.

## Per-source conversions

- **vcard, ics** — line-oriented with no cross-file state. Unfold and split lazily; emit each card/event as it
  completes. The one-based `index` in every skip reason must keep counting from the same origin.
- **linkedin, outlook** — already stream rows; feed `csv.reader` from the streaming reader and drop
  `linkedin`'s extra whole-file `splitlines`. The canonical-header preamble scan must stay bounded.
- **email** — pass the budget into `_correspondents` so one message's address expansion is metered while it is
  built, rather than after `getaddresses` returns.
- **mbox** — stop calling `list(mbox)`. The mailbox must stay open while the extraction loop consumes it, so
  ownership of the handle moves into the extractor's own scope; the existing `MeteredSourceFile` byte metering
  must continue to wrap the file for the whole iteration, including `mailbox`'s table-of-contents scan.
- **whatsapp** — the hard case, below.

## WhatsApp: preserving whole-file locale inference

`_resolve_dates` infers numeric day/month ordering **from the whole file** by documented M14 design: an export
whose ordering cannot be resolved unambiguously is skipped as a unit. That is why it currently retains every
`_Message`. Naively streaming it would change what the importer extracts, which principle 2 forbids.

Two acceptable resolutions, in preference order:

1. **Bounded two-pass scan (preferred).** The first pass streams the source and accumulates only ordering
   evidence — a small fixed set of booleans and counts, O(1) in file size — and the second pass streams it again
   and emits candidates using the resolved ordering. This preserves the documented semantics exactly, at the
   cost of reading the source twice. The second read must re-apply the same byte budget and must tolerate the
   source having changed between passes by refusing rather than mixing two versions.
2. **Explicit renegotiation.** If the two-pass cost is judged unacceptable, the whole-file inference may be
   narrowed to a documented bounded prefix — but only as a deliberate, written behavior change to M14 semantics,
   with `docs/import.md` and the M14 spec updated and the skip-reason contract restated. It is not a silent
   consequence of a memory fix.

M20.3 must state which it chose and why.

## MCP compatibility

`import_content` keeps accepting exactly what it accepts today. The streaming conversion changes how much memory
it uses, not which sources succeed, which candidates they produce, or which errors they raise. No parameter is
added, narrowed, or given a new default, so nothing here is a compatibility event under
[compatibility.md](../compatibility.md). Regression tests must prove that a source accepted by
`import_content` before the change is accepted after it, with byte-identical staged candidates.

## Migration needs

None. No schema, table, index, or migration is involved.

## Security and privacy

- Streaming does not change the no-raw-content rule: intermediates are still discarded and never persisted,
  logged, or echoed.
- A two-pass WhatsApp scan reads the source twice and retains nothing between passes.
- Resource refusals continue to name only numeric limits, never a byte, line, or field of the rejected source.
- Reading incrementally must not widen what a diagnostic can contain: no partial line, chunk, or decode context
  may reach an error message.

## Testing strategy

- **Equivalence** is the primary obligation: for a fixture corpus covering all seven sources, the streamed
  parser must produce byte-identical staged candidates, ordering, refs, skip reasons, and one-based indexes to
  the current implementation. Build this as a table-driven comparison so every source is covered by construction.
- **Retention**: prove a candidate-free source — the WhatsApp malformed-header case, an mbox of messages with no
  external correspondents — completes with live retained records bounded, rather than proportional to input size.
  Assert against the budget seam rather than by measuring memory.
- **Byte budget continuity**: the M16 mbox metering tests must still pass with the mailbox held open across the
  extraction loop, including the scan-metering and exact-ceiling boundary cases.
- **Skips stay free**: a million skipped records must not retain a million objects.
- **MCP regression**: sources accepted by `import_content` today are still accepted, with identical output and
  identical errors; no new rejection appears on that path.
- **Decoding**: `undecodable_source` is raised for the same inputs as before, at the same point, independent of
  chunk boundaries.
- **WhatsApp semantics**: the existing M14 ordering-inference tests pass unchanged under the chosen resolution;
  if option 2 was taken, they are replaced deliberately and the docs updated in the same PR.
- `uv run ruff check .`, `uv run mypy`, `uv run pytest -q` green; `uv build` if any packaged surface moves.

## Implementation decisions

- Streaming, not rejection, is what bounds the released MCP path, because rejection would narrow accepted input.
- The parser-work budget defaults to `None` so every existing caller is unaffected, matching how
  `max_source_bytes` and `max_candidates` were introduced in M16.
- The M16 ceilings and their values are unchanged; M20 adds no new user-visible limit to `pctx import`.
- Extraction output is frozen for this milestone. The only permitted behavior change is the explicitly
  renegotiated WhatsApp option, and only with documentation updated in the same pull request.
- `mbox` is expected to be the largest single win and the most intrusive change, which is why it is its own
  pull request rather than folded into the shared seam.
