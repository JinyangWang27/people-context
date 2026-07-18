-- M7 relationship vocabulary reference data. Seed rows are not user assertions.

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
