# Communication Guidance and Reminders

This document describes how `people-context` supports communication guidance — helping the user figure
out how to communicate with a specific person, in the user's own preferred style — and person-linked
reminders. Both are implemented in **M2** (see [docs/roadmap.md](roadmap.md)); their data lives in the
M0 schema (`traits`, `reminders`, `user_preferences` — see
[docs/data-model.md](data-model.md)).

## Traits

Traits are derived, structured characteristics about a person, distinct from raw observations (see
[docs/data-model.md](data-model.md#facts-vs-observations-vs-traits)). Each trait belongs to one category:

| Category | Meaning |
|---|---|
| `communication_style` | How this person prefers to communicate — channel, tone, directness, length. |
| `temperament` | General disposition — patient, high-energy, reserved, etc. |
| `values` | What this person cares about or prioritizes. |
| `preference` | Concrete likes/dislikes relevant to interacting with them. |
| `topics_to_avoid` | Subjects to steer away from. |

Every trait carries `confidence`, `sensitivity`, `provenance`, and an `evidence_note` referencing the
observation(s) or interaction(s) it was distilled from — traits should be traceable back to why the system
believes them, not asserted from nowhere.

## Division of labour: server stores signal, client composes advice

The server's job stops at supplying structured context; it never generates communication advice itself:

- **The server stores structured signal**: a person's traits (with evidence and confidence), relevant
  relationship/role context, recent interaction friction notes, active `communication_note` reminders for
  that person, and the user's own communication philosophy text.
- **The client LLM composes the actual advice**, in whatever framing the user has configured — principles
  from 周易 (I Ching) or 道德经 (Tao Te Ching), a personal style guide, company communication norms, or any
  other free-text framework the user writes. The server never hardcodes a philosophy or a style of advice.

This split keeps the server deterministic, testable, and free of any opinion about *how* to communicate,
while still letting any MCP client render advice in its own voice, matched to the user's stated values.

## `set_communication_philosophy` / `get_communication_guidance` flow

1. The user (or an agent on the user's behalf) calls `set_communication_philosophy(text)` once, storing
   free-text guidance under the `communication_philosophy` key in `user_preferences` (see
   [docs/data-model.md](data-model.md#user_preferences)). This can be edited at any time; there is no fixed
   schema for the text itself.
2. When the user is about to communicate with a specific person, an agent calls
   `get_communication_guidance(person_id, situation?)`. The server assembles and returns a bundle
   containing:
   - the person's traits (grouped by category),
   - relevant relationship/role context for that person,
   - up to five recent interaction friction notes (newest first, drawn from interaction summaries),
   - any active `communication_note` reminders for that person,
   - the user's `communication_philosophy` text, verbatim (`null` plus `philosophy_set: false` when unset),
   - the caller's `situation`, echoed unchanged.
3. The calling LLM composes advice from that bundle, in the user's own framing, for the specific
   `situation` described (if given).

Both tools are described in [docs/mcp-interface.md](mcp-interface.md); `set_communication_philosophy` is a
write tool, `get_communication_guidance` is read-only.

The implemented guidance path never returns observations. Traits and interactions marked `sensitive` or
`restricted` are also excluded; M2 deliberately has no `include_sensitive` override on this tool.

## Reminders

Reminders are person-linked and come in three kinds:

| Kind | Meaning | Uses `due_at` |
|---|---|---|
| `follow_up` | A concrete thing to follow up on with this person. | Yes |
| `occasion` | A recurring date-based reminder (birthday, anniversary, etc.). | Yes, with `recurrence` |
| `communication_note` | A standing note to surface *whenever this person comes up* — no due date. | No |

Reminders are **pull-based** in v1: there is no server-side scheduler or notification daemon. `list_reminders`
lets any agent — a Claude Code heartbeat, a scheduled Routine, or any other polling mechanism — check for due
follow-ups/occasions on its own cadence, typically at session start. `communication_note` reminders are
additionally surfaced automatically inside `get_person_context` and `get_communication_guidance` responses
for the relevant person, since they are meant to be seen whenever that person is discussed, not on a
schedule. A notification daemon that pushes reminders proactively is a possible future concern (M4+), not
part of this design.

## Privacy treatment of traits

Traits are treated the same way observations are, for privacy purposes (see
[docs/privacy-and-safety.md](privacy-and-safety.md)):

- **Subjective** — labelled as derived characteristics, never presented as objective fact.
- **Correctable** — via `correct_record`, the same mechanism used for facts, relationships, and
  affiliations.
- **Forgettable** — subject to `forget` like any other record.
- **Excluded from default context** — `get_person_context` does not include traits unless the request's
  `purpose` is communication-related; `get_communication_guidance` is the tool that surfaces them, since
  that is the one context where they are the point.

See [docs/mcp-interface.md](mcp-interface.md) for the exact tool signatures, and
[docs/data-model.md](data-model.md#traits) for the underlying schema.
