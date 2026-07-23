# M10 — Agent utilization

Status: Delivered. See [docs/roadmap.md](../roadmap.md#m10--agent-utilization).

## Motivation

The relevant capabilities already exist: `resolve_person`, `get_person_context`,
`get_communication_guidance`, `list_reminders`, `remember_person`, and the
`stage_candidates` → `review_import` → `commit_import` approval flow. What is missing is packaged guidance that
helps agents compose those tools correctly. M10 adds no new business capability, port, tool, or response field. It
may make one minimal adapter-level prose edit to `SERVER_INSTRUCTIONS`; that is a versioned-code change but not a
new server behavior or contract.

The Claude Code plugin currently has manifests under `.claude-plugin/` and no root `skills/`/`commands/` content.
Claude Code discovers skills and compatible command fallbacks at the plugin root, not inside the manifest
directory.

## Scope

In scope:

- a root-level usage skill for identity resolution, communication guidance, and staged capture;
- user-invocable who/remember/reminders workflows under the `people-context` namespace;
- a skill instruction to review durable learnings near session completion and propose staging;
- at most a small additive `SERVER_INSTRUCTIONS` prose extension naming under-used existing tools.

Non-goals:

- new MCP tools, app use cases, ports, response fields, or write paths;
- automatic hooks that persist data or inject a prompt every turn;
- bypassing review or calling `commit_import` without explicit user inspection;
- changing tool annotations or enabling elevated tools.

## Design

### Root usage skill

Add `skills/people-context-usage/SKILL.md` at the plugin root. It teaches:

- resolve identity first and preserve the `ambiguous` candidate-list contract;
- use `get_person_context` for what is known and `get_communication_guidance` for how to communicate;
- use only the strict `person`/`interaction`/`affiliation`/`fact` candidate vocabulary;
- extract concise candidate fields, never raw conversation/transcript text;
- treat `stage_candidates` as proposal, `review_import` as inspection, and `commit_import` as an explicit later
  write;
- the absence of `get_sensitive_person_context`/`export_data` from ordinary discovery is expected process-gate
  behavior, not something to work around.

### User-invocable workflows

Implement three user-invocable skills, with `commands/*.md` compatibility fallbacks only if the selected minimum
Claude Code version requires them. They are thin **workflows**, not artificially one-tool wrappers:

- `/people-context:who <query>` calls `resolve_person`; on exactly one unambiguous match it then calls
  `get_person_context`; otherwise it returns the ambiguity/candidate result without guessing.
- `/people-context:remember <description>` uses `remember_person` only for an explicit person assertion that fits
  that tool's contract. Facts, affiliations, interactions, or information extracted from prior context go through
  `stage_candidates` and remain pending review.
- `/people-context:reminders [person]` optionally resolves the person first and then calls `list_reminders` with
  the resolved id. Ambiguity is surfaced rather than silently dropping the filter.

None introduces a new response schema or calls elevated tools.

### End-of-session capture instruction, no hook

No lifecycle hook reliably injects a useful one-time prompt at session end without either being too late or firing
on every turn. Keep capture skill-only: when a session is naturally wrapping up, the agent reviews what it learned
and may call `stage_candidates` for concise durable candidates. It never calls `commit_import`, never stages raw
transcript text, and does not claim guaranteed mechanical execution at session end.

### Minimal `SERVER_INSTRUCTIONS` extension

The adapter string may gain one or two sentences naming `get_communication_guidance` and `stage_candidates`, while
preserving the identity-resolution-first and approval-gate rules. This is plain adapter prose:

- no tool signature/annotation/registration change;
- no domain/app/port import impact;
- no mention or speculative use of elevated tools.

## Migration needs

None.

## CLI / MCP surface changes

No new CLI command or MCP tool. User-invocable plugin workflows are namespaced prompt/skill surfaces. The optional
`SERVER_INSTRUCTIONS` prose edit is not a structured response-field change.

## Security and privacy

- Staging is proposal-only; commit follows reviewed output and explicit approval.
- Workflows never enable or probe gated sensitive/export tools.
- Raw conversation content is never copied into candidates, logs, files, or hook payloads.
- Ambiguous identity is surfaced and never resolved by guesswork.
- `/remember` does not misuse `remember_person` to encode facts/relationships it cannot represent.

## Testing strategy

- Validate the plugin with the repository's existing reviewed/pinned validation mechanism, covering root
  `skills/` and any compatibility `commands/` files.
- Scripted transcript fixtures prove:
  - resolution precedes context lookup;
  - ambiguous `who` performs no second read;
  - reminders resolves optional person before filtering;
  - explicit person assertion may use `remember_person`;
  - extracted facts/affiliations/interactions stage and do not commit;
  - gated tools are neither called nor suggested.
- If `SERVER_INSTRUCTIONS` changes, update exact/substring adapter tests and prove tool annotations/registrations are
  unchanged.
- Manual local plugin install/reload exercises all three workflows against a temporary database.
- `uv run ruff check .` and `uv run pytest -q` remain green.

## Delivered decisions

1. User-invocable skills require Claude Code 2.1.196 or newer; command fallbacks are unnecessary.
2. `/remember` retains the explicit-person-assertion fast path through `remember_person`; extracted facts,
   affiliations, and interactions continue through staged review.
3. The minimal instructions-string edit ships as the final M10.3 pull request.
