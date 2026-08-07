---
name: people-context-usage
description: Use the people-context MCP tools correctly when the user mentions someone in their life, asks who a person is, wants durable context or communication guidance about a contact, is preparing for a meeting or call with named attendees, or shares information worth remembering about people. Covers identity resolution first, context vs. guidance, meeting preparation, the strict staged-capture vocabulary, and the review-before-commit approval flow.
---

# Using people-context

people-context is a local-first store of durable knowledge about the people in the
user's life: their names and aliases, how they relate to the user, their
organisations and roles, and relevant past interactions. These tools compose into a
few reliable patterns. Follow them instead of guessing.

## Resolve identity first

When the user names, nicknames, or partially references a person, call
`resolve_person` **before** reading context and before asking the user who they mean.

`resolve_person` returns an explainable result. When it reports an `ambiguous`
outcome with a candidate list, preserve that contract: present or narrow the
candidates and let the user choose. Never silently pick one candidate, and never
fabricate an identity the pipeline did not return. Use `search_people` for broader
browsing when the user is exploring rather than pointing at one person.

## Read context, then guidance

These answer two different questions:

- `get_person_context` answers **what is known** — a bounded, sensitivity-aware
  bundle of identity, relationships, affiliations, facts, and recent interactions.
- `get_communication_guidance` answers **how to communicate** — tone and approach
  derived from the stored communication philosophy.

Resolve the person first, then call the tool that matches the question. When the user
wants help writing to or preparing for someone, `get_communication_guidance` is the
right tool; do not infer tone from raw context alone.

## Preparing for a meeting or conversation

When the user is about to meet, call, or write to one or more people, build the brief
from resolved records rather than from memory or guesswork:

1. Resolve every attendee with `resolve_person`, one name at a time. When a name comes
   back `ambiguous`, present the candidates and let the user pick before you read
   anything; never guess which attendee was meant, and never invent one.
2. For each resolved person, call `get_person_context` for the bounded,
   sensitivity-aware view of who they are, how they relate to the user, and what
   happened recently.
3. Call `get_communication_guidance` for each of them when the user wants help with
   tone, framing, or approach. Context says what is known; guidance says how to
   communicate. Do not infer tone from context alone.
4. Call `list_reminders` with that `person_id` to surface the open follow-ups and
   communication notes already recorded for them.
5. Compose one short brief per attendee: who they are, how they relate to the user,
   what happened last, open follow-ups, and how to communicate with them.

Preparation is a read-only flow. Do not stage or record anything the meeting has not
produced yet, and do not treat a thin brief as a reason to reach for elevated tools —
what `get_person_context` returns is the intended complete ordinary view. After the
meeting, the end-of-session capture rules below apply unchanged: propose with
`stage_candidates`, and leave the commit to the user.

## Capturing new knowledge: propose, review, then commit

There are two ways durable knowledge enters the store, and both keep the user in
control.

- An explicit, well-formed person assertion ("remember my colleague Dana Okafor,
  dana@example.com") fits `remember_person` directly.
- Everything extracted from notes, prior conversation, or other agent-visible text —
  facts, affiliations, interactions, and newly mentioned people — goes through the
  staged capture flow, never through a direct write.

The staged flow has three distinct steps. Keep them distinct:

1. `stage_candidates` is a **proposal**. It validates and atomically stages
   candidates for later review. It does not persist durable records.
2. `review_import` is **inspection**. It returns the staged candidates and their
   statuses for a batch so the user can see exactly what would be written.
3. `commit_import` is an **explicit, later write**. Call it only after the user has
   reviewed a batch and explicitly accepted specific candidates. Never call
   `commit_import` automatically, speculatively, or in the same breath as staging.

### Use only the strict candidate vocabulary

`stage_candidates` accepts exactly four candidate `type`s. Nothing else validates:

- `person` — `ref`, `name`, and strict `aliases`; optional `summary`, `message_id`,
  `date`.
- `interaction` — `summary`, `participant_refs` (batch-local person `ref`s), `date`;
  optional `channel`, `message_id`, `sensitivity`.
- `affiliation` — `person_ref`, `org`, `role`; optional `valid_from`, `valid_to`,
  `confidence`.
- `fact` — `person_ref`, `predicate`, `value`; optional `valid_from`, `valid_to`,
  `confidence`, `sensitivity`.

References are **batch-local**: an `interaction`, `affiliation`, or `fact` points at a
`person` candidate's `ref` within the same `stage_candidates` call. Extract concise,
structured field values only. Never copy raw conversation, transcript, note, or email
body text into any candidate field; summarise it into the strict fields above.

## Disclosure gates are expected, not obstacles

The ordinary tool surface deliberately omits `get_sensitive_person_context` and
`export_data`. Their absence is a process-level privacy gate the operator controls,
not a bug to work around. Do not attempt to reach sensitive or restricted records, do
not suggest enabling those tools to get around a boundary, and treat what
`get_person_context` returns as the intended, complete ordinary view.

## Near the end of a session: review learnings, propose capture

When a session is naturally wrapping up, briefly review what you genuinely learned
about people during it — a durable fact, a role change, a meaningful interaction — and
consider proposing it with `stage_candidates` so the user can review it later.

This is a best-effort review, not a guaranteed mechanical step, and it stays inside
the same rules:

- propose with `stage_candidates` only; never call `commit_import`;
- stage concise structured candidates, never raw transcript text;
- skip it entirely when nothing durable was learned — an empty proposal is worse than
  none.
