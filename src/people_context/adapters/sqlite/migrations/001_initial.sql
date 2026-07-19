-- Initial schema for people-context.
-- Datetimes: ISO-8601 TEXT (UTC, with offset). Dates: ISO date TEXT. Booleans: 0/1.
-- Provenance flattened into provenance_source / provenance_session / provenance_stated_by.

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
    kind TEXT
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

-- Repository-maintained full-text index over canonical names and alias values.
CREATE VIRTUAL TABLE person_search USING fts5(name, person_id UNINDEXED);

-- Covering indexes for common lookups.
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
