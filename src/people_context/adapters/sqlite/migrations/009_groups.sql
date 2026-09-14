-- M28.1 identified groups and membership assertions.
--
-- A group is a context people participate in; a membership asserts that one person held a role in
-- one group. Both carry their own sensitivity, because a public group can hold a private
-- membership and a sensitive group must not surface through a membership that looks ordinary.
--
-- The table is `identified_groups` rather than `groups`, which is an SQLite keyword. Kinds, roles,
-- and temporal bases are validated by the domain rather than by CHECK constraints, so the
-- vocabulary can grow without a table rebuild.
--
-- `name_normalized` is a lookup aid, not an identity: equal names stay distinct rows.
-- `organization_id` references an existing organization as placement context only; nothing here
-- creates or renames an organization.
--
-- `temporal_basis` states what the membership dates assert. Absent dates on a membership are
-- unknown, never unbounded; that distinction is what keeps a later overlap read honest.
--
-- Additive and forward-only. No affiliation or relationship is converted into a group.

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

CREATE INDEX idx_identified_groups_name_norm ON identified_groups(name_normalized, id);
CREATE INDEX idx_group_memberships_person ON group_memberships(person_id);
CREATE INDEX idx_group_memberships_group ON group_memberships(group_id);
