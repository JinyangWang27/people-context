-- M13.4 changelog tail: index the global replication ordering key.
-- `changelog_origin_order` leads with device_id, so it cannot serve a cross-device ordered
-- range scan. Without this index, EXPLAIN QUERY PLAN reports `SCAN changelog` plus
-- `USE TEMP B-TREE FOR ORDER BY` for every `list_entries_after` poll; with it, the row-value
-- cursor comparison becomes a SEARCH that seeks directly to the requested position.
-- Additive and forward-only: no existing query changes meaning.

CREATE INDEX idx_changelog_replication_order
    ON changelog(hlc_physical_ms, hlc_logical, device_id, op_id);
