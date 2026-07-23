---
name: who
description: Resolve who a person is with the people-context tools. Invoke as /people-context:who <query> to identify a name, nickname, or partial reference and, only on a confident resolution, read that person's stored context.
disable-model-invocation: true
argument-hint: <name | nickname | partial reference>
---

# /people-context:who

A thin, user-invoked workflow that answers "who is this person?" by composing the
existing read-only people-context tools. It resolves identity first and reads
context only when the resolution is confident.

The user asked about: **$ARGUMENTS**

## Steps

1. **Resolve first.** Call `resolve_person` with the query above. Do not ask the
   user who they mean before resolving, and do not call any other people-context
   tool first.

   Parse any distinguishing context out of the query into `resolve_person`'s `hints`
   rather than leaving it all in `query` — the name index holds names and aliases, not
   organizations, so `who Alex at Acme` resolves best as `query: "Alex"` with
   `hints: {"org": "Acme"}`. `hints` accepts `org`, `role`, and `relationship` and can
   improve ranking. But hints do **not** reliably break a tie between two equally matched
   same-name people: exact-name matches stay tied at score `1.0`, and a single hint
   dimension's boost is smaller than the ambiguity gap, so the result can remain
   `ambiguous` (documented resolver behaviour). Do not loop on hints expecting them to
   resolve such a tie — a **unique alias** is the definitive selector, and when the
   result is still `ambiguous`, surface the candidates for the user to choose rather than
   guessing.

2. **Branch on the resolver's own `ambiguous` flag — not on candidate count.**
   `resolve_person` returns ranked `candidates` and an `ambiguous` boolean. The flag,
   not the number of candidates, decides confidence: the resolver may return several
   ranked candidates yet report `ambiguous: false` when the top match is confidently
   ahead of the rest.

   - **`ambiguous: false` with at least one candidate:** the ranked top candidate
     (`candidates[0]`) is the confident resolution. Call `get_person_context` once
     with its `person_id` to report what is known — narrow identity, active
     relationships and affiliations, and the recent facts/interactions slice. This is
     the only case in which a second read happens.
   - **`ambiguous: true`:** stop and present the returned candidate list with each
     candidate's score and match reason. Let the user choose or add distinguishing
     detail. Never silently pick a candidate, and never call `get_person_context` for
     a guessed identity.
   - **Empty candidate list** (no confident match): report that no one matching is
     stored. Do not read context and do not invent an identity. You may note that
     `/people-context:remember` can record them.

## Boundaries

- Read-only workflow: it calls only `resolve_person` and, on a confident resolution,
  `get_person_context`. It performs no writes.
- It never calls or suggests enabling the gated `get_sensitive_person_context` or
  `export_data` tools; the ordinary context bundle is the intended, complete view.
- Preserve the `ambiguous` candidate-list contract exactly — surfacing candidates is
  the correct outcome, not a failure to work around.
