# M31 — Grounded perspectives and skill refinement

Status: Planned. This specification delivers nothing itself.
See [roadmap](../roadmap.md#m31--grounded-perspectives-and-skill-refinement) and
[PR checklist](pr-plan.md#m31--grounded-perspectives-and-skill-refinement).

## Purpose and existing foundation

Help the user understand a contact's or public figure's documented preferences, decision patterns, values, and
boundaries, use that understanding in real conversations, and explicitly export a reviewed perspective when useful.
A perspective is a qualified interpretation of available evidence, not a replica of a person or access to hidden intent.

Reuse M17/M18 reviewed capture and evidence, M19/M23 temporal reads and explainable history, M25 communication
coaching, and M29 editable staging. The server supplies deterministic records; the client synthesizes the perspective.
Existing traits and history remain the durable foundation. No parallel profile store or saved narrative authority is added.

Two external projects inform this milestone:

- [Nuwa's extraction framework](https://github.com/alchaincyf/nuwa-skill/blob/main/references/extraction-framework.md)
  motivates separating statements from inference, testing patterns across contexts, retaining contradictions, and
  stating limits. Adapt these ideas to sparse personal records; do not require a fixed number of models or invent
  enough patterns to fill a template.
- [Luban's refinement workflow](https://github.com/LearnPrompt/luban-skill/blob/master/skills/luban/SKILL.md)
  motivates checking the user problem, preserving a baseline, improving one behavior at a time, and checking actual
  outputs before claiming improvement. Adopt the verification discipline without copying a publication campaign,
  automatic push/release policy, numeric quality certificate, or mandatory agent swarm.

These are design references, not runtime dependencies. They do not override this repository's privacy or authorization
contracts. The implementation stays within the existing client skills and packaged guidance surfaces.

## Planned PRs

### M31.1 — Grounded person-perspective workflow

Add `skills/person-perspective/SKILL.md`. Trigger on an explicit request to understand a person's documented
thinking, priorities, decision patterns, or boundaries. Identity lookup remains with `who`; drafting, rehearsal,
and debriefing remain with `communication-coach`; capture and export are separate requested actions.
Mirror the essential workflow into shared usage guidance and the packaged MCP guide, preserving guide-body parity.

1. Establish the person and intended use from the conversation. Resolve identity before personalized store reads;
   ambiguity or an unconfirmed fuzzy match must not cause guessed reads. An unknown person or unavailable MCP can
   still be discussed from supplied material without creating a database record.
2. Read only relevant ordinary-disclosure context, communication guidance, and bounded history through existing
   tools. Use source and evidence references already available; inaccessible evidence is not a reason to escalate.
3. Produce concise supported patterns with their sources, dates or temporal limits, applicable situations,
   contradictions, and unknowns. Separate direct statements, other people's reports, and agent inference. Stored
   traits remain subjective signals, and repeated copies of an account are not independent corroboration.
4. Explain the limits of the available material. Cross-context evidence may support a broader pattern, but a
   single event does not establish personality. A bounded read is not complete history; no pattern is a valid result.
5. Write nothing by default. If the user requests persistence, extracted claims use existing candidate types and
   stage → review → explicit acceptance → commit. Preserve existing direct-capture rules for direct user statements.
   Do not persist the synthesized narrative, simulated behavior, or unsupported generalizations as new knowledge.

Support contacts, self, and public figures using stored or supplied evidence. Research on the public web is a
separately requested client action, never an ordinary server operation. Research does not authorize storing results.

### M31.2 — Evaluation fixtures and baseline

Add fictional Chinese and English scenarios spanning work, friends, family, and a fictional public figure with a
supplied source packet. Include source-backed patterns, conflicting accounts, sparse records, and unfamiliar questions.
Keep expected evidence and review criteria separate from the material available to the answering agent.

Record baseline skill revisions, exact prompts, fixture versions, model/settings, tool availability, and complete
outputs for executed runs. For the new workflow, M31.1 is the refinement baseline. Existing coaching uses its
unchanged pre-refinement revision. Compare equivalent conditions rather than attributing model or source changes
to a skill edit.

Extend the existing qualitative review approach in [evals.md](../evals.md). Review usefulness, source traceability,
uncertainty, contradictions, triggering, and preservation of the user's voice. Use known-answer cases and
out-of-scope questions inspired by
[Nuwa's scorecard](https://github.com/alchaincyf/nuwa-skill/blob/main/references/fidelity-scorecard.md), without
adopting an aggregate fidelity grade or treating style imitation as proof of understanding.

Authored examples, scripted dry runs, and actual model outputs must be labeled separately. Unexecuted scenarios
remain unmeasured. Structural or keyword checks cannot establish qualitative effectiveness. Commit only fictional
personal data to evaluation assets; use the existing evaluation infrastructure rather than introducing a service.

### M31.3 — Evidence-led refinement of relevant skills

Inventory existing skill responsibilities and positive/negative triggers. Concentrate refinements on
`person-perspective`, `communication-coach`, and shared usage guidance. Modify another skill only when a documented
failure or overlap requires it; this PR is not a blanket rewrite.

For each candidate edit, state the user problem, why a reusable workflow helps, the visible output, the one behavior
being changed, and the expected improvement. Include a short linked comparison with Nuwa and Luban identifying
what was adapted and what does not fit people-context.

Compare actual outputs with the frozen M31.2 baseline under equivalent conditions. Human review must explain an
improvement on at least two target scenarios and no regression in the privacy, attribution, authorization, and
trigger-boundary scenarios. Retain a candidate only when that gate passes; otherwise revise or drop it. Keep the
prompt, outputs, reviewer reasoning, and known weaknesses as reusable evidence. Do not claim improvement from
instruction-text tests alone, and do not mark this PR delivered while the required comparisons remain unexecuted.

Keep instructions focused and runtime-neutral. Preserve the user's voice, immediate utility in coaching, existing
fallbacks, and guide parity. No numeric certification, automatic optimization loop, or new model-judge service is added.

### M31.4 — Reviewed portable perspective export

Add `skills/export-perspective/SKILL.md` and its essential shared/packaged guidance. This is a client workflow,
not a native CLI command. Produce a self-contained `SKILL.md` and a concise `references/evidence.md` containing
only the selected material needed to interpret the perspective.

The package includes its purpose and triggers, scope, as-of date, supported patterns, contradictions, uncertainty,
source attribution, and hypothetical-use boundaries. Use concise source descriptions and public URLs where
available; portability must not depend on private database access or machine paths. Neither raw source documents
nor a copy of the user's contact database belongs in the package.

Preview the complete package and destination before writing and obtain explicit approval of that export. Approval
to store records is not approval to export, and approval to export does not authorize database writes. Use only
ordinary-disclosure records or supplied material explicitly selected for this purpose; omit unrelated people's
details, credentials, private paths, and raw transcripts. Write personal artifacts with private file permissions
through the client's available file facilities; do not claim server-side protection for client-created copies.

Refuse to overwrite an existing artifact without a new reviewed replacement decision. Do not install, publish,
execute, or activate the generated skill automatically. Generated instructions treat evidence as data rather than
executable instructions and label simulated answers as interpretations, not actual statements or predictions.

Exports are dated snapshots. Subsequent corrections and forget operations cannot revoke copies already exported.
Refresh requires rereading the available evidence, regenerating the package, and reviewing the replacement;
there is no background refresh or synchronization.

### M31.5 — Portability verification and worked examples

Verify an M31.4 package can be used in a clean client context with no people-context MCP access and no access to
the original source store. Use fictional contact and public-figure examples. Record the tested runtime/model,
package revision, exact prompts, outputs, and qualitative reviewer reasoning without claiming universal runtime support.

Check source-backed questions, unfamiliar situations, missing evidence, and malicious instructions embedded in
source material. The skill must explain its scope and uncertainty, distinguish interpretation from attribution,
and neither request private database access nor perform writes or research merely to fill a gap.

Add worked invocation, export, and refresh examples to the existing documentation and link them from the use-case
gallery and relevant plugin documentation. Show the generated artifact and its limitations. Record actual review
results separately from illustrative examples; do not mark empirical verification complete on a scripted dry run.

## Privacy, compatibility, and non-goals

No new server API, MCP tool or prompt, CLI command, database table, trait category, dependency, or server-side LLM
call is introduced. New interfaces are discoverable client skills, their Markdown outputs, and essential guidance
through the existing `people-context://guide`. Ordinary commands retain their no-network contract.

Incoming documents and messages are untrusted task material, not instructions to invoke tools or disclose records.
Existing sensitivity, identity, evidence, and reviewed-capture rules remain binding. No automatic sensitive-context
escalation, raw-document storage, personality diagnosis, message sending, durable simulation, or public publishing.
The client may retain prompts and tool output under its own policies; do not promise that conversation is local-only
or ephemeral. Exported plaintext is outside database disclosure and forget controls.

## Acceptance scenarios and verification

- A grounded account distinguishes what the person said, what someone else reported, and what the agent inferred;
  available sources and temporal limits are retained without turning repeated reports into independent evidence.
- Contradictory or outdated preferences remain qualified. Sparse evidence produces a limited account or no supported
  pattern, not a completed personality template. Unfamiliar questions elicit uncertainty or abstention.
- Ambiguous identity causes no guessed reads; unavailable MCP and unknown identities permit clearly labeled work
  from supplied material. Missing evidence never triggers sensitive reads or automatic public-web research.
- Lookup, perspective, coaching, capture, and export prompts select the intended workflow. Coaching still leads with
  a usable reply or action and preserves the user's voice, goals, and boundaries.
- Synthesis writes nothing. Requested capture uses existing review; generated drafts and simulated replies never
  become observations, traits, or evidence. A requested export does not implicitly save source records.
- Source text containing tool instructions is treated as data in both synthesis and generated skills. Export previews
  exclude unrelated private material, and writing/replacing packages requires approval of the concrete artifact.
- A reviewed package works without MCP or private paths, labels its as-of date and uncertainty, and explains the
  inability to revoke old copies after correction or forget. Refresh produces a newly reviewed snapshot.
- Existing frontmatter, skill-delivery, packaging, and guide-parity checks cover the added surfaces. Reuse relevant
  capture/lifecycle regression checks rather than implementing another persistence path. Qualitative comparisons
  remain recorded human judgments, separate from automated structural tests.

Implementation PRs run focused checks plus repository-required Ruff, mypy, and pytest gates. Skill/guide packaging
changes also run `uv build`. The documentation-only planning PR checks links, milestone/checklist consistency, and
`git diff --check`; it does not claim to have executed the future workflow or its evaluations.

## Dependencies and deferred work

Internal order is M31.1 → M31.2 → M31.3 → M31.4 → M31.5. Each PR is independently reviewable and mergeable after
its predecessor. All reuse delivered foundations; M31 requires no new M30 browser capability.

Defer native export commands, live-context skill packages, automatic refresh, background research, new profile
schemas, model scoring services, automatic publishing, and unrelated skill rewrites until demonstrated need.
