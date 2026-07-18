-- M7 follow-up: indexed organization name matching and changelog entity lookups.
-- people_normalize is a deterministic SQL function registered by open_db before
-- migrations run; it applies the same normalization as domain normalize_name.

ALTER TABLE organizations ADD COLUMN name_normalized TEXT;

UPDATE organizations SET name_normalized = people_normalize(name);

CREATE INDEX idx_organizations_name_norm ON organizations(name_normalized);

CREATE INDEX idx_changelog_entity ON changelog(entity_id);
