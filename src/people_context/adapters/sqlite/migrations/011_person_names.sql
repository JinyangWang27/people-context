-- Explicit optional names; existing canonical names and aliases remain untouched.
ALTER TABLE persons ADD COLUMN preferred_name TEXT;
ALTER TABLE persons ADD COLUMN preferred_name_normalized TEXT;
ALTER TABLE persons ADD COLUMN given_name TEXT;
ALTER TABLE persons ADD COLUMN given_name_normalized TEXT;
ALTER TABLE persons ADD COLUMN family_name TEXT;
ALTER TABLE persons ADD COLUMN family_name_normalized TEXT;
CREATE INDEX idx_persons_display_name ON persons (
    coalesce(preferred_name_normalized, given_name_normalized, canonical_name_normalized), id
) WHERE deleted_at IS NULL;
