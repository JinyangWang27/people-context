---
name: remember
description: Record durable knowledge about a person with the people-context tools. Invoke as /people-context:remember <description> to make an explicit person assertion via remember_person, or to stage extracted facts, affiliations, and interactions for later review.
disable-model-invocation: true
argument-hint: <what to remember about a person>
---

# /people-context:remember

A thin, user-invoked workflow that captures durable knowledge about people. It
routes the request down one of the existing paths below and keeps the user in
control of every write.

The user wants to remember: **$ARGUMENTS**

## Act on what the user asked to remember

Work from the invocation above. When the description **explicitly** points at prior
context — for example `/people-context:remember what I just said about Alice's
promotion`, which refers to earlier conversation — consult that referenced context to
construct concise candidates; that
targeted, user-requested capture is exactly what this workflow is for. What stays out
of scope is *automatic* extraction: do not trawl the conversation for unrelated things
to persist, and do not apply classification heuristics beyond the explicit request.

Whichever path applies below, **resolve every person the request names first** with
`resolve_person` (see [Resolve people first](#resolve-people-first)) so an existing
person is updated, not duplicated.

### 1. Explicit person assertion → `remember_person`

When the description is a direct assertion that a specific person exists or should be
recorded — a name, optionally aliases/nicknames, a one-line summary of who they are,
and optionally that they are the user (`is_self`) — it fits `remember_person`'s
contract. Resolve the person first; if a confident match exists, call `remember_person`
with that person's **canonical name** so the existing record is updated (its lookup
matches only the exact normalized `name`, not the supplied aliases, so a partial name
like `Alice` would otherwise create a second record beside `Alice Smith`). Call
`remember_person` with those fields only. If it returns `ambiguous_person`, the
canonical name matches several stored people and `remember_person` has **no `person_id`
parameter**, so re-asking the user to pick the name cannot complete the write — it only
re-raises the same error. Target the intended person by passing their **unique handle**
as `name` (its lookup also matches handles), and if they have no unique handle, report
that this workflow cannot direct-write to one of several identically-named people — the
same targeting limitation as staging — instead of looping on the error.

If it returns `self_already_exists`, do not treat the error as done — the user cannot
be resolved by a pronoun like "I", so a self assertion such as "I also go by John"
naturally hits this error. The error carries the existing self's canonical name. When
the assertion adds an alias or handle to the user (a new "also known as" / "goes by"),
**retry** `remember_person` against that existing canonical name with the new value in
`aliases`, so the alias is actually recorded on the self record. Only when there is
nothing new to add, tell the user that nothing was recorded because their self record
already exists.

### 2. Extracted facts, affiliations, or interactions → resolve first, then stage

Facts (predicate/value), affiliations (org/role), and interactions do **not** fit
`remember_person`. They go through staging, using only the strict `person` /
`interaction` / `affiliation` / `fact` vocabulary with batch-local `ref`s. Resolve
referenced people first (below), then call `stage_candidates` with a fixed
non-content `source` label — use `claude-code-remember`. **Never** pass the
description, `$ARGUMENTS`, or any personal statement as the `source`: the stager
persists that value as durable provenance on every staged row, so a raw statement
there would leak into stored records. Likewise never copy raw conversation, note, or
transcript text into a candidate field.

### Resolve people first

Before either write, **resolve every referenced person** with `resolve_person` and use
the resolved canonical identity (canonical name, or a known handle) — in the
`remember_person` call for path 1, or in the `person` candidate for path 2. Both the
stager and `remember_person` match an existing person only by an exact normalized name
or handle, so a partial reference like `Alice` would otherwise be recorded as a **new
duplicate** of the stored `Alice Smith`. If resolution is `ambiguous`, surface the
candidates and let the user choose before writing; do not guess. If resolution is
empty, the person is genuinely new — create or stage them as new.

A staged `person` candidate can carry only a `name` and `aliases` — it has **no person
id field** — and the stager binds it to an existing record only when that name or a
handle matches exactly one person. So when two stored people share the same normalized
canonical name, the resolved `person_id` cannot be represented in a candidate and the
name alone is ambiguous: prefer putting the resolved person's **unique handle** in the
candidate's `aliases` so the batch binds to exactly them. If the resolved person shares
a normalized canonical name with another and has no unique handle, staging cannot
target them unambiguously and the batch would fail to commit — report this limitation
instead of staging an uncommittable batch. The direct `remember_person` write in path 1
has the **same limitation** (it has no `person_id` parameter either), so apply the same
rule there: target by a unique handle, or report that the workflow cannot write to one
of several identically-named people.

### 3. Anything that fits neither path → report the limitation, do not force it

Some requests fit neither `remember_person` nor the strict staging vocabulary. A
**relationship** ("Alice is my sister") is the common case: staging has no
relationship candidate type, so emitting one fails validation, and flattening it into
a generic fact hides the edge from the relationship graph. Do **not** force such a
request into staging. Tell the user plainly that this workflow records people
(`remember_person`) and facts/affiliations/interactions (staged for review) only, and
that relationships and other unsupported data are not captured here.

## Staging stays a proposal

`stage_candidates` only proposes. After staging, tell the user the batch is pending
and can be inspected with `review_import`. **Do not** call `commit_import` — the
commit is an explicit, later, user-approved step performed after the user reviews the
batch. Never commit automatically or in the same breath as staging.

## Boundaries

- Uses only `resolve_person`, `remember_person`, and `stage_candidates` (and
  `review_import` for inspection). It never calls `commit_import`.
- It never calls or suggests enabling the gated `get_sensitive_person_context` or
  `export_data` tools.
- It does not misuse `remember_person` to encode facts, relationships, or
  interactions that tool cannot represent, and it does not force unsupported data
  (such as relationships) into the staging schema.
