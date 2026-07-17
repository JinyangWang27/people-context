# 0004. Dedicated changelog instead of replaying the audit log

## Status

ACCEPTED.

## Context

M5 evaluates whether the existing `audit_log` can serve as the source for multi-device replication. The full
assessment is in [the sync design](../design/sync.md#2-fitness-of-the-current-audit-log-as-a-replication-source).

The audit trail was designed for local accountability, not lossless replay. Its payload contract deliberately
permits concise summaries where full content is unnecessary. Current examples include communication-philosophy
entries that store lengths rather than text, person updates that omit complete alias rows, and forget tombstones
that store counts rather than deleted row ids.

Lifecycle operations also challenge the original append-only description. Merge and forget each summarize a
multi-row transaction in one entry. Forget then rewrites matching earlier payloads to `{"redacted": true}`. A
replication source needs exact row outcomes, device identity, deterministic ordering, idempotency metadata, and a
replicated way to make already-synced replicas forget prior content.

## Decision

Add a dedicated `changelog` table in a future implementation milestone. Write changelog entries in the same
SQLite transaction as the primary mutation and the user-facing audit entry.

The changelog would carry full replay payloads, origin device identity, a hybrid logical clock, transaction
membership, changed-field metadata, payload schema version, and deterministic lifecycle manifests. Ordinary
entries would be append-only. Forget would be an explicit exception that redacts or removes covered payloads and
retains a minimal propagation tombstone.

Keep `audit_log` as the accountability trail. Its payloads may remain intentionally concise and may continue to
be redacted in place by forget. Do not require the audit payload and changelog payload to be identical.

M6 implements the local table, migration, transactional writer integration, and inspection surface. Exchange, pairing, replay, and bootstrap remain deferred to M7.

## Consequences

- Replay fidelity no longer depends on privacy-oriented audit summaries.
- The audit trail can remain readable and narrow without carrying full preference text solely for sync.
- Merge and forget can record exact transactional outcomes without changing the user-facing audit contract.
- Forget applies consistently to primary data, audit payloads, changelog payloads, and already-synced replicas.
- Primary writes gain another transactional responsibility in a future implementation.
- Two historical stores consume more space and require explicit compaction rules.
- The changelog is not absolutely immutable: forget remains a mandatory redaction exception.

## Alternatives considered

- **Extend `audit_log` into the operation log.** This avoids a second table, but it couples replay fidelity to an
  accountability contract that intentionally omits content. It would either weaken privacy or preserve gaps that
  make replay impossible.
- **Replicate snapshots only.** Snapshot replacement is simple for one-way backup, but it handles concurrent
  offline edits poorly and cannot propagate targeted forget without replacing the full dataset.
- **Diff primary tables during each session.** Table diffing lacks durable operation intent, performs poorly for
  lifecycle actions, and cannot distinguish correction, merge, deletion, and stale resurrection reliably.

## Open points before acceptance

- Exact changelog payload schemas and transaction grouping.
- Local payload encryption and compaction policy.
- Bootstrap treatment of pre-changelog databases.
- Retention and acknowledgement rules for forget tombstones.
