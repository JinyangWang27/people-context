# 0005. Conservative field-level conflict resolution

## Status

ACCEPTED.

## Context

Single-user multi-device sync permits concurrent offline changes. A deterministic tie-breaker is necessary, but
blind row-level last-writer-wins can damage identity resolution, lower sensitivity, resurrect forgotten data, or
silently choose one side of a merge race.

The complete policy is in [the sync design](../design/sync.md#4-conflict-resolution). The design uses hybrid
logical clocks for deterministic ordering, structural merge where the data model has a safe set interpretation,
and user review where an automatic answer could attach data to the wrong person.

## Decision

Use field-level last-writer-wins, ordered by hybrid logical clock plus device and operation tie-breakers, as the
default for reversible non-identity scalar fields.

Override that default as follows:

- union aliases by normalized value when they still identify the same person;
- merge concurrent changes to distinct fields on the same row;
- resolve sensitivity to the more restrictive value;
- keep reminder completion monotonic unless an explicit restore operation exists;
- require user review for divergent canonical names, `is_self` conflicts, normalized aliases attached to
  different people, merge-vs-edit or merge-vs-merge races, participant-set changes, and correct-vs-correct;
- make forget dominate every ordinary create, update, correction, merge, or replayed stale operation.

A replica must record unresolved identity conflicts separately and exclude ambiguous results from normal identity
resolution until reviewed. Automatic convergence is subordinate to preserving identity correctness and privacy.

M6 implements the local HLC and conflict table foundation. Conflict replay, review UI, exchange, and bootstrap remain deferred to M7.

## Consequences

- Most independent edits converge without user intervention.
- Alias additions survive concurrent work instead of being overwritten as one list value.
- Privacy does not weaken because a later but less restrictive sensitivity value arrived.
- Identity ambiguity is visible rather than silently resolved by clock order.
- Replicas need per-field operation metadata or an equivalent conflict index.
- Some offline edits remain pending until a user reviews them.
- A future multi-user mode must add authenticated actor and ownership semantics before reusing this policy.

## Alternatives considered

- **Row-level last-writer-wins.** Simple, but it discards unrelated concurrent field changes and can silently
  corrupt identity decisions.
- **CRDTs for every entity.** Provides strong convergence properties, but the model contains semantic operations
  such as merge, correction, and forget that still require domain policy and review.
- **Always ask the user.** Safest semantically, but creates excessive review work for independent scalar edits.
- **Server-authoritative serialization.** Conflicts are reduced only while online and would make a hosted service
  a required authority, contrary to the local-first goal.

## Invariant before acceptance

No automatic rule may reduce identity-resolution correctness, create two self rows for one owner, lower
sensitivity, or revive forgotten content.
