# Sync and replication design

## Status and scope

This document delivers M5 as a design milestone only. It defines a replication model that a later
implementation can adopt, but it does not commit the project to a transport, hosted service, schema migration,
or release date. ADRs [0004](../decisions/0004-changelog-vs-audit-log.md) and
[0005](../decisions/0005-conflict-resolution-strategy.md) remain proposed.

## 1. Goals and non-goals

### Goals

- Sync one user's dataset across multiple devices before attempting any form of inter-user sharing.
- Preserve the local-first operating model. Every device keeps a complete local SQLite database and remains
  usable while disconnected.
- Work over a dumb encrypted relay, removable media, or direct file exchange. A hosted relay may improve
  availability, but it is optional convenience rather than a required authority.
- Make replay deterministic, idempotent, and safe under duplicate delivery, reordered batches, retries, and
  clock skew.
- Preserve the existing privacy posture, especially sensitivity filtering, minimal disclosure, and hard forget.
- Leave room for future multi-user ownership and sharing without claiming that M5 solves authorization,
  collaborative identity resolution, or remote access.

### Non-goals

- Implementing sync, device registration, remote transport, or schema changes in M5.
- Making the current `audit_log` a replayable change feed without changing its payload contract.
- Real-time collaboration, server-authoritative state, or mandatory accounts.
- General-purpose CRDT semantics for every field.
- Syncing derived indexes, cached embeddings, pending import candidates, raw source files, or model caches.
- Solving backup retention on a device that never reconnects.

The first implementation target should therefore be **single-user, multi-device replication**. Multi-user
sharing is a later concern designed for in section 7, not an extension silently folded into the first sync
release.

## 2. Fitness of the current audit log as a replication source

The current audit log is useful evidence for this design, but it is not a sufficient replication source. It was
built for local accountability: an operator can inspect what kind of mutation occurred, when, and from which
provenance source. Replay has a stricter requirement: every durable state transition must be reconstructable
without consulting state that may not exist on the receiving device.

### 2.1 Payloads are intentionally lossy

Most record-creation paths store a full domain snapshot, and corrections store full `before` and `after`
snapshots. Other entries intentionally omit state:

- `set_communication_philosophy` records only the previous and new character lengths. A receiving device
  cannot reconstruct the preference text from those lengths.
- `remember_person` records a canonical name, a created flag, and an alias count, but not the complete person
  row or alias rows.
- `add_alias` records an alias value and whether it was newly added, but not the complete alias row metadata.
- forget tombstones contain the scope and deletion counts, not the deleted row ids or content.

This is correct for a privacy-aware accountability trail. It is insufficient for replication because data that
was never recorded cannot be replayed.

### 2.2 Redaction rewrites history

The SQLite lifecycle adapter handles forget by finding earlier audit entries that name the forgotten entity, or
contain a forgotten person id as a nested scalar, and replacing their payload with exactly
`{"redacted": true}`. The table is therefore append-oriented during ordinary operation, but it is not immutable.

That exception is required by the privacy model. It creates a replication obligation: a device that already
received an unredacted operation must later delete the corresponding primary rows and redact its local history.
Appending a new tombstone without processing prior local copies would not satisfy forget.

A sync design must therefore treat redaction as a first-class replicated operation, not as a local cleanup detail.
The right to forget wins over retaining a complete historical feed.

### 2.3 Merge and forget aggregate multi-row transactions

`merge_people` and `forget` each produce one audit entry for a transaction that changes or deletes many rows.
The aggregate entry protects the accountability trail from describing a partially committed lifecycle action,
but the payload is not a complete row-level delta:

- merge records the duplicate id, adopted alias values, moved-row counts, and removed self-loop count;
- forget records deletion counts only.

A replica could replay these only if the command were deterministic over identical pre-state. Replicas cannot
be assumed to have identical pre-state after concurrent edits, partial history, or conflict resolution. A true
replication source must either record every resulting row mutation in one transaction group or carry a complete,
deterministic outcome manifest.

### 2.4 Identity, sequencing, and clock skew are missing

An `AuditEntry` has an entry ULID, wall-clock timestamp, operation, entity type/id, payload, and provenance
source. It has no stable device identity, authenticated actor identity, device-local sequence, causal metadata,
or explicit transaction id.

ULIDs are globally collision-resistant and useful record ids, but their lexical order begins with a wall-clock
millisecond timestamp. They do not establish causality. If device B is ten minutes fast, every B operation may
sort after A operations that actually happened up to ten minutes later. Conversely, if B's clock is ten minutes
slow and a peer advances a global lexical cursor, a newly created B entry can sort behind that cursor and be
omitted permanently by a naive `id > cursor` query.

