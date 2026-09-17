-- M29.1 editable staging: a receipt whose whole batch was withdrawn.
--
-- A withdrawal is review's second verb. When the last pending row of a source-tracked batch is
-- withdrawn and nothing was ever committed, the receipt has nothing left to review and recorded
-- nothing, which is neither `committed` nor `staged`. It becomes `withdrawn`: terminal, but
-- unlike `redacted` it keeps its batch, its label, and its staging rows, because a reviewer who
-- cannot see what they dropped cannot check that they dropped the right thing.
--
-- `import_staging.status` gains its own new value, `rejected`, with no schema change at all: that
-- column is plain TEXT and carries no CHECK. This table is the one that constrains its status, so
-- widening it is a table rebuild — SQLite cannot alter a CHECK in place.
--
-- The order below is the whole point of the file. `import_candidate_mappings` references this
-- table ON DELETE CASCADE, and the migration runner executes every file inside one transaction
-- with `PRAGMA foreign_keys=ON`, where `PRAGMA foreign_keys=OFF` has no effect. Dropping the
-- parent first would therefore fire an implicit cascading DELETE and silently erase every commit
-- mapping in the database. So the child is rebuilt against the new parent and dropped first,
-- leaving the old parent with no children to cascade into. Renaming the parent afterwards is what
-- re-points the rebuilt child's foreign key at the final name.
--
-- Forward-only and data-preserving: both tables are copied whole, and no row's values change.

CREATE TABLE import_source_sessions_new (
    id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    label TEXT,
    external_source_id TEXT,
    content_digest TEXT,
    extraction_fingerprint TEXT,
    extraction_contract_revision TEXT,
    claim_key TEXT UNIQUE,
    batch_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (status IN ('staged', 'partially_committed', 'committed', 'redacted', 'withdrawn')),
    -- A claim is only meaningful over real source bytes.
    CHECK (claim_key IS NULL OR content_digest IS NOT NULL),
    -- The minimal-claim invariant for a terminal redacted receipt, carried over unchanged. A
    -- withdrawn receipt is deliberately not held to it: erasure removed a redacted receipt's
    -- content, while a withdrawal removed nothing and must keep what it still has to show.
    CHECK (
        status <> 'redacted'
        OR (
            claim_key IS NOT NULL
            AND content_digest IS NOT NULL
            AND label IS NULL
            AND external_source_id IS NULL
            AND extraction_contract_revision IS NULL
            AND batch_id IS NULL
        )
    )
);

INSERT INTO import_source_sessions_new
SELECT id, source_kind, label, external_source_id, content_digest, extraction_fingerprint,
       extraction_contract_revision, claim_key, batch_id, status, created_at
FROM import_source_sessions;

CREATE TABLE import_candidate_mappings_new (
    candidate_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL REFERENCES import_source_sessions_new(id) ON DELETE CASCADE,
    disposition TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    created_at TEXT NOT NULL,
    CHECK (
        (disposition = 'entity' AND entity_id IS NOT NULL)
        OR (disposition = 'merged_away' AND entity_id IS NULL AND entity_type = 'relationship')
    )
);

INSERT INTO import_candidate_mappings_new
SELECT candidate_id, batch_id, source_session_id, disposition, entity_type, entity_id, created_at
FROM import_candidate_mappings;

-- The child goes first, so the parent has nothing left to cascade into.
DROP TABLE import_candidate_mappings;
DROP TABLE import_source_sessions;

-- The parent is renamed first, which rewrites the rebuilt child's foreign key to the final name.
ALTER TABLE import_source_sessions_new RENAME TO import_source_sessions;
ALTER TABLE import_candidate_mappings_new RENAME TO import_candidate_mappings;

-- M18.2 lists sources newest-first by (created_at DESC, id DESC) with keyset pagination.
CREATE INDEX idx_import_source_sessions_recent
    ON import_source_sessions(created_at DESC, id DESC);

-- `source show` pages mappings by `candidate_id ASC` within one source session.
CREATE INDEX idx_import_candidate_mappings_source
    ON import_candidate_mappings(source_session_id, candidate_id);

-- Hard forget deletes every mapping targeting an entity it actually erased.
CREATE INDEX idx_import_candidate_mappings_entity
    ON import_candidate_mappings(entity_type, entity_id);
