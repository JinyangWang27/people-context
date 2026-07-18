# Data model

The primary store is one user-owned SQLite database. Migration `001_initial.sql` creates the core people data,
`002_sync_foundations.sql` adds local replay capture, and `003_relationship_vocabulary.sql` adds the M7
relationship vocabulary. Domain and application code reach these tables only through ports and SQLite adapters.

## Conventions

- Primary entity and record ids are sortable ULID strings.
- Human names also have normalized columns populated by `normalize_name()` for identity matching.
- Timestamps are timezone-aware UTC datetimes; validity bounds are calendar dates.
- Assertive records carry confidence, provenance (`source`, optional `session`, optional `stated_by`), and where
  applicable sensitivity (`public`, `personal`, `sensitive`, `restricted`).
- Facts, affiliations, and relationships use a bitemporal-lite shape: world validity plus recording/creation time.

## Core tables

| Table | Purpose |
|---|---|
| `persons` | Canonical identity, `is_self`, summary, timestamps, and soft-delete tombstone. |
| `aliases` | Nickname, native-script name, transliteration, handle, former name, or other alias. |
| `organizations` | Named organization and optional kind. |
| `affiliations` | Person-to-organization role with validity, confidence, and provenance. |
| `relationships` | One stored typed edge between two people with validity, confidence, and provenance. |
| `facts` | Objective predicate/value statements with validity, recording time, sensitivity, and provenance. |
| `observations` | Explicitly subjective point-in-time notes. |
| `traits` | Derived structured characteristics such as communication style or values. |
| `interactions` / `interaction_participants` | Concise interaction summaries and participant joins; never raw transcripts. |
| `reminders` | Follow-up, occasion, and standing communication-note reminders. |
| `user_preferences` | JSON values such as communication philosophy and semantic model metadata. |
| `import_staging` | Reviewable distilled import candidates; raw source content is not stored. |
| `audit_log` | Privacy-oriented accountability record for every mutation. |

### Persons and soft deletion

`persons.deleted_at` hides a person from ordinary resolution, graph, context, and vault reads while preserving
the database rows. `forget` is different: it hard-deletes the selected record/person graph, redacts covered
audit/changelog payloads, and emits an ID-only durable tombstone.

### Relationships

`relationships` stores `id`, `subject_id`, `object_id`, normalized `type`, optional `label`, validity bounds,
confidence, provenance, and `created_at`. M7 stores exactly one canonical edge per active relationship:

- inverse input may swap endpoints and store the canonical type;
- symmetric input orders endpoint ids lexically;
- a repeated active canonical triple updates the existing row.

No inverse row is stored. Hydrated `RelationshipRecord` and public relationship context add `display_type`, a
read model field rather than a database column. It is the stored type from the subject perspective, the inverse
type from the object perspective, the stored type for symmetric relationships, and the stored fallback for
unknown vocabulary.

See [relationship-graph.md](relationship-graph.md) for the complete normalization contract.

## Relationship vocabulary (migration 003)

### `relationship_types`

| Column | Type | Meaning |
|---|---|---|
| `type` | TEXT PK | Normalized snake-case vocabulary value. |
| `inverse` | TEXT nullable | Opposite perspective type; null for symmetric types. |
| `symmetric` | INTEGER | Boolean flag. |
| `category` | TEXT | Seed categories are `professional`, `family`, and `social`; custom categories are allowed. |
| `canonical` | INTEGER | Exactly one member of each inverse pair is canonical. |

Seeded professional types: `reports_to`/`manages`, `mentor_of`/`mentee_of`, `colleague_of`.
Seeded family types: `parent_of`/`child_of`, `sibling_of`, `cousin_of`, `spouse_of`, `partner_of`.
Seeded social types: `friend_of`, `neighbor_of`, `acquaintance_of`.

### `relationship_type_synonyms`

| Column | Type | Meaning |
|---|---|---|
| `synonym` | TEXT PK | Normalized input spelling, for example `manager_of`. |
| `type` | TEXT FK | Vocabulary row that the synonym resolves to. |

Seed rows are reference data produced by migration, not user assertions, so they create no changelog entries.
Custom rows created through `relationship-types add` are portable user state and flow through audit/changelog.
A relationship type with no vocabulary row remains legal and reads as category `uncategorized`.

## Sync-foundation tables (migration 002)

`devices` holds one active installation id plus persisted hybrid logical clock state. `changelog` stores full
replay operations with device id, HLC components, transaction id, entity type/id, operation kind, replay
payload, changed fields, actor, schema version, and insertion time. Its deterministic order is
`(hlc_physical_ms, hlc_logical, device_id, op_id)`.

`sync_conflicts` is local staging for future conservative conflict review. M6/M7 add no exchange, pairing,
relay, peer cursor, replay engine, or bootstrap protocol. Forget redacts covered replay payloads and retains
stable-id tombstones.

**Do not copy the SQLite file between machines as a sync substitute.** The active `devices` row is the
installation's identity: two live copies of the same file (Dropbox/iCloud folder sync, restoring one backup
onto two machines) share one device id and interleave its persisted HLC state, corrupting the changelog's
per-origin ordering that future sync will rely on. Moving the file once to a new machine is fine; running two
copies concurrently is not. Device re-registration/copy detection is deliberately deferred to the sync
milestone. Use `people-context export` (or the vault export) to move data between machines today.

Every M7 write path—relationship create/update dedupe, custom vocabulary add, and applied legacy
normalization—uses the same transactional `audit_mutation` capture seam as M6.

## Search indexes

`person_search` is an FTS5 index over active canonical names and aliases. Repository writes maintain it;
`people-context reindex` repairs it after direct SQL changes.

With the optional semantic extra, `semantic_vectors` is a derived same-file `sqlite-vec` table containing
active-person documents and eligible public/personal interaction summaries. Sensitive/restricted interactions
are excluded. `people-context reindex --semantic` explicitly downloads the pinned model when needed and
atomically replaces vectors and model metadata.

## Facts, observations, and traits

| | Facts | Observations | Traits |
|---|---|---|---|
| Nature | Objective and verifiable in principle | Subjective impression | Derived structured pattern |
| Time shape | Validity plus recorded time | Observed time | Revisable updated time |
| Default use | Context subject to sensitivity/budget | Excluded from normal context | Communication-specific use |
| Vault export | Included subject to sensitivity | Excluded | Excluded |

Keeping these concepts separate prevents subjective material from being presented as fact and supports narrow,
purpose-specific disclosure.