More generally, a clock error of `Δ` can invert the apparent order of any independent events separated by less
than `|Δ|`. Clock rollback can also generate new operations whose timestamps precede operations already sent.
Ordering replication solely by `ts` or ULID therefore breaks deterministic last-writer selection and can break
incremental delivery.

### 2.5 Derived and staging state are not portable state

The current export intentionally excludes:

- `person_search`, because FTS is derived from people and aliases;
- `semantic_vectors`, because embeddings are derived, model-specific, and sensitivity-filtered;
- `import_staging`, because candidates are temporary review workflow state.

The sync design adopts the same boundary. Derived search state and semantic vectors are rebuilt locally and are
never synced. Pending import candidates are device-local workflow state and are not synced in the first
implementation. Model caches and original import files are also excluded.

Portable primary state is the domain-shaped export content: people and aliases, organizations, affiliations,
relationships, facts, observations, traits, interactions and participants, reminders, user preferences, and the
privacy-safe historical trail.

### 2.6 Recommendation

Add a dedicated `changelog` table written in the same SQLite transaction as the primary mutation and, where
applicable, the accountability audit entry. Do not reinterpret the current `audit_log` as the replication log.

The dedicated changelog should:

- record full replay payloads even when the audit payload intentionally summarizes or omits content;
- identify the originating device and carry a replication ordering primitive;
- group row-level effects of one logical transaction;
- support idempotent replay and conflict metadata;
- permit mandatory payload redaction or deletion after forget while retaining a minimal durable tombstone.

The audit log should remain optimized for user-facing accountability. It may continue to redact earlier payloads
in place. The changelog is optimized for replay fidelity, but it is also subject to the same forget rule: ordinary
entries are append-only; forget is an explicit, replicated exception that removes or redacts earlier payloads.

Extending `audit_log` would avoid a second table, but it would couple two incompatible payload policies. Full
preference text and complete lifecycle manifests are necessary for replay but are deliberately absent from the
audit trail. A dedicated changelog keeps the privacy-facing audit contract narrow while making replication
requirements explicit. See proposed ADR [0004](../decisions/0004-changelog-vs-audit-log.md).

## 3. Operation model

### 3.1 Changelog entry

A future changelog entry should contain at least:

| Field | Purpose |
|---|---|
| `op_id` | Globally unique deduplication id; not the ordering source. |
| `device_id` | Stable id for the originating installation. |
| `hlc` | Hybrid logical clock advanced when remote operations are observed. |
| `transaction_id` | Groups every row-level effect of one transaction. |
| `entity_type` | Portable entity class or lifecycle tombstone type. |
| `entity_id` | Stable entity id; replay never substitutes a new id. |
| `op_kind` | `create`, `update`, `correct`, `merge`, `forget`, or `prefs`. |
| `payload` | Full replay payload or deterministic lifecycle manifest. |
| `changed_fields` | Sorted fields for update/correct conflict detection. |
| `actor` | Local provenance actor/source. |
| `schema_version` | Payload schema version, independent of DB migration. |

A changelog row should also record local insertion time for diagnostics. That time is not used for conflict
ordering.

### 3.2 Ordering primitive: hybrid logical clocks

Use a hybrid logical clock (HLC) as the operation ordering primitive. An HLC combines:

1. a physical millisecond component;
2. a logical counter that increments when local time does not advance or when a received clock is ahead; and
3. `device_id` and `op_id` as deterministic tie-breakers.

The comparison key is `(physical_ms, logical_counter, device_id, op_id)`. This gives a stable total order for
conflict resolution while preserving causality when a device incorporates received HLC values before producing
new local operations. It does not claim to detect every concurrent relationship the way a vector clock would.
That trade-off is acceptable for the first single-user design because conflicts are resolved conservatively and
ambiguous identity changes are surfaced for review.

#### Worked clock-skew example

- Device A's correct clock reads `12:00:00.000` and emits HLC `(12:00:00.000, 0)`.
- Device B's wall clock is ten minutes fast and emits `(12:10:00.000, 0)` for an independent edit.
- B then receives A's operation. Its HLC remains at the greater physical component and increments logically.
- B's next causally dependent operation becomes `(12:10:00.000, 1)`, which orders after both prior operations.
- A receives B's operation and advances its local HLC to at least `(12:10:00.000, 2)` before creating another
  operation, even though A's wall clock still reads about `12:00`.

