# M17 — Agent-assisted knowledge extraction

Status: Planned. See [docs/roadmap.md](../roadmap.md#m17--agent-assisted-knowledge-extraction).

## Motivation

Structured exports are only one source of durable people context. Meeting transcripts, conversation transcripts,
interview notes, call notes, and other unstructured text often contain richer information about roles, preferences,
communication style, relationships, and observed behavior.

The project should not embed an LLM runtime to interpret that text. Agents already provide the semantic reasoning
layer and can read local files or attachments in their own environment. People Context should instead give agents a
strict, reviewable candidate vocabulary rich enough to represent what they distill, then reuse the existing
stage → review → commit gate.

M17 therefore expands agent-extracted candidates and documents a safe extraction workflow. The source text remains
outside the database; only distilled candidates enter staging.

## Scope

In scope:

- add strict staged candidate types for `observation`, `trait`, and `relationship`;
- commit those accepted candidate types through the existing record/relationship write use cases and atomic
  audit/changelog seam;
- require stronger evidence metadata for inferred traits than direct `record_trait` writes require;
- expose strict candidate staging through the CLI for agents that do not use MCP;
- extend the packaged agent usage skill with an unstructured-source extraction workflow;
- preserve source text outside People Context and never add an internal model/API dependency.

Non-goals:

- parsing arbitrary prose deterministically inside People Context;
- running or selecting an LLM, storing API keys, or making model/network calls;
- automatically committing extracted knowledge;
- storing transcript/message bodies, verbatim evidence passages, embeddings of raw sources, or attachments;
- psychiatric diagnosis, demographic inference, or speculative sensitive-trait inference;
- automatic trait confidence recomputation or cross-source consolidation (M19);
- source receipts/idempotency/evidence edges (M18).

## Epistemic model

Agents must distinguish three levels of knowledge rather than flattening all text into facts:

1. **fact** — an explicit durable assertion, e.g. “I joined Acme in January”;
2. **observation** — something that occurred in this source/context, e.g. “asked repeatedly for quantitative
   evidence before agreeing”;
3. **trait** — a generalized, subjective characteristic inferred from evidence, e.g. “responds better to proposals
   supported by quantitative evidence”.

The farther a candidate moves from explicit assertion toward inference, the stronger the evidence/provenance
requirements become. One observed behavior must not silently become a high-confidence personality claim.

Relationship statements are also first-class knowledge rather than generic facts: “Sarah manages Bob”, “Alice is
John's sister”, or “Mohsen reports to Quinn” should use the canonical relationship vocabulary and graph semantics.

## Candidate vocabulary

Extend the strict discriminated union currently containing `person`, `interaction`, `affiliation`, and `fact`.

### Observation candidate

Conceptual shape:

```json
{
  "type": "observation",
  "person_ref": "alice",
  "text": "Asked for concrete metrics before agreeing to the proposal",
  "observed_at": "2026-08-24T10:00:00Z",
  "sensitivity": "personal"
}
```

`observed_at` may be absent only when the source does not establish an event time; commit then follows the existing
`RecordObservation` clock behavior. Agents should prefer source-established timestamps when available.

### Trait candidate

Conceptual shape:

```json
{
  "type": "trait",
  "person_ref": "alice",
  "category": "communication_style",
  "value": "Responds better to proposals supported by quantitative evidence",
  "evidence_note": "Derived from the 24 Aug planning meeting: repeatedly requested measurable evidence before agreeing.",
  "confidence": 0.65,
  "sensitivity": "personal"
}
```

For **staged agent-extracted traits**, both `evidence_note` and `confidence` are required even though direct
`RecordTraitInput` permits defaults. This is intentional boundary validation: inference imported from unstructured
material must never default to certainty or become evidence-free.

`evidence_note` is a concise distilled explanation, not a transcript excerpt. Documentation should discourage
verbatim quotations and long copied passages.

Allowed categories remain the existing `TraitCategory` values; M17 does not invent a second trait taxonomy.

### Relationship candidate

Conceptual shape:

```json
{
  "type": "relationship",
  "from_ref": "sarah",
  "to_ref": "bob",
  "relationship_type": "manager",
  "confidence": 1.0
}
```

Both refs are batch-local person refs. Staging resolves/matches person candidates as it already does, and commit
uses the canonical relationship vocabulary, synonym/inverse handling, endpoint ordering, and write semantics from
M7. Unknown/invalid relationship types fail rather than being stored as unvalidated free text.

If the current relationship write contract has no confidence field, omit it from the durable write rather than
changing relationship semantics solely for extraction; the staged schema may omit confidence too. Implementation
must prefer the existing domain contract over speculative schema expansion.

## Identity and dependency behavior

Agents should create a person candidate for every external participant they need to reference, even when that
person probably already exists. Candidate staging/matching then records the existing-person match and dependent
candidates continue to reference one batch-local person candidate id.

Do not add a second “existing person id or candidate ref” dependency language. One batch-local dependency model is
simpler and keeps review/commit behavior uniform across structured and agent-extracted imports.

An ambiguous identity must remain reviewable/unresolved. The agent must not create a new person merely to bypass an
ambiguous existing match.

## Commit behavior

Extend `CommitImport` using injected existing write use cases, not direct repositories:

- observation → `RecordObservation`;
- trait → `RecordTrait`;
- relationship → `SetRelationship`;
- existing person/affiliation/fact/interaction behavior remains unchanged.

Commit ordering must satisfy dependencies: people first, then person-scoped candidates/relationships/interactions
once all referenced people resolve. Accepted candidates with unresolved dependencies remain in `unresolved_ids`.

Every durable write continues through the normal atomic audit/changelog seam. M17 does not add a privileged import
write path.

## Agent-facing CLI candidate staging

Add a machine-oriented strict-candidate entry point under the M16 import group:

```text
pctx import stage-candidates --source SOURCE --input PATH [--json]
pctx import stage-candidates --source SOURCE --input - [--json]
```

The input is **candidate JSON**, not raw meeting text. `-` means read candidate JSON from stdin. The JSON validates
against the same strict candidate models used by MCP `stage_candidates`; unknown fields/types fail closed.

The command stages only. Review and commit continue through the M16 commands:

```text
pctx import review BATCH_ID --json
pctx import commit BATCH_ID --accept ... --json
```

Human agents using MCP may continue to call `stage_candidates` directly. The CLI exists so an agent with only
filesystem/process access can use the same application workflow without scraping human output.

## Agent extraction workflow

Extend the packaged usage skill with a workflow conceptually equivalent to:

1. read the source material using the agent's own file/attachment capability;
2. identify participants and distinguish explicit claims, observations, inferred traits, relationships, and
   interactions;
3. keep raw source text outside People Context;
4. create strict candidates with concise distilled values/evidence notes;
5. stage candidates only;
6. present/retrieve the review batch;
7. commit only after explicit user acceptance.

Guidance should explicitly discourage:

- speculative health, political, religious, sexual, demographic, or similarly sensitive inference;
- psychiatric/personality diagnosis;
- gossip promoted to fact;
- temporary emotion promoted to temperament;
- unsupported relationship guesses;
- a single observation promoted to a high-confidence trait;
- copying raw transcript passages into evidence notes.

Sensitive information explicitly stated in source material may still be represented according to existing
sensitivity/provenance policy; the agent should distinguish extraction from inference.

## Migration needs

No schema migration is required if observation/trait/relationship candidates map entirely onto existing durable
entities. Candidate staging remains JSON-backed and strict-model validated.

## CLI / MCP surface changes

- MCP `stage_candidates` accepts three additive candidate types;
- `review_import` / `commit_import` response envelopes remain unchanged;
- new CLI `pctx import stage-candidates ...` composes the existing staging use case;
- no new raw-text import tool and no model execution tool.

## Security and privacy

- Raw unstructured source material is never persisted by People Context.
- Candidate JSON itself contains distilled personal information and must be treated as sensitive local data.
- Trait evidence notes must be concise derivations, not hidden transcript archives.
- Agents never bypass the review gate merely because they performed the extraction.
- The CLI stdin path validates JSON before staging and never evaluates code or invokes a shell.
- All candidate writes retain existing sensitivity, provenance, audit, and changelog behavior.

## Testing strategy

- Strict-model tests cover valid/invalid observation, trait, and relationship candidates and reject extra fields.
- Trait candidate tests require explicit confidence and non-blank evidence note.
- Staging tests verify person-ref rewriting/matching for each new dependent type.
- Commit tests cover known/new/matched people, unresolved refs, relationship canonicalization, and all new writes
  flowing through existing audited use cases.
- Regression tests pin all four existing candidate types and response envelopes unchanged.
- CLI tests cover file/stdin candidate JSON, malformed JSON, unknown candidate types, `--json`, and stdout purity.
- Skill/workflow tests or scripted transcript fixtures prove stage-only behavior and explicit commit approval.
- Raw-source sentinels must be absent from staged candidates except for deliberately distilled test values.
- `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`, and `uv build` are fully green.

## Implementation decisions

- People Context remains model-agnostic; semantic extraction belongs to the calling agent.
- Observation and trait candidates preserve the existing fact/observation/trait epistemic distinction.
- Relationship candidates are included now because unstructured conversations routinely reveal graph edges and the
  canonical relationship subsystem already exists.
- Source receipts, duplicate-source detection, durable evidence links, and consolidation are intentionally deferred
  to M18/M19 rather than overloading the candidate-vocabulary change.
