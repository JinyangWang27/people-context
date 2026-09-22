-- people-context schema (user_version 10). Later changes are forward-only numbered migrations.

CREATE TABLE persons (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    canonical_name_normalized TEXT NOT NULL,
    is_self INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE aliases (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    value TEXT NOT NULL,
    value_normalized TEXT NOT NULL,
    kind TEXT NOT NULL,
    lang TEXT,
    script TEXT
);

CREATE TABLE organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT,
    name_normalized TEXT
);

CREATE TABLE affiliations (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    provenance_source TEXT NOT NULL,
    provenance_session TEXT,
    provenance_stated_by TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE relationships (
    id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    object_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    label TEXT,
    valid_from TEXT,
    valid_to TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    provenance_source TEXT NOT NULL,
    provenance_session TEXT,
    provenance_stated_by TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE facts (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    predicate TEXT NOT NULL,
    value TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    recorded_at TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    sensitivity TEXT NOT NULL DEFAULT 'personal',
    provenance_source TEXT NOT NULL,
    provenance_session TEXT,
    provenance_stated_by TEXT
);

CREATE TABLE observations (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    sensitivity TEXT NOT NULL DEFAULT 'personal',
    provenance_source TEXT NOT NULL,
    provenance_session TEXT,
    provenance_stated_by TEXT
);

CREATE TABLE traits (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    value TEXT NOT NULL,
    evidence_note TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    sensitivity TEXT NOT NULL DEFAULT 'personal',
    provenance_source TEXT NOT NULL,
    provenance_session TEXT,
    provenance_stated_by TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE interactions (
    id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    channel TEXT,
    sensitivity TEXT NOT NULL DEFAULT 'personal',
    provenance_source TEXT NOT NULL,
    provenance_session TEXT,
    provenance_stated_by TEXT
);

CREATE TABLE interaction_participants (
    interaction_id TEXT NOT NULL REFERENCES interactions(id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    PRIMARY KEY (interaction_id, person_id)
);

CREATE TABLE reminders (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    kind TEXT NOT NULL,
    due_at TEXT,
    recurrence TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE user_preferences (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE import_staging (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    source TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    op TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL
);

CREATE VIRTUAL TABLE person_search USING fts5(name, person_id UNINDEXED);

CREATE TABLE devices (
    id TEXT PRIMARY KEY,
    display_name TEXT,
    public_key TEXT,
    created_at TEXT NOT NULL,
    retired_at TEXT,
    hlc_physical_ms INTEGER NOT NULL DEFAULT 0,
    hlc_logical INTEGER NOT NULL DEFAULT 0
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

CREATE TABLE sync_conflicts (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    conflict_kind TEXT NOT NULL,
    candidate_ops_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE relationship_types (
    type TEXT PRIMARY KEY,
    inverse TEXT,
    symmetric INTEGER NOT NULL DEFAULT 0,
    category TEXT NOT NULL,
    canonical INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE relationship_type_synonyms (
    synonym TEXT PRIMARY KEY,
    type TEXT NOT NULL REFERENCES relationship_types(type)
);

CREATE TABLE trait_evidence (
    trait_id TEXT NOT NULL REFERENCES traits(id) ON DELETE CASCADE,
    evidence_type TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (trait_id, evidence_type, evidence_id),
    CHECK (evidence_type IN ('observation', 'interaction'))
);

CREATE TABLE identified_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_normalized TEXT NOT NULL,
    kind TEXT NOT NULL,
    organization_id TEXT REFERENCES organizations(id),
    sensitivity TEXT NOT NULL DEFAULT 'personal',
    provenance_source TEXT NOT NULL,
    provenance_session TEXT,
    provenance_stated_by TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE group_memberships (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL REFERENCES identified_groups(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    temporal_basis TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    sensitivity TEXT NOT NULL DEFAULT 'personal',
    provenance_source TEXT NOT NULL,
    provenance_session TEXT,
    provenance_stated_by TEXT,
    created_at TEXT NOT NULL
);

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
    CHECK (status IN ('staged', 'partially_committed', 'committed', 'redacted', 'withdrawn')),
    -- A claim is only meaningful over real source bytes.
    CHECK (claim_key IS NULL OR content_digest IS NOT NULL),
    -- The minimal-claim invariant for a terminal redacted receipt. A withdrawn receipt is deliberately
    -- not held to it: erasure removed a redacted receipt's content, while a withdrawal removed nothing
    -- and must keep what it still has to show.
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

CREATE TABLE import_candidate_mappings (
    candidate_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL REFERENCES import_source_sessions(id) ON DELETE CASCADE,
    disposition TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    created_at TEXT NOT NULL,
    CHECK (
        (disposition = 'entity' AND entity_id IS NOT NULL)
        OR (disposition = 'merged_away' AND entity_id IS NULL AND entity_type = 'relationship')
    )
);

CREATE INDEX idx_persons_canonical_norm ON persons(canonical_name_normalized);
CREATE INDEX idx_aliases_person ON aliases(person_id);
CREATE INDEX idx_aliases_value_norm ON aliases(value_normalized);
CREATE INDEX idx_affiliations_person ON affiliations(person_id);
CREATE INDEX idx_affiliations_org ON affiliations(org_id);
CREATE INDEX idx_relationships_subject ON relationships(subject_id);
CREATE INDEX idx_relationships_object ON relationships(object_id);
CREATE INDEX idx_facts_person ON facts(person_id);
CREATE INDEX idx_observations_person ON observations(person_id);
CREATE INDEX idx_traits_person ON traits(person_id);
CREATE INDEX idx_interaction_participants_person ON interaction_participants(person_id);
CREATE INDEX idx_reminders_person ON reminders(person_id);
CREATE INDEX idx_audit_log_ts ON audit_log(ts);
CREATE INDEX changelog_origin_order
    ON changelog(device_id, hlc_physical_ms, hlc_logical, op_id);
CREATE INDEX idx_organizations_name_norm ON organizations(name_normalized);
CREATE INDEX idx_changelog_entity ON changelog(entity_id);
CREATE INDEX idx_changelog_replication_order
    ON changelog(hlc_physical_ms, hlc_logical, device_id, op_id);
CREATE INDEX idx_import_staging_batch
    ON import_staging(batch_id, created_at, id);
CREATE INDEX idx_trait_evidence_target
    ON trait_evidence(evidence_type, evidence_id);
CREATE INDEX idx_identified_groups_name_norm ON identified_groups(name_normalized, id);
CREATE INDEX idx_group_memberships_person ON group_memberships(person_id);
CREATE INDEX idx_group_memberships_group ON group_memberships(group_id);
CREATE INDEX idx_import_source_sessions_recent
    ON import_source_sessions(created_at DESC, id DESC);
CREATE INDEX idx_import_candidate_mappings_source
    ON import_candidate_mappings(source_session_id, candidate_id);
CREATE INDEX idx_import_candidate_mappings_entity
    ON import_candidate_mappings(entity_type, entity_id);

-- Relationship vocabulary reference data. Seed rows are not user assertions.

INSERT INTO relationship_types (type, inverse, symmetric, category, canonical) VALUES
    ('reports_to', 'manages', 0, 'professional', 1),
    ('manages', 'reports_to', 0, 'professional', 0),
    ('mentor_of', 'mentee_of', 0, 'professional', 1),
    ('mentee_of', 'mentor_of', 0, 'professional', 0),
    ('colleague_of', NULL, 1, 'professional', 1),
    ('parent_of', 'child_of', 0, 'family', 1),
    ('child_of', 'parent_of', 0, 'family', 0),
    ('sibling_of', NULL, 1, 'family', 1),
    ('cousin_of', NULL, 1, 'family', 1),
    ('spouse_of', NULL, 1, 'family', 1),
    ('partner_of', NULL, 1, 'family', 1),
    ('friend_of', NULL, 1, 'social', 1),
    ('neighbor_of', NULL, 1, 'social', 1),
    ('acquaintance_of', NULL, 1, 'social', 1);

INSERT INTO relationship_type_synonyms (synonym, type) VALUES
    ('reports_to', 'reports_to'),
    ('reports_to_manager', 'reports_to'),
    ('manager_of', 'manages'),
    ('manages', 'manages'),
    ('mentor', 'mentor_of'),
    ('mentors', 'mentor_of'),
    ('mentee', 'mentee_of'),
    ('colleague', 'colleague_of'),
    ('coworker', 'colleague_of'),
    ('parent', 'parent_of'),
    ('child', 'child_of'),
    ('sibling', 'sibling_of'),
    ('cousin', 'cousin_of'),
    ('spouse', 'spouse_of'),
    ('partner', 'partner_of'),
    ('friend', 'friend_of'),
    ('friend_of', 'friend_of'),
    ('neighbor', 'neighbor_of'),
    ('neighbour', 'neighbor_of'),
    ('acquaintance', 'acquaintance_of');
