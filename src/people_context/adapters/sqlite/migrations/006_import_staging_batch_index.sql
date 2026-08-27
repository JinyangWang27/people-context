-- M16.1 bounded CLI import reads: index the batch predicate every staging read uses.
-- `import_staging` had no index at all, so `WHERE batch_id = ?` was a full table scan. The
-- bounded preflight's inner `LIMIT` caps the rows it *returns*, not the rows SQLite visits:
-- for a small or absent batch it still scanned every row of every historical batch, so review
-- and commit work grew with unrelated staging history despite the new bounded guarantee.
-- The trailing columns match `list_batch`'s `ORDER BY created_at, id`, so the same index also
-- removes that query's temp B-tree sort.
-- Additive and forward-only: no existing query changes meaning.

CREATE INDEX idx_import_staging_batch
    ON import_staging(batch_id, created_at, id);
