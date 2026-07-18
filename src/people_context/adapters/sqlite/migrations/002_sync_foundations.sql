-- M6 local sync foundations: installation identity, durable changelog, and conflict staging.
-- Existing domain rows are intentionally not backfilled into changelog history.

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

CREATE INDEX changelog_origin_order
    ON changelog(device_id, hlc_physical_ms, hlc_logical, op_id);

CREATE TABLE sync_conflicts (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    conflict_kind TEXT NOT NULL,
    candidate_ops_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