The incorrect physical time remains visible for diagnostics, but a clock rollback cannot create an operation
that moves behind the device's previously emitted HLC. A receiver must never use raw wall time or ULID lexical
order as its replication cursor.

### 3.3 Replay and idempotency rules

Every replica stores applied `op_id` values. Receiving an already applied `op_id` is a no-op. A transaction group
is applied atomically; a partial group is rejected and retried.

| Operation | Replay rule |
|---|---|
| `create` | Insert the supplied full row and original id; equivalent existing content is a no-op. |
| `update` | Apply full after-image fields with per-field clocks and section 4 policies. |
| `correct` | Apply the after-image; concurrent corrections to one assertion require review. |
| `merge` | Apply the exact deterministic outcome manifest; completed manifests are no-ops. |
| `forget` | Hard-delete, redact covered history, retain the tombstone, and suppress covered operations. |
| `prefs` | Upsert the complete preference value from the encrypted changelog. |

The merge manifest contains the primary and duplicate ids, final primary identity snapshot, exact affected row
ids, re-parent targets, removed edges, and duplicate tombstone. The future writer should create row-level
changelog entries for ordinary rows. A merge or forget may also have a semantic parent entry, but its transaction
group must contain enough exact outcomes to replay without querying the origin's previous state.

Communication philosophy sync carries its full text in the encrypted changelog even though the accountability
audit entry remains length-only.

## 4. Conflict resolution

The default rule is deterministic field-level last-writer-wins using the HLC comparison key, but only for fields
where an incorrect automatic choice is reversible and does not compromise identity or privacy. Structural merge
and user review override that default.

| Entity or field | Automatic policy | Review conditions |
|---|---|---|
| Person non-identity scalars | Field-level LWW. | Edit racing with merge or forget. |
| Person `canonical_name` | Keep both candidates. | Any divergent concurrent values. |
| Person `is_self` | No automatic resolution. | Any conflict or two self rows per owner. |
| Alias set | Union by normalized value; metadata uses LWW. | One alias resolves to different people. |
| Organization | Kind uses LWW. | Divergent normalized names. |
| Affiliation/relationship | Distinct fields merge; same fields use LWW. | Endpoint changes or semantic corrections. |
| Facts/observations/traits | Distinct fields merge; same fields use LWW. | Competing corrections or person changes. |
| Interactions | Scalar fields use LWW. | Concurrent participant-set changes. |
| Reminders | Fields use LWW; completion is monotonic. | Invalid merged schedule/kind. |
| User preferences | Full-value LWW; disclosure defaults are conservative. | Incompatible schema versions. |
| Soft delete | Wins over older edits; explicit restore may reverse it. | Merge or identity-reassignment race. |
| Merge | Quarantine affected identities. | Merge/edit, merge/merge, or primary conflict. |
| Forget | Always wins. | No content review. |

Sensitivity always resolves to the more restrictive value. Concurrent edits to distinct fields may merge. A
`correct` versus `correct` race on the same assertion requires review even when the changed fields differ,
because both operations claim to replace previously wrong knowledge.

### Identity invariant

Automatic conflict resolution must never reduce identity-resolution correctness. In particular, it must not:

- silently choose between divergent canonical names;
- create two self rows for one owner;
- attach one normalized alias to two people;
- redirect records across people because of an HLC tie-breaker;
- apply a merge manifest when either identity has an unresolved merge/edit race.

When an automatic result would violate those rules, the replica records a conflict object and excludes the
ambiguous change from normal resolution until the user reviews it. This conservative boundary is more important
than convergence without intervention. See proposed ADR
[0005](../decisions/0005-conflict-resolution-strategy.md).

## 5. Forget and redaction across replicas

### 5.1 Forget-propagation operation

A replicated forget operation is a durable tombstone with the highest semantic precedence. Its payload contains
only the information required to find and delete copies:

- scope: `person` or `record`;
- target type and stable target id;
- exact affected entity ids known to the origin;
- for person scope, the rule to delete any local row that references the person id;
- operation ids or transaction ids whose payloads must be redacted when known;
- HLC, origin device, and tombstone schema version.

It must not contain names, summaries, assertion values, or deleted preference text. Applying it performs one
local transaction:

1. hard-delete matching primary rows and derived index rows;
2. redact earlier audit payloads using the existing exact-id semantics;
3. delete or replace covered changelog payloads with a minimal redaction marker;
4. retain the forget tombstone so later-arriving stale operations remain suppressed;
5. rebuild affected FTS and semantic state locally as needed.

A non-forget operation covered by a retained tombstone is ignored even if its HLC is later. Forget wins over
sync completeness and over ordinary last-writer rules.

