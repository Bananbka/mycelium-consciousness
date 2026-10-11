-- Lab 9: plans for the statements behind the hot paths. Run per node:
--   COMPOSE=... bash profiling/run.sh explain          (control plane: db)
-- The data-plane statement (memory_backups) is run on a shard by run.sh.
\echo === auth: user by primary key (+ profile by unique user_id) ===
EXPLAIN (ANALYZE, BUFFERS)
SELECT u.id, u.email, u.role, u.is_active, p.id
FROM users u LEFT OUTER JOIN clone_profiles p ON p.user_id = u.id
WHERE u.id = 1;

\echo === auth, as issued by selectinload: two statements ===
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM users WHERE id = 1;
EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM clone_profiles WHERE user_id IN (1);

\echo === pre-ping / SELECT 1 for scale ===
EXPLAIN (ANALYZE) SELECT 1;
