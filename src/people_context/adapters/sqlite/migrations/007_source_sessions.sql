-- M18.1 provenance and idempotency: durable source receipts and candidate commit outcomes.
--
-- A source session records *that* material was processed, never the material itself. It carries
-- a bounded machine category, an optional caller label, the SHA-256 digest of the exact stable
-- bytes that produced the batch, and an extraction fingerprint derived from the extraction-
-- affecting configuration. No source body, transcript excerpt, absolute path, or raw self
-- identity is stored here.
--
-- `claim_key` is the canonical duplicate claim. It is a single pre-composed text value rather
-- than a three-column UNIQUE because the fingerprint's absence must be an explicit state: SQLite
-- treats NULLs in a UNIQUE index as distinct, so a nullable fingerprint column would let two
-- "digest present, fingerprint not supplied" sessions both claim the same source. The composed
-- key substitutes a fixed sentinel that cannot collide with any 64-hex fingerprint instead.
--
-- A NULL `claim_key` means "this row asserts no canonical claim": an explicit `--force`
-- reprocessing session, or an agent session that supplied no digest and therefore makes no
-- source-level idempotency promise. NULLs being distinct is exactly the wanted behaviour there.
--
-- Additive and forward-only. Pre-M18 batches have no source session and are not backfilled.

CREATE TABLE import_source_sessions (
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
    CHECK (status IN ('staged', 'partially_committed', 'committed', 'redacted')),
    -- A claim is only meaningful over real source bytes.
    CHECK (claim_key IS NULL OR content_digest IS NOT NULL),
    -- The minimal-claim invariant for a terminal redacted receipt: claim-backed, and stripped of
    -- every caller-authored and optional inspection field.
    CHECK (
        status <> 'redacted'
        OR (
            content_digest IS NOT NULL
            AND label IS NULL
            AND external_source_id IS NULL
            AND extraction_contract_revision IS NULL
            AND batch_id IS NULL
        )
    )
);

-- M18.2 lists sources newest-first by (created_at DESC, id DESC) with keyset pagination.
CREATE INDEX idx_import_source_sessions_recent
    ON import_source_sessions(created_at DESC, id DESC);

CREATE TABLE import_candidate_mappings (
    candidate_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL REFERENCES import_source_sessions(id) ON DELETE CASCADE,
    disposition TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    created_at TEXT NOT NULL,
    -- `merged_away` is the narrow terminal outcome for a committed relationship candidate whose
    -- edge a later person merge removed as a self-loop. It carries no entity id rather than
    -- dangling, and no other entity type can reach it.
    CHECK (
        (disposition = 'entity' AND entity_id IS NOT NULL)
        OR (disposition = 'merged_away' AND entity_id IS NULL AND entity_type = 'relationship')
    )
);

-- `source show` pages mappings by `candidate_id ASC` within one source session.
CREATE INDEX idx_import_candidate_mappings_source
    ON import_candidate_mappings(source_session_id, candidate_id);

-- Hard forget deletes every mapping targeting an entity it actually erased.
CREATE INDEX idx_import_candidate_mappings_entity
    ON import_candidate_mappings(entity_type, entity_id);
