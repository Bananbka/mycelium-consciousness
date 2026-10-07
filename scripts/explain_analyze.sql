-- Lab 7: EXPLAIN ANALYZE before/after for the hot queries.
--
--   docker compose -f infra/docker-compose.yaml exec -T db \
--     psql -U admin -d clone_memory_sandbox < scripts/explain_analyze.sql
--
-- Everything runs in one transaction that is rolled back at the end, so the
-- synthetic data (5 000 clones x 40 backups = 200 000 rows) and the temporary
-- DROP INDEX never touch the real tables. Run against a sandbox database only.

\set ON_ERROR_STOP on
\pset pager off
BEGIN;

INSERT INTO users (email, password_hash, role, is_active)
SELECT 'explain-' || g || '@example.com', 'x', 'clone', true
FROM generate_series(1, 5000) AS g;

INSERT INTO clone_profiles (designation, user_id)
SELECT 'explain-clone-' || u.id, u.id
FROM users u WHERE u.email LIKE 'explain-%@example.com';

INSERT INTO memory_backups
    (clone_id, period_start, period_end, entry_count, storage_key, subscription_tier)
SELECT p.id,
       now() - (b || ' days')::interval - interval '1 hour',
       now() - (b || ' days')::interval,
       10, 'explain/' || p.id || '/' || b, 'free'
FROM clone_profiles p, generate_series(1, 40) AS b
WHERE p.designation LIKE 'explain-clone-%';

ANALYZE users;
ANALYZE clone_profiles;
ANALYZE memory_backups;

SELECT min(id) AS uid FROM users WHERE email LIKE 'explain-%' \gset
SELECT id AS cid FROM clone_profiles WHERE user_id = :uid \gset

\echo
\echo '=== 1a. auth lookup BEFORE: two statements (SELECT users, then SELECT profile; selectinload) ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT * FROM users WHERE users.id = :uid;
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT * FROM clone_profiles WHERE clone_profiles.user_id IN (:uid);

\echo
\echo '=== 1b. auth lookup AFTER: one LEFT OUTER JOIN (joinedload) ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT users.*, clone_profiles.*
FROM users LEFT OUTER JOIN clone_profiles ON users.id = clone_profiles.user_id
WHERE users.id = :uid;

\echo
\echo '=== 2a. GET /memories/backups WITH ix_memory_backups_clone_id_period_end ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT * FROM memory_backups
WHERE clone_id = :cid ORDER BY period_end DESC LIMIT 50;

\echo
\echo '=== 3a. find_due_clones (max(period_end) per clone) WITH the index ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT p.id FROM clone_profiles p
LEFT OUTER JOIN (
    SELECT clone_id, max(period_end) AS last_end
    FROM memory_backups GROUP BY clone_id
) lb ON lb.clone_id = p.id
WHERE coalesce(lb.last_end, p.created_at) <= now() - interval '30 days';

DROP INDEX ix_memory_backups_clone_id_period_end;

\echo
\echo '=== 2b. same listing WITHOUT the index ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT * FROM memory_backups
WHERE clone_id = :cid ORDER BY period_end DESC LIMIT 50;

\echo
\echo '=== 3b. find_due_clones WITHOUT the index ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT p.id FROM clone_profiles p
LEFT OUTER JOIN (
    SELECT clone_id, max(period_end) AS last_end
    FROM memory_backups GROUP BY clone_id
) lb ON lb.clone_id = p.id
WHERE coalesce(lb.last_end, p.created_at) <= now() - interval '30 days';

ROLLBACK;
