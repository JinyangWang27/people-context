# Data Model

This document describes every table in the SQLite schema (`adapters/sqlite/migrations/001_initial.sql`),
the metadata columns common to assertive records, ID and normalization conventions, the FTS5 search tables,
the bitemporal-lite time model, and the facts/observations/traits distinction. See
[docs/architecture.md](architecture.md) for how this schema is reached only through the `adapters/sqlite`
layer, and [docs/privacy-and-safety.md](privacy-and-safety.md) for how sensitivity and forget/soft-delete are
used in practice.

## Conventions used throughout

- **IDs are ULIDs** — 26-character Crockford-base32 strings, sortable by creation time, generated with
  `python-ulid` (`str(ULID())`). Every table's primary key is a ULID string, not an auto-increment integer.
- **Normalized-name columns.** Any column holding a human name that is matched against also has a paired
  `_normalized` column (e.g. `persons.canonical_name_normalized`, `aliases.value_normalized`), populated by
  `normalize_name()`: Unicode NFKC, casefold, strip combining marks (NFD, drop Unicode category `Mn`),
  collapse internal whitespace, strip. This is what stage 1 and stage 2 of identity resolution match against
  — see [docs/identity-resolution.md](identity-resolution.md).
- **Timestamps** are timezone-aware UTC `datetime`s; **dates** (e.g. validity bounds) are plain
  `datetime.date`, since validity is a calendar concept, not an instant.
- **Common metadata columns** — every *assertive* record (affiliations, relationships, facts, observations,
  traits, interactions) carries:

  | Column(s) | Meaning |
  |---|---|
  | `confidence` | Float, `0.0`–`1.0`. How confident the source is in the truth of the assertion. Defaults to `1.0` for direct user statements, lower for inferred/imported data. |
  | `sensitivity` | Enum: `public`, `personal`, `sensitive`, `restricted`. Governs default inclusion in context responses — see [docs/privacy-and-safety.md](privacy-and-safety.md). |
  | provenance: `source`, `session`, `stated_by` | `source` — who/what produced this record (`"user"`, `"agent:claude-code"`, `"import/email"`, …). `session` — optional session identifier. `stated_by` — who asserted it (a person id, or `"self"`). |
  | timestamps | `created_at`/`updated_at` and, where relevant, `recorded_at`/`observed_at`/`occurred_at` — see Bitemporal-lite below. |

## Tables

### `persons`

