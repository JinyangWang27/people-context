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
`.claude-plugin/` today contains exactly `marketplace.json`, `mcp.json`, and `plugin.json` (verified — no
`commands/`, `skills/`, or `hooks/` directory exists yet), so there is no packaged guidance beyond that one
instructions string and whatever a user happens to type.

This is precisely the "zero server changes" adoption path called out in the source analysis: the fix is
prompt/skill/command content shipped alongside the existing plugin, not new tools or new response fields.

## Scope

In scope:

- a packaged Claude Code skill teaching an agent when to call `resolve_person`, `get_communication_guidance`,
  and the `stage_candidates`/`review_import`/`commit_import` flow;
- slash commands `/who`, `/remember`, `/reminders`;
- an optional session-end hook that prompts (never silently performs) candidate staging;
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

A new skill under `.claude-plugin/skills/people-context-usage/` (directory name illustrative; see Open
Questions for the exact manifest wiring) documents, in plain language grounded in the real contracts already
published in [docs/mcp-interface.md](../mcp-interface.md):

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

### Slash commands

Three new command files under `.claude-plugin/commands/` (`who.md`, `remember.md`, `reminders.md`), each a
short prompt template that instructs the agent to call one MCP tool with the user's argument:

- `/who <query>` → `resolve_person(query=...)`, then `get_person_context(person_id=...)` on an unambiguous
  single match, surfacing the `ambiguous`/candidate-list contract when resolution is not unique;
- `/remember <description>` → `remember_person` for an explicit new/updated person, or `stage_candidates` when
  the agent is extracting from prior conversation context rather than the user directly asserting a fact;
- `/reminders [person]` → `list_reminders`, optionally filtered by a resolved `person_id`.

Each command is a thin wrapper around one existing, already-documented tool contract; none introduces new
response shapes for the user to learn.

### Optional session-end capture hook

Claude Code's hook mechanism can run a script at defined lifecycle points, but a shell hook cannot itself decide
*what* is worth remembering — that judgment requires the agent, not a deterministic script. This milestone
therefore designs the hook as a **prompt injection**, not a data-computing script: at session end, the hook
appends an instruction asking the agent to review what it learned in the session and call `stage_candidates`
for anything durable, exactly the same call the skill already describes. The hook never calls
`commit_import` itself and never bypasses the user review step; it only increases the odds that something
worth remembering gets staged for the user to review later via `review_import`. If Claude Code's hook contract
cannot deliver prompt text back into the conversation at session end (see Open Questions), this deliverable
degrades gracefully to being purely a skill instruction ("before ending a session, consider staging what you
learned") with no separate hook file at all.

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
- The session-end hook must not persist or transmit session transcript content anywhere outside the normal
  `stage_candidates` call it prompts for; the hook script itself (if implemented as a script rather than pure
  prompt injection) must not read or log conversation content, matching the "never log private values" rule in
  `AGENTS.md`.
- Slash commands must not widen disclosure: `/who` and `/reminders` only ever call tools already available at
  the default (non-elevated) MCP surface, so their behavior is identical whether or not the operator has set
  either elevation environment variable.

## Testing strategy

- No new Python code is introduced unless `SERVER_INSTRUCTIONS` is edited, in which case existing tests that
  assert on that string (if any exist in `tests/adapters/test_mcp_server.py` or
  `tests/adapters/test_mcp_entrypoint.py`) must be checked and updated for the new wording.
- Plugin validation: extend the existing `claude plugin validate . --strict` step
  ([docs/claude-code-plugin.md](../claude-code-plugin.md#local-validation)) to cover the new `commands/`,
  `skills/`, and (if added) `hooks/` paths.
- Manual interactive verification, following the existing "Local validation" procedure exactly
  ([docs/claude-code-plugin.md](../claude-code-plugin.md#local-validation)): install locally, `/reload-plugins`,
  then exercise `/who`, `/remember`, and `/reminders` against a temporary database, and confirm the skill
  changes observed tool-selection behavior in a scripted transcript (agent calls `resolve_person` before
  assuming an identity; agent proposes `stage_candidates` rather than fabricating a write).
- If the session-end hook ships as an actual script, add a narrow adapter-free test harness (a fixture that
  invokes the hook script directly and asserts it only emits prompt text, performs no filesystem/network I/O
  beyond what Claude Code's hook contract requires, and never imports or calls into `people_context` directly).

## Open questions

1. What are the current, exact Claude Code plugin manifest keys and directory conventions for `commands/`,
   `skills/`, and `hooks/`? `plugin.json` today only demonstrates the `mcpServers: "./mcp.json"` pointer
   pattern; the equivalent pointers (or auto-discovery rules) for commands/skills/hooks must be confirmed
   against Claude Code's current plugin schema before implementation, not assumed from this spec.
2. Does Claude Code's session-end hook lifecycle support injecting a follow-up prompt back into the same
   conversation, or is it limited to fire-and-forget side effects (logging, notifications)? This determines
   whether the "optional session-end hook" deliverable is a real hook file or purely a skill-level instruction.
3. Should `/remember` attempt to disambiguate "explicit user assertion" (→ `remember_person`) from "agent
   extraction from context" (→ `stage_candidates`) automatically, or should the command always route through
   staging for consistency, accepting one extra review step even for explicit assertions?
4. Should this milestone's `SERVER_INSTRUCTIONS` edit ship independently of the skill/command/hook work (since
   it is the only piece touching versioned server code), or stay bundled with the rest of the plugin release?
