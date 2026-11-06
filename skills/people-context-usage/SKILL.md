---
name: people-context-usage
description: Use the people-context MCP tools whenever the user mentions someone in their life, asks who a person is, prepares for a meeting or conversation, shares something worth remembering about a person, hands over a transcript, CV, or notes to capture from, or asks to review or tidy what is stored. Covers resolving identity first, context vs. guidance, and propose-review-commit capture.
---

# Using people-context

people-context is a local-first store of durable knowledge about the people in the
user's life: their names and aliases, how they relate to the user, their
organisations and roles, and relevant past interactions. These tools compose into a
few reliable patterns. Follow them instead of guessing.

## When a tool is not advertised

A server started with `--tools core` advertises only the everyday tools: identity,
context and guidance, `remember` capture, aliases, correction and supersession,
`forget`, the staged-import review gate, and reminders. Graph, group, consolidation,
semantic search, `import_content`, `merge_people`, and the per-record `record_*`,
`set_affiliation`, and `set_relationship` writes need the full set. If a workflow
below names a tool you cannot see, do not imitate it with other tools: say it needs
the server started with `--tools all`, and use `remember` for a single direct fact,
affiliation, or relationship.

## Resolve identity first

When the user names, nicknames, or partially references a person, call
`resolve_person` **before** reading context and before asking the user who they mean.

`resolve_person` returns an explainable result. When it reports an `ambiguous`
outcome with a candidate list, preserve that contract: present or narrow the
candidates and let the user choose. Never silently pick one candidate, and never
fabricate an identity the pipeline did not return.

Read `match_reason`, not just the score. A reason starting with `fuzzy` means no
stored name matches what was typed and the resolver fell back to spelling distance,
so a lone `fuzzy` candidate is a suggestion to confirm rather than an identification
— treat it like `ambiguous`. A matched hint appends `+hint:…` and raises the score,
so a high score alone does not make a near-spelling into a match. Use `search_people` for broader
browsing when the user is exploring rather than pointing at one person.

## Read context, then guidance

These answer two different questions:

- `get_person_context` answers **what is known** — a bounded, sensitivity-aware
  bundle of identity, relationships, affiliations, facts, and recent interactions.
  Its `truncated` flag says the item budget cut the list. Sensitive and restricted
  records leave no trace at all, by design: what you get back is the intended
  complete ordinary view, not a redacted one.
- `get_communication_guidance` answers **what is known about communicating with them** —
  the person's traits, roles, recent interaction summaries, active communication notes,
  and the user's own philosophy text, returned as stored. The server composes no tone and
  recommends no approach; the advice is yours to write from that material. `situation` is
  echoed back unchanged rather than used to select or rank anything, and `friction_notes`
  holds recent ordinary-disclosure interaction summaries whether or not friction occurred,
  so a field name is not evidence that friction happened.

Resolve the person first, then call the tool that matches the question. When the user
wants help writing to or preparing for someone, `get_communication_guidance` is the
right tool; do not infer tone from raw context alone. Both reads, like `list_reminders`,
`get_person_timeline`, and `upcoming_dates`, also accept `person` (the name as said)
in place of `person_id`; a name that is ambiguous, or that only a near-spelling
matches, then returns candidates instead of data, so the resolution contract holds
either way. Surface those candidates and let the user choose, exactly as you would
for `resolve_person`.

## Reference files: read the one the task needs

This file covers what every session needs. Before a specialised workflow, read its reference file in full;
the rules there are not repeated here.

| Before you... | Read |
|---|---|
| prepare for a meeting, coach a real conversation, or describe how someone thinks | [references/conversations.md](references/conversations.md) |
| stage, amend, withdraw, or commit candidates, or extract from a transcript or note | [references/staged-capture.md](references/staged-capture.md) |
| capture from a CV, biography, or notes, a shared context, or explain how two people know each other | [references/documents.md](references/documents.md) |
| correct, supersede, consolidate, or review a newer CV against stored records | [references/maintenance.md](references/maintenance.md) |

## Capturing new knowledge: propose, review, then commit

There are two ways durable knowledge enters the store, and both keep the user in
control.

- An explicit, well-formed person assertion ("remember my colleague Dana Okafor,
  dana@example.com") fits `remember_person` directly.
- One durable thing the user **states directly** about one person — "Dana from Acme
  prefers short emails", "Bob is my manager", "I had coffee with Dana today" — fits
  `remember` in a single call: it resolves the name, creates the person only when
  nobody matches, and records the note, affiliation, or relationship in one audited
  transaction. It never guesses between similar names: `status: ambiguous` or
  `unconfirmed` returns candidates and records nothing, so ask and call again with the
  exact name. Set `sensitivity` to `sensitive` or `restricted` for private matters, as a
  `note`; an elevated level with `org` or `relationship` is refused, because those rows
  carry no sensitivity and every ordinary read discloses them.
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

Keep the round trips few. Stage everything one source yields in a single
`stage_candidates` call, and commit an accepted batch in a single `commit_import`. Its
receipt is the verification: report what was committed, anything in `unresolved_ids` or
`skipped_ids`, and any refusal. Read records back only for those ids, or when the user asks
to see them.


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
