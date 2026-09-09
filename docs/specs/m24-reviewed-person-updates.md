# M24 — Conservative CV update review

Status: Planned. See [docs/roadmap.md](../roadmap.md#m24--conservative-cv-update-review).

## Purpose

Treat a newer CV as additional evidence about a person, not a replacement profile. Build on
[M22](m22-source-grounded-person-capture.md) capture and the delivered
[consolidation](../mcp-interface.md#get_consolidation_context) and
[fact-supersession](../mcp-interface.md#supersede_fact) operations to make proposed changes specific and reviewable.
Preserve historically correct knowledge, source attribution, and uncertainty throughout the workflow.

## Planned PRs

### M24.1 — Include affiliations in bounded consolidation context

Add an affiliation collection to the existing consolidation-context read. Include stable affiliation/person and
organization identifiers, organization name, role, validity bounds, recording/creation time as available, confidence,
stored provenance, and available source-session references. Keep assertion attribution (`stated_by`) distinct from
the import receipt; missing provenance must not be invented.

Follow the current per-collection `limit` contract: default **50**, range **1–200**, with an additive
`affiliations_truncated` flag. Use deterministic newest-first ordering by asserted `valid_from`, falling back to
`created_at`, with id as the final tie-breaker. Bounds apply to the underlying read as well as the returned result;
do not load an unbounded professional history to produce a bounded response.

Preserve existing facts, traits, observations, signals, their bounds, and JSON meanings. Existing not-found results
gain only an empty affiliation collection and its additive metadata. Affiliations have no sensitivity field and
remain ordinary-disclosure records; adding this collection must not widen protected facts or evidence. No new
automatic affiliation-conflict signal or semantic comparison engine is required.

### M24.2 — Add the reviewed CV-update workflow

Extend shared agent guidance and fictional workflow examples:

1. Resolve one identity unambiguously. An ambiguous name or inconsistent identity evidence remains unresolved;
   do not create a replacement person or merge people just to continue the CV update.
2. Read incoming material using M22 capture rules, then read available person, timeline, and consolidation context.
   State unreadable sections, missing evidence, and truncation. Use available follow-up reads where helpful; these
   bounded interfaces do not promise exhaustive pagination or complete coverage.
3. Compare incoming claims with readable records and classify each proposal using the outcomes below. Include the
   target record ids, before/after meaning, supporting attribution/source references, date precision, uncertainty,
   and concrete supported operation and arguments for every proposed mutation.
4. Present proposals for explicit acceptance before writes. Acceptance identifies particular actions; agreement to
   read a CV or inspect a proposal is not approval of every possible maintenance change. New candidates retain the
   existing stage → review → commit gate.
5. Immediately before applying accepted actions, reread affected records and identity to check the reviewed target
   still matches, including validity and the fields relevant to the proposal. If it changed, disappeared, or can no
   longer be read, stop that action and obtain renewed review. Do not silently retarget an accepted mutation.
6. Apply only supported, accepted operations, then reread affected records and report the result. Report completed,
   failed, skipped, and unresolved actions separately when relevant, retaining ids needed to assess the outcome.

### Proposal outcomes

| Outcome | Required reasoning and action |
|---|---|
| Add | A supported claim is not represented in the available context; stage the attributed claim for review. Qualify the conclusion when reads are incomplete. |
| Already represented | Explain the matching stored claim and any source limitations; do not create another row or raise confidence merely because the source repeats it. |
| Correct an error | Evidence establishes that stored data was wrong at the time; propose only fields supported by `correct_record`, with the prior meaning and correction explicit. |
| Record a supported temporal transition | The old assertion was historically correct and a supported operation can preserve it while recording the known change; use the narrow fact-supersession contract where applicable. |
| Leave unresolved | Identity, evidence, dates, readable coverage, or mutation support is insufficient; describe the missing basis and retain existing records. |

### Conservative comparison and mutation rules

- Omission never implies deletion or the end of employment, education, membership, a relationship, or another claim.
  A shorter CV is not evidence that omitted history stopped being true.
- Concurrent jobs, multiple skills, and different traits are not inherently contradictory. Existing deterministic
  consolidation signals identify comparisons, not verdicts about which claim to discard.
- Repetition may be correlated or copied; another source receipt does not automatically increase confidence or
  constitute independent evidence. Preserve conflicting assertions when the evidence cannot settle them.
- Preserve exact dates in validity fields and approximate dates in concise claim text. Never invent an effective
  date from a year/month, the newer CV's date, or the day the document was imported.
- `supersede_fact` requires a known effective date, strictly after any old `valid_from`, not after any old `valid_to`,
  and with a representable prior day. It preserves the old person, predicate, value, provenance, and recorded time;
  closes the old period on the preceding day; and gives the replacement the original `valid_to`. Do not widen a
  bounded assertion or use supersession to change its person, predicate, or independent end date.
- Use `correct_record` for actual errors only. Do not overwrite a historically correct value, role, or validity
  period to simulate a transition. There is no generic affiliation/relationship supersession operation: unsupported
  transitions remain unresolved. Separately supported new claims may still be proposed without pretending they
  close or replace the old affiliation or relationship.
- Separate tool calls are not an atomic update transaction. The existing fact-supersession operation is atomic
  within its own call; a CV workflow comprising staging, commits, corrections, or supersessions has no collective
  rollback. On failure, report partial completion accurately and reread before proposing retries. Do not claim that
  all accepted actions happened, retry successful actions blindly, or attempt unapproved compensating edits.
- Rereading targets is a workflow safeguard, not a compare-and-swap or isolation guarantee. No generic concurrency
  token or batch transaction is introduced by this milestone.

## Privacy and compatibility

Incoming CV text stays outside the database. Use concise distilled claims and existing source receipts, not document
copies in proposals, logs, source labels, or provenance. Keep sensitive background out of the unrestricted summary,
affiliations, and relationships; use facts with enforceable sensitivity where appropriate.

Ordinary consolidation and timeline reads keep independent record and evidence disclosure. Missing or filtered
evidence is not permission to retrieve protected material through another field, infer its contents, or call the
profile complete. Maintain existing source redaction and duplicate-source rules. Proposal generation is read-only;
mutations retain normal validation, audit/changelog behavior, and explicit acceptance.

M24.1 is an additive response change with no new primary table. M24.2 uses the existing mutation tools and their
constraints; neither PR changes default disclosure, candidate compatibility, or historical record meanings.

## Acceptance scenarios and verification

- Bounded consolidation includes old and concurrent affiliations with dates, stored attribution, available receipt
  references, stable ordering, and explicit truncation. Existing collections and signals keep their behavior.
- Reimporting an identical tracked CV follows receipt/idempotency rules. A revised CV repeating a skill is classified
  as already represented when supported by the available records, without automatic confidence growth.
- Conflicting CV claims produce a specific review proposal or remain unresolved. Concurrent jobs and multiple skills
  are not closed, merged, or deleted simply because another value exists or a newer document omits them.
- Year-only, month-only, approximate, and unknown dates never generate a guessed supersession boundary. An exactly
  dated valid fact transition preserves the old value and original endpoint; invalid transitions remain unresolved.
- An ambiguous person produces no retargeted writes. Unreadable CV sections, truncated affiliation history, and
  filtered evidence are disclosed as comparison limits rather than proof a claim is absent.
- A reviewed target changed by another writer requires renewed review; its old acceptance is not applied to the new
  state. A supported factual correction and a supported temporal transition use their distinct existing operations.
- A workflow whose first accepted action succeeds and second fails reports that partial result, rereads the first,
  and does not imply rollback or replay it blindly. Historically correct values survive the successful transition.
- Sensitive incoming background creates no unrestricted summary, affiliation, or relationship leakage. Readable
  traits reveal only readable evidence; neither proposals nor truncation metadata expose hidden evidence.
- Focused read-model, SQLite bounding, MCP compatibility/disclosure, and fictional workflow checks exercise these
  outcomes. Verify proposals precede writes, acceptance matches applied arguments, and read-only comparison changes
  no domain/audit/changelog state; run required repository checks for implementation PRs.

## Dependencies and deferred work

M22 precedes M24; M24.1 precedes M24.2. M24 also reuses delivered M19 consolidation and fact supersession. M23 is an
independent presentation improvement and is not a prerequisite for reviewed CV updates.

Structured partial-date storage, dedicated life events, automated belief revision, semantic deduplication, and
generic batch mutation are deferred. Native document parsing, internal LLM execution, raw-document storage, and
automatic personality assessment remain out of scope. The initial workflow cannot promise complete historical
comparison, arbitrary temporal transitions, automatic conflict resolution, or atomic multi-tool updates.
