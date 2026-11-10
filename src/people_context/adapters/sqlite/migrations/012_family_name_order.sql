-- Preserve existing name components; name order is an explicit per-person choice.
ALTER TABLE persons ADD COLUMN family_name_first INTEGER NOT NULL DEFAULT 0
    CHECK (family_name_first IN (0, 1));
