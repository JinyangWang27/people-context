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

**Anything derived from prior context always stages.** The M10 contract requires
information extracted from earlier conversation to remain pending review, so route
**all** prior-context-derived content through path 2 (staging) — even a pure identity
detail such as a newly mentioned nickname. The direct `remember_person` fast path in
path 1 is only for an assertion stated **directly in this invocation**, never for
content extracted from prior context.

Whichever path applies below, **resolve every person the request names first** with
`resolve_person` (see [Resolve people first](#resolve-people-first)) so an existing
person is updated, not duplicated.

**Precedence between the paths.** A single request can carry both an identity assertion
and a structured record — for example `Alice is an engineer at Acme` is both a person
and an affiliation. When the request contains any fact, affiliation, or interaction the
staged schema can represent, route that structured content through **path 2 (staging)**
so it is reviewable and queryable. Never fold it into a `remember_person` `summary`,
which would bypass review and leave affiliation/fact queries unaware of the record. The
path-1 summary fast path applies only to a pure identity assertion with no such
structured content. For a combined identity-and-structured request targeting one of
several identically-named people, do not imply that staging can apply both portions:
the dependent-only path below can record the structured portion only.

### 1. Explicit person assertion → `remember_person`

When the description is a pure identity assertion **stated directly in this invocation**
(not extracted from prior context) that a specific person exists or should be recorded —
a name, optionally aliases/nicknames, a one-line summary of who they are, and optionally
that they are the user (`is_self`) — and it carries no structured
fact/affiliation/interaction, it fits `remember_person`'s contract.
Resolve the person first; if a confident match exists, call `remember_person` with their
**canonical name** so the existing record is updated (its lookup matches only the exact
normalized `name`, not the supplied aliases, so a partial name like `Alice` would
otherwise create a second record beside `Alice Smith`). Call `remember_person` with
those fields only. If it returns `ambiguous_person`, the canonical name matches several
stored people and `remember_person` has **no `person_id` parameter**, so re-asking the
user to pick the name cannot complete the write — it only re-raises the same error.
Target the intended person by passing **any unique alias** as `name`: the lookup matches
every alias regardless of kind (nickname, handle, former name, …), so any alias that
resolves to exactly one person works. If no alias resolves uniquely, report that this
workflow cannot direct-write to one of several identically-named people instead of
looping on the error.

If it returns `self_already_exists`, do not treat the error as done — the user cannot
be resolved by a pronoun like "I", so a self assertion such as "I also go by John"
naturally hits this error. The error carries the existing self's canonical name. When
the assertion adds an alias or handle to the user (a new "also known as" / "goes by"),
**retry** `remember_person` against that existing canonical name with the new value in
`aliases`, so the alias is actually recorded on the self record. Only when there is
nothing new to add, tell the user that nothing was recorded because their self record
already exists.

**Never mark an unrelated contact as the user.** For a self-identity assertion like
"I am Jane" when no self record exists yet, do not blindly pass a resolved contact's name
to `remember_person` with `is_self=true`: if `Jane` resolves to an existing non-self
contact, `remember_person` would mark that contact as the user and corrupt both
identities (the self guard only blocks reuse when a self record already exists). Only set
`is_self=true` against a record that is already the user's self, or create a genuinely new
self record. If an existing same-named contact might be a different person, confirm with
the user first, or report that turning an existing same-named contact into the self is
unsupported — never guess.

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

**Every `person` candidate needs its mandatory `aliases` field, shaped as objects.**
`aliases` has no default, so a natural `{type, ref, name}` row is rejected with
`invalid_candidates`. Set `aliases: []` when the person has none. Each alias is an
**object** `{value, kind?, lang?, script?}` — a bare string list like `["Al"]` is
rejected. For matching, the stager checks `handle`-kind aliases and then checks the
person candidate's `name`; repository lookup for that `name` matches canonical names
and aliases of **every** kind. Therefore, put a verified unique nickname, former name,
or other non-handle alias in `name` when it must distinguish a shared canonical name.
A verified unique handle may instead bind through `aliases`, but it must carry
`kind: "handle"`, e.g. `{"value": "al-smith", "kind": "handle"}`.

**Preserve sensitivity for private content.** `fact` and `interaction` candidates
default `sensitivity` to `personal`, which ordinary `get_person_context` (and so `/who`)
discloses. Health, financial, and other private matters must be set to the appropriate
higher tier (e.g. `sensitive`) so the ordinary context path cannot later surface them;
never leave such content at the `personal` default.

**Never stage private affiliation-like content as an `affiliation`.** Affiliation
candidates have no `sensitivity` field and reject one as extra input, while ordinary
`get_person_context` returns every active affiliation without sensitivity filtering.
When the private statement can be represented faithfully as a fact — for example,
`patient_at=Mayo Clinic` — stage a `fact` with the appropriate `sensitive` or
`restricted` sensitivity instead. If converting it to a fact would distort its meaning,
report that the private affiliation-like information cannot be safely staged.
Do not emit an ungated affiliation.

**Interactions need a real occurrence date.** Each `interaction` candidate's `date` is
mandatory — there is no default. If the request does not say when it happened (for
example `Alice spoke with Bob`, with no time), ask the user for the date, or report that
the interaction cannot be staged yet. Never substitute the current time or otherwise
guess when it occurred.

**First-person references are the user, not a new person.** "I", "me", and "my" denote
the user's own self record. A staged `person` candidate has no `is_self` field, so
staging "I" would create a duplicate person literally named `I`. Handle self by role:

- **Self as an interaction participant** ("I met Alice"): omit the self participant —
  self is implicit, matching the importers — and stage only the resolved other people.
  If the only participant would be the user, there is nothing to stage; report that.
- **Self as a fact or affiliation subject** ("I live in London", "I am an engineer at
  Acme"): these need a `person_ref`, so self must be a person candidate that binds to
  the existing self record. Stage that candidate using the self record's **resolvable
  canonical name or any verified unique alias** (never a bare "I"), so the stager
  matches the existing self. Put a unique non-handle alias in the candidate's `name`;
  a unique handle may alternatively bind through `aliases` with `kind: "handle"`.
  If no canonical name or alias resolves uniquely, report the case as unsupported
  rather than staging a duplicate `I`.

### Resolve people first

Before either write, **resolve every referenced person** with `resolve_person`, parsing
any distinguishing context (organization, role, or relationship) into its `hints`
(`org`/`role`/`relationship`) rather than leaving it in `query` — the name index holds
names and aliases, not organizations. Use the resolved canonical identity (canonical
name when it resolves uniquely, or any verified unique alias) — in the
`remember_person` call for path 1, or in the `person` candidate's `name` for path 2.
For staging, a known unique handle may alternatively go in `aliases` with
`kind: "handle"`. Both the stager and `remember_person` use exact normalized repository
lookup, which matches canonical names and aliases of every kind, so a partial reference
like `Alice` would otherwise be recorded as a **new duplicate** of the stored
`Alice Smith`. If resolution is `ambiguous`, surface the candidates and let the user
choose before writing; do not guess. If resolution is empty, the person is genuinely
new — create or stage them as new.

A staged `person` candidate has **no `person_id` field**, so a person who shares a
normalized canonical name with another stored person needs care. Accepting such a person
candidate would fail: on commit the pipeline re-derives it by `canonical_name` and calls
`remember_person`, which raises `ambiguous_person` for the duplicate name even when a
handle bound the staged row. But a dependent record for that person **can** still be
committed when any alias identifies the intended record uniquely.
For a verified unique nickname, former name, or other non-handle alias, put that alias in
the person candidate's `name`; repository lookup matches every alias kind there and sets
`matched_person_id`. A unique handle may alternatively bind through the candidate's
`aliases` when marked `kind: "handle"`. Stage the dependent
(fact/affiliation/interaction) referencing that candidate, then at commit
**accept only the dependent row, leaving the matched person row unaccepted**. The
pipeline resolves the reference to the existing person from `matched_person_id` without
creating a person or re-minting the matched row, so the dependent commits cleanly. Only
when no alias resolves uniquely is staging a dependent for one of several
identically-named people unsupported — report that instead.

**Do not discard identity updates in a dependent-only commit.** The verified alias or
handle used above for binding must already belong to the matched person; it identifies
the record but does not update it. When the same request also asks to add a new alias,
name, summary, or other identity detail, leaving the person row unaccepted means that
`_existing_resolution` uses only its `matched_person_id`; the identity fields are not recorded.
Stage the supported dependent, but explicitly report that the identity portion is unsupported and was not recorded.
Do not present the whole proposal as committable, and do not put new identity data on
the unaccepted person row as if it will be applied.

That dependent-only path needs a dependent record. A **person-only** staged update to a
shared-canonical-name person — for instance a pure prior-context identity detail like a
new nickname for one of two people named `Sam`, with no accompanying fact, affiliation,
or interaction — has no dependent row to accept, and accepting the person row itself
raises `ambiguous_person`. That case cannot be committed through staging at all; report
it unsupported rather than offering it as a usable pending proposal.

The direct `remember_person` write in path 1 is different: it does not go through that
commit path, and its `name` lookup matches **any** alias kind, so a non-unique canonical
name can still be targeted by passing **any unique alias** (a nickname, handle, former
name, …) as `name`. Only when no alias resolves uniquely does path 1 report that it
cannot write to one of several identically-named people.

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
