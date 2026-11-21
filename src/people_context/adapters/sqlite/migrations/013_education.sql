-- Education is its own record, not an affiliation kind: every affiliation reader means employment.
CREATE TABLE education (
    id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    degree TEXT,
    field_of_study TEXT,
    valid_from TEXT,
    valid_to TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    provenance_source TEXT NOT NULL,
    provenance_session TEXT,
    provenance_stated_by TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_education_person ON education (person_id);
CREATE INDEX idx_education_org ON education (org_id);

-- What an organization does belongs to the organization, not to each person's tenure there.
ALTER TABLE organizations ADD COLUMN industry TEXT;