### 5.2 Late replicas

A device may have been offline since before the forget. On its next session it can upload stale creates or edits
before downloading the tombstone. The session must therefore exchange tombstones before ordinary operations, or
apply the complete batch atomically with forget precedence. Once the device receives the tombstone, it deletes
its local copy and must not re-advertise suppressed operations.

Forget tombstones should not be compacted merely because currently online devices acknowledged them. They must
remain available to every registered device until that device is explicitly retired, or until the user accepts
that it can no longer safely rejoin.

### 5.3 Relay and offline-device limits

A dumb relay may retain encrypted batches according to its own retention policy. End-to-end encryption prevents
the relay from reading payloads, but it does not guarantee physical deletion of ciphertext from backups. A
future implementation should support batch deletion and bounded retention where the relay allows it, and should
consider per-device or per-epoch keys so retired ciphertext becomes undecryptable.

A device that never reconnects keeps its local copy. Software on other replicas cannot force deletion from a
permanently offline, lost, seized, or adversarial device. Device retirement prevents that device from receiving
future data; it does not erase data already stored there. This residual risk must be stated in user-facing sync
documentation.

## 6. Sync session protocol sketch

### 6.1 Device registration

Each installation creates:

- a random stable `device_id`;
- an HLC state;
- an encryption key pair or pre-shared file-exchange key;
- a local registry of known devices, retirement state, and acknowledged cursors.

For single-user direct file exchange, registration may be a QR code, pairing file, or explicit key import. A
hosted relay may store opaque device mailboxes and public-key metadata, but it is not the source of truth for the
dataset.

### 6.2 Cursor and high-water marks

Each receiver tracks a high-water mark per origin device, not one global ULID cursor. The cursor is the greatest
fully applied HLC/op-id key from that origin plus a compact set of gaps if out-of-order batches are allowed.
Senders retain operations until all non-retired peers have acknowledged them or a user-approved compaction
snapshot supersedes them.

Per-device cursors prevent a slow or newly paired device from hiding operations created on another device with
an older wall clock. They also make duplicate and retry handling explicit.

### 6.3 Batch envelope

Reuse the export envelope conventions: a named format, independent version, creation time, and domain-shaped
JSON content. A sync batch could use:

```json
{
  "format": "people-context-sync-batch",
  "version": 1,
  "created_at": "2026-07-17T12:00:00Z",
  "sender_device_id": "...",
  "base_cursors": {"device-a": "hlc/op-id"},
  "tombstones": [],
  "transactions": [],
  "acknowledgements": {"device-b": "hlc/op-id"}
}
```

The serialized envelope is encrypted and authenticated end to end before it is placed on a relay or removable
medium. Relay-side TLS is useful but insufficient; the relay must not receive plaintext personal data or
replication keys. Batch signatures or authenticated encryption must detect tampering, truncation, and sender
substitution.

### 6.4 Session order

1. Authenticate or verify the peer/device key.
2. Exchange device registry changes and retirements.
3. Exchange cursors and missing-range requests.
4. Deliver forget tombstones first.
5. Deliver complete transaction groups in bounded batches.
6. Apply groups atomically, rebuild derived state locally, and persist acknowledgements.
7. Compact only when the required peers have acknowledged a snapshot boundary.

The same protocol works through an optional hosted mailbox, a shared encrypted directory, or manually copied
batch files.

### 6.5 First-sync bootstrap

The current `export_data` envelope is a useful snapshot shape, but the current import features are candidate
staging pipelines, not a trusted full-dataset restore path. A future sync implementation must not feed a full
export through `import_content`, `stage_candidates`, or review staging.

Bootstrap should instead create a consistent snapshot at changelog watermark `H`:

1. export the portable primary dataset using the existing domain-shaped collections;
2. include the changelog/device metadata needed to identify watermark `H`;
3. load it through a future trusted, transactional snapshot-restore path;
4. replay all operations after `H`;
5. rebuild FTS and semantic vectors locally;
6. leave `import_staging` empty on the new device.

This avoids replaying the entire historical log while retaining a precise hand-off from snapshot state to
incremental operations.

## 7. Multi-user considerations

Multi-user sharing means two human principals, not merely two devices, can read or change some of the same data.
That introduces ownership and disclosure boundaries that do not exist in single-user sync.

### 7.1 Required concepts

- **Owner identity.** Rows need an owning user or dataset id. A device id is not an owner id.
- **Actor identity.** Changelog and audit entries need the authenticated human/service actor in addition to the
  originating device and free-form provenance source.