The core entity. Every person the system knows about, including the user themselves (see
[docs/architecture.md](architecture.md#self-as-a-person-row)).

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (ULID) | Primary key. |
| `canonical_name` | TEXT | The name used to refer to this person by default. |
| `canonical_name_normalized` | TEXT | `normalize_name(canonical_name)`, indexed for exact/normalized matching. |
| `is_self` | BOOLEAN | True for exactly one row: the user. |
| `summary` | TEXT, nullable | Short free-text summary of who this person is. |
| `created_at` | TIMESTAMP | Row creation time. |
| `updated_at` | TIMESTAMP | Last modification time. |
| `deleted_at` | TIMESTAMP, nullable | Soft-delete tombstone — see Soft-delete vs. forget below. |

### `aliases`

Alternative names for a person: nicknames, native-script names, transliterations, handles, former names.
Transliterations are **stored**, not computed — see [docs/identity-resolution.md](identity-resolution.md).

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (ULID) | Primary key. |
| `person_id` | TEXT (ULID) | FK → `persons.id`. |
| `value` | TEXT | The alias string as written. |
| `value_normalized` | TEXT | `normalize_name(value)`. |
| `kind` | TEXT enum | `nickname`, `native_script`, `transliteration`, `handle`, `former_name`, `other`. |
| `lang` | TEXT, nullable | BCP-47-ish language tag, e.g. `"zh"`, `"en"`. |
| `script` | TEXT, nullable | Script tag, e.g. `"Hans"`, `"Latn"`. |

### `organizations`

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (ULID) | Primary key. |
| `name` | TEXT | Organization name. |
| `kind` | TEXT, nullable | Free-text classification (company, team, school, …). |

### `affiliations`

A person's role at an organization over time.

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (ULID) | Primary key. |
| `person_id` | TEXT (ULID) | FK → `persons.id`. |
| `org_id` | TEXT (ULID) | FK → `organizations.id`. |
| `role` | TEXT | e.g. "engineering manager". |
| `valid_from`, `valid_to` | DATE, nullable | Validity period — see Bitemporal-lite. |
| `confidence`, `sensitivity`, provenance | — | Common metadata columns. |

### `relationships`

Directed, typed edges between two people. The user's own relationships hang off the `is_self` person as
`subject_id`.

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (ULID) | Primary key. |
| `subject_id` | TEXT (ULID) | FK → `persons.id`. The "from" side of the edge. |
| `object_id` | TEXT (ULID) | FK → `persons.id`. The "to" side of the edge. |
| `type` | TEXT | Relationship type, e.g. `"manager_of"`, `"sibling_of"`, `"friend_of"`. |
| `label` | TEXT, nullable | Free-text elaboration. |
| `valid_from`, `valid_to` | DATE, nullable | Validity period. |
| `confidence`, `sensitivity`, provenance | — | Common metadata columns. |

### `facts`

Objective, time-aware, third-person-verifiable statements about a person — kept structurally separate from
subjective content. See Facts vs. observations vs. traits below.

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (ULID) | Primary key. |
| `person_id` | TEXT (ULID) | FK → `persons.id`. |
| `predicate` | TEXT | What kind of fact, e.g. `"job_title"`, `"employer"`, `"location"`. |
| `value` | TEXT | The fact's value. |
| `valid_from`, `valid_to` | DATE, nullable | When the fact was/is true in the world. |
| `recorded_at` | TIMESTAMP | When the system was told this fact. |
| `confidence`, `sensitivity`, provenance | — | Common metadata columns. |

### `observations`

Explicitly subjective notes — impressions, not verifiable facts.

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (ULID) | Primary key. |
| `person_id` | TEXT (ULID) | FK → `persons.id`. |
| `text` | TEXT | The observation itself, in prose. |
| `observed_at` | TIMESTAMP | When this observation was made. |
| `sensitivity`, provenance | — | Common metadata columns (observations default to `sensitivity = personal`; no separate `confidence` beyond the observer's own certainty expressed in `text`, though the schema does not preclude one). |

### `traits`

Derived, structured characteristics — communication style, temperament, values, preferences, topics to
avoid — distilled from observations and interactions over time. See
[docs/communication-guidance.md](communication-guidance.md) for how these are used.

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (ULID) | Primary key. |
| `person_id` | TEXT (ULID) | FK → `persons.id`. |
| `category` | TEXT enum | `communication_style`, `temperament`, `values`, `preference`, `topics_to_avoid`, `other`. |
| `value` | TEXT | The trait content, e.g. `"prefers async written updates over calls"`. |
| `evidence_note` | TEXT, nullable | Reference to the observation(s)/interaction(s) this was distilled from. |
| `confidence`, `sensitivity`, provenance | — | Common metadata columns. |
| `updated_at` | TIMESTAMP | Last revision time. |

### `interactions` / `interaction_participants`

Concise summaries of interactions — never transcripts or raw content.

| Table | Column | Type | Meaning |
|---|---|---|---|
| `interactions` | `id` | TEXT (ULID) | Primary key. |
| `interactions` | `summary` | TEXT | Short prose summary of what happened. |
| `interactions` | `occurred_at` | TIMESTAMP | When the interaction happened. |
| `interactions` | `channel` | TEXT, nullable | e.g. `"email"`, `"call"`, `"in_person"`. |
| `interactions` | `sensitivity`, provenance | — | Common metadata columns. |
| `interaction_participants` | `interaction_id` | TEXT (ULID) | FK → `interactions.id`. |
| `interaction_participants` | `person_id` | TEXT (ULID) | FK → `persons.id`. One row per participant. |

### `reminders`

Person-linked reminders. See [docs/communication-guidance.md](communication-guidance.md) for the pull-based
retrieval model (no daemon in v1).

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (ULID) | Primary key. |
| `person_id` | TEXT (ULID) | FK → `persons.id`. |
| `text` | TEXT | The reminder content. |
| `kind` | TEXT enum | `follow_up`, `occasion` (birthdays, etc.), `communication_note` (surfaced whenever this person comes up, no due date). |
| `due_at` | TIMESTAMP, nullable | When the reminder is due (not used for `communication_note`). |
| `recurrence` | TEXT, nullable | Free-text recurrence rule, e.g. `"yearly"`. |
| `status` | TEXT enum | `active`, `completed`, `cancelled`. |
| `created_at` | TIMESTAMP | Row creation time. |

### `user_preferences`

Key/value store for user-level settings.

| Column | Type | Meaning |
|---|---|---|
| `key` | TEXT | Primary key, e.g. `"communication_philosophy"`. |
| `value_json` | TEXT (JSON) | The value, JSON-encoded. |

The `communication_philosophy` key holds free-text guidance the user writes themselves — for example,
principles drawn from 周易 (I Ching) or 道德经 (Tao Te Ching), a personal style guide, or company
communication norms. It also holds default disclosure settings. See
[docs/communication-guidance.md](communication-guidance.md).

M4 also stores `semantic_embedding_model_id` and `semantic_embedding_dimension`. These portable preferences
identify the derived vectors without embedding backend-specific types in the domain. A semantic rebuild
replaces both keys atomically with the vec0 rows; search refuses mismatched metadata.

### `import_staging`

Extracted candidate records awaiting user approval. Raw source content is never stored here — only
distilled candidates plus a provenance reference. See [docs/import.md](import.md).

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (ULID) | Primary key. |
| `batch_id` | TEXT (ULID) | Groups candidates from one import run. |
| `source` | TEXT | e.g. `"import/email"`, `"import/vcard"`, or `"import/agent:meeting-notes"`. |
| `candidate_json` | TEXT (JSON) | The distilled candidate record (proposed person/alias/fact/interaction) plus a provenance reference (message id, date) — never the raw message. |
| `status` | TEXT enum | Pending / accepted / rejected. |
| `created_at` | TIMESTAMP | Row creation time. |

### `audit_log`

Append-only record of every mutation. See
[docs/architecture.md](architecture.md#append-only-audit-log-as-future-sync-foundation) and
[docs/privacy-and-safety.md](privacy-and-safety.md).

| Column | Type | Meaning |
|---|---|---|
| `id` | TEXT (ULID) | Primary key. |
| `ts` | TIMESTAMP | When the mutation happened. |
| `op` | TEXT | `"create"`, `"update"`, `"merge"`, `"forget"`, … |
| `entity_type` | TEXT | e.g. `"person"`, `"fact"`. |
| `entity_id` | TEXT (ULID) | The affected row's id. |
| `payload_json` | TEXT (JSON) | Op-specific detail. |
| `source` | TEXT | Provenance source string of the mutation. |

Audit payloads normally follow one convention across use cases: one mutated row produces one audit entry. Create
payloads describe the resulting row; ordinary updates describe changed values; corrections and status
transitions carry `before`, `after`, and a sorted `fields` list. Payloads are JSON-compatible and may use a
privacy-preserving summary where full content is unnecessary. In particular, communication-philosophy
audits store only before/after character lengths, never the philosophy text. Organization auto-creation and
affiliation creation are two row mutations and therefore produce one audit entry each. Atomic `merge` and
`forget` are intentional exceptions: each multi-row lifecycle transaction produces exactly one aggregate
entry so the audit cannot describe a partial operation that never committed. A merge audit records the
duplicate id, every adopted alias value in `aliases_added`, moved-row counts, and removed self-loop count;
the duplicate canonical name is adopted as a `former_name` alias. The replay changelog is intentionally more
complete: merge emits exact row-level child operations plus one semantic parent manifest under a shared
`transaction_id`. Forget audit tombstones intentionally use the scope (`person` or `record`) as `entity_type`;
their `entity_id` is the person id for person scope or the validated `concrete_type:id` target for record scope.
The corresponding changelog tombstone uses the concrete target type/id and carries affected entity ids plus
covered operation/transaction ids only; it never carries deleted names or values.

### `devices`, `changelog`, and `sync_conflicts` (M6)

Migration `002_sync_foundations.sql` adds local sync state without backfilling invented history. On first open,
one active installation row is created in `devices` with a random ULID, hostname display name, and persisted
`hlc_physical_ms`/`hlc_logical` state. HLC emission never regresses across process restarts or wall-clock rollback.

`changelog` stores full replay operations: `op_id`, `device_id`, HLC components, `transaction_id`, entity type/id,
`op_kind`, full `payload_json`, sorted `changed_fields_json`, `actor_json`, independent `schema_version`, and local
`inserted_at`. The deterministic order key is `(hlc_physical_ms, hlc_logical, device_id, op_id)`. Ordinary entries
are append-only; forget redacts covered transaction payloads and retains an ID-only tombstone.

`sync_conflicts` is local staging for future conservative conflict review. M6 does not create peer cursors because
no peer, exchange, or acknowledgement protocol exists. These tables are not included in the version-1 export
envelope; M7 must define trusted snapshot restore and changelog transfer.

## FTS5 tables

One SQLite FTS5 virtual table provides ranked, tokenized search, maintained by the repository on every
write (not by database triggers, so all maintenance logic lives in `adapters/sqlite/repository.py`):

- **`person_search`** — indexes `canonical_name` plus every alias `value` for a person, associated back to
  `person_id` (stored `UNINDEXED` in the FTS row so it can be selected without being tokenized). This is
  what backs stage 3 (FTS prefix/token match) of identity resolution and the `search_people`/`show` /
  `search` CLI paths — see [docs/identity-resolution.md](identity-resolution.md).
Whenever a person or their aliases change, the repository deletes and re-inserts that person's `person_search`
rows so the index never drifts from the source tables. If a user edits the database directly with an
external SQL tool, the index can go stale — `people-context reindex` atomically rebuilds it from active
people and aliases.

## Optional semantic vec0 table

With the `semantic` extra installed, M4 dynamically creates a same-file `semantic_vectors` virtual table:

```sql
CREATE VIRTUAL TABLE semantic_vectors USING vec0(
  entity_id TEXT PRIMARY KEY,
  kind TEXT PARTITION KEY,
  embedding FLOAT[256] DISTANCE_METRIC=cosine
);
```

It contains active-person documents (canonical name, aliases ordered by id, summary) and only `public` or
`personal` interaction summaries. `sensitive`/`restricted` interactions are removed. This table is derived,
excluded from portable export, and maintained best-effort after saves/merges/deletes; primary data commits
even if vector refresh fails. `people-context reindex --semantic` atomically repairs all vectors and their
model metadata. Exposed similarity is `1.0 - distance`.

## Bitemporal-lite

Facts, affiliations, and relationships each carry **two independent time dimensions**, deliberately kept
lightweight rather than fully bitemporal:

1. **Validity period** (`valid_from`/`valid_to`, `date`) — when the fact was/is true *in the world*.
2. **Recording time** (`recorded_at` on facts; `created_at`/`updated_at` elsewhere) — when the system was
   *told* about it.

This is enough to answer questions like "who was her manager in 2024?" — query `affiliations` (or
`relationships`) where `valid_from <= 2024-XX-XX <= valid_to` (nullable bound = open-ended) — without the
complexity of a full bitemporal model that also versions corrections to `recorded_at` itself. If a
previously recorded assertion turns out to have been wrong, `correct_record` fixes its whitelisted fields in
place and writes a lossless audit payload containing the full before/after snapshots and changed field names
(see [docs/mcp-interface.md](mcp-interface.md)). A real-world change over time is represented by a new record
and validity period instead, not as a correction.

## Facts vs. observations vs. traits

These three record types look superficially similar (person-linked, provenanced, sensitivity-tagged text)
but serve distinct purposes and are never merged in the API or in response formatting:

| | `facts` | `observations` | `traits` |
|---|---|---|---|
| Nature | Objective, in principle verifiable | Subjective impression | Derived, structured characteristic |
| Example | `"employer" = "Acme Corp"` | `"seemed stressed about the launch"` | `communication_style: "prefers written summaries over live discussion"` |
| Time shape | Bitemporal-lite (`valid_from`/`valid_to` + `recorded_at`) | Point-in-time (`observed_at`) | Point-in-time, revisable (`updated_at`) |
| Evidence link | N/A | N/A | `evidence_note` references the observations/interactions it was distilled from |
| Default disclosure | Included in context per sensitivity | Excluded unless purpose calls for it | Excluded from default context; included for communication-guidance purposes |

Facts answer "what is true." Observations answer "what did this feel like." Traits answer "what pattern have
we distilled, and how should that shape communication." See
[docs/communication-guidance.md](communication-guidance.md) for how traits specifically are used, and
[docs/privacy-and-safety.md](privacy-and-safety.md) for the disclosure rules that separate all three.

## Soft-delete vs. forget

- **Soft-delete** (`persons.deleted_at`) marks a person as no longer active without removing any row. Soft-
  deleted people are excluded from normal reads (`list_people(include_deleted=False)` by default,
  `resolve_person` excludes them) but remain in the database and in exports until forgotten.
- **Forget** (the MCP tool and CLI delete command) is a **hard delete**: rows are actually removed, prior
  audits naming the target are replaced with exactly `{"redacted": true}`. Covered changelog transactions are
  also redacted, and a durable changelog tombstone records stable ids and coverage only. The user-facing audit
  tombstone remains minimal and records scope plus pluralized deletion counts. This is the mechanism for genuinely removing data
  the user no longer wants stored, as opposed to merely hiding it. See
  [docs/privacy-and-safety.md](privacy-and-safety.md) for the full forget semantics.
