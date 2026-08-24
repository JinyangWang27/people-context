# M19 — Knowledge consolidation and temporal views

Status: Planned. See [docs/roadmap.md](../roadmap.md#m19--knowledge-consolidation--temporal-views).

## Motivation

Once repeated structured imports and agent-assisted extraction become normal, the main problem shifts from capture
to maintenance. Long-lived stores accumulate repeated facts, overlapping traits, observations that reinforce or
contradict earlier inferences, and changing affiliations. Users and agents need to understand how knowledge evolved
over time and identify where consolidation would improve quality.

M19 adds bounded chronological views and review-only consolidation proposals. It deliberately does **not** create an
autonomous belief updater. Durable corrections, merges, and temporal state transitions remain explicit approved
mutations. M19 adds one narrow fact-supersession operation because the existing `correct_record` contract is an
in-place correction and must not be repurposed to overwrite historically correct values.

## Scope

In scope:

- a deterministic bounded person timeline over durable events/state transitions already stored;
- local CLI and ordinary-disclosure MCP access to that timeline with explicit sensitivity handling;
- a bounded consolidation-context read that gives an agent enough evidence/provenance to reason about duplicate,
  superseding, reinforcing, or contradictory knowledge;
- a narrow atomic `supersede_fact` application/MCP mutation for a fact that was historically correct but changes at
  a known effective date;
- one shared logical transaction id across every changelog row emitted by that supersession;
- an agent workflow that proposes structured maintenance actions and requires explicit approval before writes;
- reuse of M18 source/evidence links when explaining why a trait exists or why a consolidation is proposed;
- tests that prove no report/read path performs durable mutation.

Non-goals:

- automatically merging people, facts, observations, traits, or relationships;
- automatically changing trait confidence from evidence count;
- opaque model-generated scores stored as truth;
- a continuously running maintenance agent/daemon;
- semantic vector clustering as a required core dependency;
- deleting source sessions/evidence as a side effect of consolidation;
- rewriting history to make the current state look cleaner;
- a generic multi-record “consolidate” mutation or arbitrary batch-write tool;
- temporal supersession for every record type in M19; affiliation/relationship transitions can be designed later if
  real usage demonstrates the need.

## Temporal view

Add a bounded application read for one resolved person. Exact naming is implementation-time; conceptual CLI/MCP
surface:

```text
pctx timeline PERSON [--limit N] [--include-sensitive] [--json]
get_person_timeline(person, limit=...)
```

The timeline is a projection, not a new source of truth. It may include appropriate chronological entries derived
from:

- interactions;
- observations;
- dated facts / validity changes, including explicit fact supersession;
- affiliation validity changes;
- relationship creation/change where useful and attributable;
- trait creation/correction/evidence additions;
- reminders only if they represent historical durable events rather than future task state.

Each timeline item should expose stable ids/type, an event/effective timestamp, concise display metadata,
provenance/source-session id when available, and sensitivity/disclosure metadata needed by the caller. Do not expose
raw source material.

### Ordering and bounds

- sort by effective/event timestamp with a stable id/type tie-breaker;
- define behavior for undated durable state explicitly rather than inventing timestamps;
- bound rows in the application layer and, where material, bound underlying SQLite work too;
- ordinary MCP disclosure excludes restricted/sensitive material according to existing policy;
- local CLI may support explicit `--include-sensitive` because it is a human-operated local surface, but the default
  should remain conservative for newly introduced machine JSON.

The timeline should answer “what changed / what happened around this person?” rather than reconstructing every audit
row. Audit/changelog remain lower-level operational history.

## Consolidation context

Add a bounded read model intended for review/agent reasoning, not direct mutation. For one person it should make
visible enough structured context to identify:

- exact/near-duplicate facts or traits;
- facts with overlapping validity and different values;
- observations supporting or contradicting a trait;
- multiple traits expressing substantially the same durable characteristic;
- later explicit facts that supersede older state;
- provenance/source-session/evidence links needed to justify a maintenance proposal.

Prefer deterministic selection/bounding and existing normalized values before adding semantic dependencies. If an
agent wants semantic comparison, it may reason over the bounded returned text using its own model.

This read complements, not replaces, M15 `doctor`: doctor continues to report deterministic store-integrity/data
quality findings, while consolidation context supplies richer person-scoped evidence for judgement calls that
cannot safely be decided by deterministic core policy alone.

## Fact correction vs supersession

A factual **correction** and a factual **state transition** are different domain events:

- correction: “the stored value was wrong at the time”;
- supersession: “the old value was correct, then the real-world state changed”.

`CorrectRecord` explicitly updates a record in place and preserves the prior snapshot only in audit history. It
therefore remains the tool for erroneous data and must not be used to replace an historically correct fact's value
with its newer value.

For temporal supersession add a narrow `SupersedeFact` use case and MCP mutation, conceptually:

```text
supersede_fact(
  fact_id,
  new_value,
  effective_from,
  confidence?,
  sensitivity?
)
```

The operation is one atomic unit of work:

1. load the existing fact and require its person to remain active;
2. require a concrete `effective_from` date that is after any existing `valid_from` and represents a transition
   while the old fact is still effective; reject inconsistent/already-ended periods rather than guessing;
3. close the old fact at `effective_from - 1 day` because `ValidityPeriod` endpoints are inclusive;
4. preserve the old fact's person, predicate, value, original provenance, and recorded timestamp; only its
   `valid_to` changes;
5. create a new fact for the same person/predicate with `new_value` and `valid_from=effective_from`; new confidence
   and sensitivity may be supplied, otherwise inherit the old fact's values;
6. give the new fact normal provenance from the approved supersession call;
7. mint one logical `transaction_id` for the supersession and pass that exact id to the `audit_mutation` call for
   both the old-row update and the new-row creation;
8. commit both durable rows, audit entries, and changelog rows in the same unit of work, so neither “old closed, new
   missing” nor “new created, old still open” can commit.

The shared `transaction_id` requirement is independent of SQLite atomicity. `audit_mutation` mints a new id when
none is supplied, while the sync contract defines `transaction_id` as the grouping key for every row-level effect of
one logical transaction. Calling `audit_mutation` twice without sharing the id would therefore make replay and
inspection describe one indivisible supersession as two unrelated transactions even though SQLite committed them
together.

Implementation may generate the id explicitly before both mutations or use the id returned by the first
`audit_mutation` for the second; either way both supersession changelog entries must carry the same non-empty id. The
ordinary audit rows remain whatever the existing audit schema supports; do not add an audit-only transaction field
merely for this operation.

The use case does not allow changing the predicate/person as part of supersession. A caller correcting a typo,
misidentified predicate, wrong historical value, or wrong validity date still uses `correct_record`.

This narrow operation is a temporal domain primitive, not a generic consolidation mutation. It exists so the M19
maintenance workflow can preserve canonical history without abusing an in-place correction API.

## Review-only consolidation workflow

Extend the packaged agent skill with a maintenance workflow:

1. resolve one person unambiguously;
2. read ordinary context, timeline, and consolidation context;
3. identify possible duplicate, superseding, reinforcing, or contradictory knowledge;
4. explain the evidence and provenance supporting each proposal;
5. propose structured actions using stable mutation tools/ids;
6. do not execute those actions until the user explicitly accepts them;
7. after approved writes, re-read the affected person and report the resulting state.

Examples of proposals:

- correct an erroneous historical fact using `correct_record`;
- when a historically correct fact changes, use `supersede_fact` so the old row keeps its value with a closed
  validity period and a new row begins at the effective date;
- merge duplicate people only when identity is independently established;
- add a better-supported trait that supersedes an earlier weak inference;
- retain two observations because they are separate evidence rather than “deduplicating” history;
- leave contradictory evidence intact when the source material genuinely conflicts.

The agent must distinguish **redundant representation** from **multiple evidence**. Three observations that all
support one trait are not necessarily duplicates and should not be collapsed merely to reduce row count.

The agent must also distinguish **correction** from **temporal supersession**. It must never propose changing an old
fact's `value` in place merely because a newer value is now true.

## Trait confidence policy

M19 deliberately avoids a formula such as `confidence += N * 0.1`. Confidence is epistemic judgement, not a count of
supporting rows. Evidence can be correlated, repetitive, low-quality, or contradicted.

An agent may propose a corrected/replacement trait with a different confidence and cite the evidence used, but the
new value is committed only through the normal explicit mutation/review path. No background task recalculates it.

## Migration needs

Prefer none for timeline/consolidation reads or fact supersession; facts already carry inclusive validity periods.
Use existing primary records, provenance, validity, M18 source sessions, and evidence links.

If implementation-time query plans require an additive index, use the next free migration number and document the
measured reason. Do not create a denormalized timeline table merely for presentation unless profiling proves the
read cannot be bounded efficiently from canonical data.

## CLI / MCP surface changes

Expected additive surfaces:

```text
pctx timeline PERSON [--limit N] [--include-sensitive] [--json]
```

plus:

- one read-only MCP timeline tool;
- a separate consolidation-context MCP read if the existing context response cannot provide the required bounded
  evidence/provenance cleanly;
- one narrow write MCP tool `supersede_fact`, following the ordinary mutation/audit/disclosure conventions.

Any machine JSON documented for CLI integration is versioned and additive under M12. Human rendering remains
non-frozen.

No generic consolidation mutation tool is added merely to bundle arbitrary writes. `supersede_fact` is the single
narrow exception because temporal fact transition is a domain operation that cannot be represented correctly by
in-place value correction or by two non-atomic independent writes.

## Security and privacy

- Timeline/consolidation views can juxtapose sensitive information; ordinary MCP disclosure rules apply before
  model exposure.
- Local `--include-sensitive` is explicit and documentation warns before redirecting/sharing output.
- Source-session ids and evidence metadata do not grant access to raw source content because none is stored.
- Maintenance suggestions use stable ids and structured tool arguments, never shell-interpolated names/text.
- The agent workflow must not infer or “clean up” sensitive characteristics merely for consistency.
- Reports and proposal generation are read-only; no hidden write occurs during analysis.
- `supersede_fact` is invoked only after explicit approval and preserves the old historical value rather than
  rewriting it.

## Testing strategy

- Timeline fixtures cover interactions, observations, validity-dated facts/affiliations, explicit supersession,
  trait/evidence metadata, undated records, stable ties, limits, and disclosure filtering.
- SQLite tests prove underlying reads are bounded and query plans remain reasonable on dense synthetic history.
- Consolidation-context tests cover deterministic ordering/bounds and include provenance/evidence without raw-source
  leakage.
- `SupersedeFact` tests cover inclusive boundary math, old-value preservation, inherited/explicit confidence and
  sensitivity, invalid/already-ended periods, inactive people, stable same person/predicate, provenance on the new
  fact, and full rollback when either old-close or new-create/audit phase fails.
- Audit/changelog tests prove supersession emits exactly the expected two row-level changelog effects with the same
  non-empty `transaction_id`, while the entity ids/op kinds/changed fields remain distinct and correct. The test also
  proves the operation does not masquerade as a `correct_record` value replacement.
- Failure-injection tests prove the shared transaction grouping and both durable mutations roll back together on
  either phase failure; no one-sided changelog group survives.
- Regression tests prove M15 doctor and existing `correct_record` behavior remain unchanged and no M19 read mutates
  audit/changelog/domain state.
- MCP tests cover ordinary disclosure, ambiguity/not-found behavior, `supersede_fact` validation, and explicit
  mutation semantics.
- CLI tests cover human/JSON timeline output, explicit sensitivity opt-in, stable ids, and no shell construction.
- Scripted agent-workflow tests prove proposals occur before any mutation, approval is required, erroneous facts use
  correction, and historically correct changed facts use supersession without overwriting the old value.
- `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`, and `uv build` are fully green.

## Implementation decisions

- Timeline is a projection over canonical history, not a duplicate event store.
- Consolidation separates deterministic retrieval from agent judgement.
- Multiple supporting observations remain evidence rather than being collapsed automatically.
- `correct_record` is for erroneous stored data; historically correct fact changes use the atomic
  `supersede_fact` temporal operation.
- Both row-level changelog effects of one supersession share one logical `transaction_id` as required by the sync
  contract; SQLite transaction scope alone is not sufficient grouping metadata.
- Confidence changes, corrections, merges, and supersession remain explicit approved mutations.
- A background self-modifying memory process is intentionally outside M19.
