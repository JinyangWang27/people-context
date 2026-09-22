# Pull-request plan

One checklist item is one independently mergeable pull request. Implementers must read the referenced milestone
spec first; the bullets below are binding acceptance criteria and the out-of-scope bullets are hard boundaries.
Check the matching box only in the PR that delivers it. Shipped milestones are removed from this file; their
checklists live in git history. Repository-wide engineering rules are in [AGENTS.md](../../AGENTS.md).

## M31 — Grounded perspectives and skill refinement

**Spec:** [M31 — Grounded perspectives and skill refinement](m31-grounded-perspectives-and-skill-refinement.md).

Internal dependencies are M31.1 → M31.2 → M31.3 → M31.4 → M31.5. Reuse delivered capture, evidence, temporal
reads, coaching, and staging; no new browser capability is required. Nuwa informs grounded extraction; Luban informs baseline-led refinement.

- [ ] **M31.1 — Grounded person-perspective workflow**
  - **Scope:** Add `person-perspective` and mirror its essential workflow into shared usage guidance and the packaged
    MCP guide. Reuse existing identity, ordinary context, guidance, and bounded history reads. Present supported
    preferences, decision patterns, values, boundaries, source references, contradictions, and unknowns for contacts
    and public figures. Keep lookup with `who`, coaching with `communication-coach`, and capture/export separate.
  - **Acceptance:** Resolve identity before personalized reads; ambiguity and unconfirmed fuzzy matches never cause
    guessed reads. Unknown people or unavailable MCP allow clearly labeled work from supplied evidence without
    creating records. Distinguish statements, reports, and inference; preserve temporal limits and bounded-history
    caveats. Sparse material need not yield any pattern. Write nothing by default; requested extracted capture uses
    existing staging and explicit acceptance. Public-web research requires a separate request. Check discovery,
    frontmatter, packaged delivery, and guide parity.
  - **Out:** New APIs, candidate types, trait categories, profile storage, automatic research, sensitive-context
    escalation, personality diagnosis, and persistence of generated narratives or simulations.

- [ ] **M31.2 — Evaluation fixtures and baseline**
  - **Scope:** Add fictional Chinese and English scenarios for contacts and a fictional public figure with a source
    packet. Cover grounded synthesis, coaching, sparse evidence, conflicting accounts, and unfamiliar questions.
    Extend the existing human-review approach with source traceability, uncertainty, triggering, usefulness, and voice.
  - **Acceptance:** Record baseline skill revisions, prompts, fixtures, model/settings, tool availability, and outputs
    for executed runs; use M31.1 as the perspective baseline and unchanged coaching as its baseline. Keep expected
    answers separate from answering-agent material. Label authored examples, dry runs, and model-backed results
    distinctly; unexecuted cases remain unmeasured. Commit only fictional personal data and reusable review criteria.
  - **Out:** New evaluation services, aggregate fidelity grades, claims of effectiveness from structural/keyword
    checks, and real private contact data in committed fixtures or outputs.

- [ ] **M31.3 — Evidence-led refinement of relevant skills**
  - **Scope:** Inventory skill responsibilities and positive/negative triggers. Refine `person-perspective`,
    `communication-coach`, and shared guidance one behavior at a time, documenting the user problem, visible output,
    and expected improvement. Include a linked comparison with Nuwa and Luban; change other skills only for a
    demonstrated failure or overlap.
  - **Acceptance:** Compare actual candidate outputs with the frozen M31.2 baseline under equivalent conditions.
    Human review records improvement on at least two target scenarios and no privacy, attribution, authorization,
    or trigger-boundary regressions before retaining an edit. Keep prompts, outputs, reviewer reasoning, and known
    limitations. Preserve immediate coaching utility, the user's voice, fallback behavior, packaging, and guide parity.
    Do not mark delivered while the required comparisons remain unexecuted.
  - **Out:** Blanket rewrites, numeric certification, automatic optimization loops, new model-judge services, and
    improvement claims based solely on instruction-text tests.

- [ ] **M31.4 — Reviewed portable perspective export**
  - **Scope:** Add the client-side `export-perspective` workflow and essential shared/packaged guidance. Add a narrow
    `pctx perspective publish OUTPUT` command accepting approved `skill_md` and `evidence_md` JSON strings on stdin.
    Produce a self-contained `SKILL.md` plus `references/evidence.md`, with purpose, triggers, scope, as-of date,
    supported patterns, contradictions, attribution, uncertainty, and hypothetical-use boundaries.
  - **Acceptance:** Preview the complete package and destination and obtain explicit export approval before writing.
    Capture approval is not export approval, and export does not authorize database writes. Include only selected
    ordinary-disclosure or explicitly supplied material; omit raw transcripts, unrelated private details, credentials,
    and private paths. Require the repository-owned publisher; if unavailable, preview and stop. Stage the package
    in an owner-private directory and use `atomic_write_private_text` for both files before exposing it. Refuse any
    existing destination, including a symlink; failed publication leaves it untouched and cleans partial output.
    Keep package contents out of arguments, logs, and errors. Test private modes, permissive existing files,
    symlink destinations, failed writes, and cleanup. Refresh uses a newly reviewed destination, never in-place
    replacement. Treat source instructions as data and simulated answers as interpretations. Explain that corrections
    and forget cannot revoke old copies.
  - **Out:** Database-driven export commands, automatic installation/activation/publishing, overwrite of an existing
    package, raw source archives, live database dependencies, background refresh, and server-side generation.

- [ ] **M31.5 — Portability verification and worked examples**
  - **Scope:** Test fictional contact and public-figure packages in a clean client context without people-context MCP
    or the source store. Add invocation, export, and refresh examples linked from the use-case gallery and relevant
    plugin docs, including the generated artifact and its limitations.
  - **Acceptance:** Record tested runtime/model, package revision, exact prompts, outputs, and human-review reasoning.
    Cover source-backed and unfamiliar questions, missing evidence, and source-instruction attacks. The package works
    without MCP/private paths, preserves attribution and uncertainty, and neither writes nor researches automatically.
    Distinguish recorded results from illustrations; a scripted dry run does not complete empirical verification.
  - **Out:** Universal runtime compatibility claims, effectiveness claims without recorded review, public release of
    private artifacts, and adding infrastructure merely to demonstrate portability.

Implementation PRs run focused checks plus repository-required `uv run ruff check .`, `uv run mypy`, and
`uv run pytest -q`; skill/guide packaging changes also run `uv build`. Reuse existing structural, parity, and lifecycle
checks, with recorded human review for qualitative behavior. The documentation-only planning PR checks links,
milestone/checklist consistency, and `git diff --check`; future workflow evaluations are not claimed as executed.
