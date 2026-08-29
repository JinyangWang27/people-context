-- M18.3 trait evidence: the durable link from an inferred trait to what it rests on.
--
-- A trait is the one record type here that nobody asserted directly, so until now the only
-- account of where one came from was its free-text `evidence_note`. This relation adds the
-- id-based half: the observations and interactions the generalization was drawn from.
--
-- The primary key is the whole triple, so citing the same record twice is a no-op rather than a
-- duplicate row, and `evidence_type` is constrained to the two record types a trait may cite. A
-- trait citing another trait is deliberately impossible: that would be a belief chain, and every
-- link in it would look like grounding while adding no observed material.
--
-- `trait_id` carries a foreign key with cascade so a deleted trait can never leave a dangling
-- link. `evidence_id` cannot: it addresses one of two tables, which SQL has no way to declare.
-- Hard forget therefore deletes evidence-side links explicitly, in its own transaction, which is
-- also what lets it count them in the preview and redact their history.
--
-- Additive and forward-only. Traits recorded before M18.3 have no links and are not backfilled;
-- `evidence_note` remains exactly as useful as it was.

CREATE TABLE trait_evidence (
    trait_id TEXT NOT NULL REFERENCES traits(id) ON DELETE CASCADE,
    evidence_type TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (trait_id, evidence_type, evidence_id),
    CHECK (evidence_type IN ('observation', 'interaction'))
);

-- Hard forget of an observation or interaction deletes every link citing it.
CREATE INDEX idx_trait_evidence_target
    ON trait_evidence(evidence_type, evidence_id);
