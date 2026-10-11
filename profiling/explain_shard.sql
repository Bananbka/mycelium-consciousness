\echo === GET /memories/backups: newest 50 of one clone ===
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM memory_backups
WHERE clone_id = (SELECT clone_id FROM memory_backups LIMIT 1)
ORDER BY period_end DESC LIMIT 50;

\echo === the same without the composite index (hypothesis H-index) ===
BEGIN;
DROP INDEX ix_memory_backups_clone_id_period_end;
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM memory_backups
WHERE clone_id = (SELECT clone_id FROM memory_backups LIMIT 1)
ORDER BY period_end DESC LIMIT 50;
ROLLBACK;