- **Per-owner self.** `is_self` must be unique within an owner or workspace, not globally across the database.
- **Sharing policy.** Shared entities need grants or memberships defining who may read, propose, edit, merge,
  export, and forget them.
- **Disclosure boundary.** Sensitivity becomes an inter-user authorization boundary, not only a context-return
  filter. `restricted` records never sync to another user by default. `sensitive` sharing requires explicit,
  narrow consent and should not be implied by sharing the containing person.
- **Authorship and review.** Conflicts should show which user asserted each candidate. One user must not silently
  rewrite another user's provenance.

### 7.2 Existing choices that help

- ULID entity ids reduce collision risk when independent devices or users create rows offline.
- Representing self as a person row keeps relationships uniform; the flag only needs owner scoping.
- Provenance `source`, `session`, and `stated_by` columns provide useful attribution vocabulary.
- Separate sensitivity fields on assertive records provide a place to enforce disclosure rules.
- Hexagonal ports keep a future authenticated transport or policy adapter outside domain persistence details.

These choices reduce migration cost, but they do not solve multi-user authorization. ULIDs do not identify an
owner or actor, the current `is_self` invariant is global, provenance strings are not authenticated principals,
and existing rows have no ownership or sharing columns.

## 8. Migration path appendix

This is a sketch for a future migration `002`; **it is not implemented by M5**.

Minimal backward-compatible additions could include:

```sql
CREATE TABLE devices (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    public_key TEXT,
    created_at TEXT NOT NULL,
    retired_at TEXT
);

CREATE TABLE changelog (
    op_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id),
    hlc_physical_ms INTEGER NOT NULL,
    hlc_logical INTEGER NOT NULL,
    transaction_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    op_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    changed_fields_json TEXT NOT NULL,
    actor_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    inserted_at TEXT NOT NULL
);

CREATE INDEX changelog_origin_order
    ON changelog(device_id, hlc_physical_ms, hlc_logical, op_id);

CREATE TABLE sync_peer_cursors (
    peer_device_id TEXT NOT NULL REFERENCES devices(id),
    origin_device_id TEXT NOT NULL REFERENCES devices(id),
    hlc_physical_ms INTEGER NOT NULL,
    hlc_logical INTEGER NOT NULL,
    op_id TEXT NOT NULL,
    PRIMARY KEY (peer_device_id, origin_device_id)
);

CREATE TABLE sync_conflicts (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    conflict_kind TEXT NOT NULL,
    candidate_ops_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
```

The migration would also need transactional write integration so each primary mutation, audit entry, and
changelog transaction commit or roll back together. Existing rows require a bootstrap snapshot rather than
invented historical operations. Multi-user support would later add owner/user/actor tables, ownership columns,
and sharing grants; those should not be added to a single-user sync migration without a concrete authorization
design.

## 9. Open questions

1. **Should a future implementation store full changelog payloads as plaintext inside the local SQLite file?**
   Leaning: yes initially, because the primary database already contains the same plaintext data; use end-to-end
   encryption for exported batches and evaluate SQLCipher separately.
2. **Should merge record row-level child operations, one deterministic manifest, or both?**
   Leaning: both. Child operations simplify generic replay; the semantic parent preserves user intent and review.
3. **How long should forget tombstones be retained?**
   Leaning: indefinitely for every non-retired device, with explicit user-controlled device retirement before
   compaction may remove them.
4. **Should pending `import_staging` candidates sync?**
   Leaning: no for the first implementation. They are device-local review workflow, not accepted knowledge.
5. **Should reminders sync before a notification daemon exists?**
   Leaning: yes. Reminder rows are portable primary state; notification delivery state is a separate future
   concern.
6. **How should new devices be paired without a hosted account?**
   Leaning: encrypted pairing files or QR-assisted public-key exchange first; a hosted account may wrap the same
   key exchange later.
7. **Is HLC sufficient once genuine multi-user collaboration is introduced?**
   Leaning: sufficient for deterministic ordering, but multi-user review and authorization may require explicit
   causal parent references for collaborative workflows.
8. **Should bootstrap restore include the accountability audit log?**
   Leaning: yes, after its current redactions, because it is portable user-owned history; the changelog may be
   compacted separately once a snapshot boundary is acknowledged.
9. **How are retired or lost devices prevented from decrypting future batches?**
   Leaning: rotate a dataset epoch key on device retirement and distribute it only to active devices.
10. **Should sensitivity ever be lowered automatically by conflict resolution?**
    Leaning: no. Automatic resolution selects the more restrictive value; lowering sensitivity requires an
    explicit user action.
