# M10 — Agent utilization

Status: Planned. See [docs/roadmap.md](../roadmap.md#m10--agent-utilization).

## Motivation

The tools this milestone is meant to make more discoverable already exist and are already wired end to end:
`resolve_person`, `get_communication_guidance`, and `stage_candidates` are all registered in
`build_server()`'s `ToolDeps` (`src/people_context/adapters/mcp/server.py:194-247`) and documented in
[docs/mcp-interface.md](../mcp-interface.md). `ImportContent` already accepts an optional `CandidateStager`
(`src/people_context/app/import_content.py:316`), and `StageCandidates`
(`src/people_context/app/import_content.py:271`) is a thin, already-shipped wrapper around it. What is missing
is not capability but *guidance*: the only agent-facing instruction text today is the single
`SERVER_INSTRUCTIONS` string in `adapters/mcp/server.py:87-97`, which nudges `resolve_person` and
`get_person_context` but says nothing about `get_communication_guidance`, nothing about the
stage/review/commit approval flow, and nothing about when an agent should proactively stage what it learns.
The plugin today ships exactly `marketplace.json`, `mcp.json`, and `plugin.json` inside `.claude-plugin/`
(verified), and no `skills/`, `commands/`, or `hooks/` directory exists at the plugin root — which is where
Claude Code actually discovers them; only the manifests belong inside `.claude-plugin/`. So there is no
packaged guidance beyond that one instructions string and whatever a user happens to type.

This is precisely the "zero server changes" adoption path called out in the source analysis: the fix is
prompt/skill/command content shipped alongside the existing plugin, not new tools or new response fields.

## Scope

In scope:

- a packaged Claude Code skill teaching an agent when to call `resolve_person`, `get_communication_guidance`,
  and the `stage_candidates`/`review_import`/`commit_import` flow;
- user-invocable who/remember/reminders entry points (namespaced by the plugin name — `/people-context:who`
  etc.);
- an end-of-session capture instruction, delivered through the skill (no hook — see Design), that proposes —
  never silently performs — candidate staging;
- at most a small, additive extension of `SERVER_INSTRUCTIONS` mentioning the two under-used tools by name.

Non-goals:

- any new MCP tool, port, or app use case — every capability this milestone surfaces already ships;
- any change to `stage_candidates`'/`commit_import`'s approval semantics — staging remains proposal-only and
  commit remains an explicit, separate step, unchanged;
- an automatic/unattended commit path triggered by a hook — see Security below;
- changing the write-approval annotation on any existing tool (`stage_candidates` keeps its default
  write-approval `ToolAnnotations`, per `adapters/mcp/tools/imports.py:17,38`, not `readOnlyHint=true`).

## Design

### Skill: tool-selection guidance

A new skill under `skills/people-context-usage/` at the plugin root — Claude Code auto-discovers `skills/`,
`commands/`, and `hooks/` at the plugin root; only `plugin.json` and its sibling manifests live inside
`.claude-plugin/` — documents, in plain language grounded in the real contracts already published in
[docs/mcp-interface.md](../mcp-interface.md):

- resolve identity first (`resolve_person`) before assuming who a name refers to, matching the existing
  `SERVER_INSTRUCTIONS` guidance, restated with the ambiguity-handling contract (`ambiguous` field) spelled out;
- call `get_communication_guidance` (not `get_person_context`) when the task is *how* to communicate with
  someone, not *what is known about* them — the two tools return different, complementary shapes
  (`CommunicationGuidanceResult` vs. `PersonContextResult`);
- when the agent learns something durable about a person during a conversation (a fact, an affiliation, a
  completed interaction), call `stage_candidates` with the strict candidate vocabulary already defined in
  `app/import_content.py` (`person`/`interaction`/`affiliation`/`fact`) rather than either silently discarding
  it or fabricating a `remember_person`/`record_fact` call without the user's explicit review;
  raw conversation text is never a candidate field, matching the existing "agents must extract concise
  candidates from notes rather than submit or persist the notes themselves" rule
  ([docs/import.md](../import.md#agent-candidate-staging));
- never call `get_sensitive_person_context` or `export_data` speculatively — both are operator-gated and, per
  [docs/mcp-interface.md](../mcp-interface.md#operator-elevated-reads), absent from ordinary tool discovery
  unless the operator started the server with the matching environment flag; a skill that tells an agent to
  "try" a gated tool anyway would be actively wrong guidance, so the skill states explicitly that their absence
  from the tool list is expected, not a bug to work around.

### User-invocable who/remember/reminders

Three user-invocable entry points, each a short prompt template that instructs the agent to call one MCP tool
with the user's argument. Because plugin commands and skills are namespaced by the manifest name
(`people-context`), the actual invocations are `/people-context:who`, `/people-context:remember`, and
`/people-context:reminders` — the roadmap's short `/who` form is shorthand, not the literal user interface.
Claude Code has folded standalone commands into the skills mechanism (a skill with user-invocation metadata
serves the same purpose), so the preferred implementation is three small user-invocable skills at the plugin
root, with root-level `commands/*.md` files only as a compatibility fallback if the minimum supported Claude
Code version requires them:

- `/people-context:who <query>` → `resolve_person(query=...)`, then `get_person_context(person_id=...)` on an
  unambiguous single match, surfacing the `ambiguous`/candidate-list contract when resolution is not unique;
- `/people-context:remember <description>` → `remember_person` for an explicit new/updated person, or
  `stage_candidates` when the agent is extracting from prior conversation context rather than the user
  directly asserting a fact;
- `/people-context:reminders [person]` → `list_reminders`, optionally filtered by a resolved `person_id`.

Each entry point is a thin wrapper around one existing, already-documented tool contract; none introduces new
response shapes for the user to learn.

### End-of-session capture: skill instruction, no hook

A hook cannot itself decide *what* is worth remembering — that judgment requires the agent, not a
deterministic script — so this deliverable is a **prompt**, not a data-computing script. No hook lifecycle
event fits it, though: `SessionEnd` hooks support command/HTTP/MCP-tool side effects but cannot inject prompt
text back into a conversation that is already ending, and the only prompt-capable alternative, a `Stop`-event
hook, fires after **every** assistant turn rather than at session end — a global hook that nags for staging
after each response, plus loop-prevention complexity for the continuation turn its own prompt produces.

This milestone therefore ships the capture behavior **skill-only**: the skill instructs the agent that before
a session wraps up, it should review what it learned and call `stage_candidates` for anything durable. This
cannot loop, needs no lifecycle support, and fires with agent judgment about timing instead of on a mechanical
event. The prompt never calls `commit_import` itself and never bypasses the user review step — it only
increases the odds that something worth remembering gets staged for review later via `review_import`.

### `SERVER_INSTRUCTIONS` extension (optional, minimal)

`adapters/mcp/server.py:87-97` may gain one or two additional sentences naming `get_communication_guidance` and
`stage_candidates`, exactly as the existing string already names `resolve_person`, `get_person_context`,
`search_people`, and `remember_person`. This is a plain-string edit inside an adapter module with no
port/domain/app impact and no response-shape change.

## Migration needs

None.

## CLI / MCP surface changes

None. No new tool, no new CLI command, no response-shape change. `SERVER_INSTRUCTIONS` (if edited) is
server-provided prose, not a versioned contract field.

## Security / privacy considerations

- Every instruction this milestone adds must preserve the existing approval-gating philosophy verbatim: staging
  is proposal, review is inspection, commit is the only step that writes real rows
  ([docs/import.md](../import.md#extract-and-stage-model)). No skill, command, or hook text may instruct an
  agent to call `commit_import` without the user having reviewed `review_import`'s output first.
- No skill or command may reference or attempt to enable `get_sensitive_person_context` or `export_data`; both
  remain process-environment-gated (`PEOPLE_CONTEXT_MCP_ENABLE_SENSITIVE`, `PEOPLE_CONTEXT_MCP_ENABLE_EXPORT`)
  and that gate is enforced in `adapters/mcp/security.py:process_elevation_enabled`, not by prompt discipline —
  this milestone's guidance reinforces, but is not a substitute for, that process-level boundary (see
  [docs/privacy-and-safety.md](../privacy-and-safety.md#writes-and-destructive-operations-are-annotated-for-client-side-gating)).
- The end-of-session capture instruction must not persist or transmit session transcript content anywhere
  outside the normal `stage_candidates` call it prompts for, matching the "never log private values" rule in
  `AGENTS.md` — and raw conversation text is not a candidate field, so nothing in the strict candidate
  vocabulary can carry it.
- The user-invocable entry points must not widen disclosure: who and reminders only ever call tools already available at
  the default (non-elevated) MCP surface, so their behavior is identical whether or not the operator has set
  either elevation environment variable.

## Testing strategy

- No new Python code is introduced unless `SERVER_INSTRUCTIONS` is edited, in which case existing tests that
  assert on that string (if any exist in `tests/adapters/test_mcp_server.py` or
  `tests/adapters/test_mcp_entrypoint.py`) must be checked and updated for the new wording.
- Plugin validation: extend the existing `claude plugin validate . --strict` step
  ([docs/claude-code-plugin.md](../claude-code-plugin.md#local-validation)) to cover the new root-level
  `skills/` (and, if added, `commands/` and `hooks/`) paths.
- Manual interactive verification, following the existing "Local validation" procedure exactly
  ([docs/claude-code-plugin.md](../claude-code-plugin.md#local-validation)): install locally, `/reload-plugins`,
  then exercise `/people-context:who`, `/people-context:remember`, and `/people-context:reminders` against a
  temporary database, and confirm the skill changes observed tool-selection behavior in a scripted transcript
  (agent calls `resolve_person` before
  assuming an identity; agent proposes `stage_candidates` rather than fabricating a write).

## Open questions

1. What minimum Claude Code version should the plugin require for user-invocable skills (versus shipping
   root-level `commands/*.md` fallbacks), and does the marketplace manifest need a version constraint bump from
   the currently documented 2.1.196 floor?
2. Should `/people-context:remember` attempt to disambiguate "explicit user assertion" (→ `remember_person`)
   from "agent extraction from context" (→ `stage_candidates`) automatically, or should the command always
   route through staging for consistency, accepting one extra review step even for explicit assertions?
3. Should this milestone's `SERVER_INSTRUCTIONS` edit ship independently of the skill work (since it is the
   only piece touching versioned server code), or stay bundled with the rest of the plugin release?
