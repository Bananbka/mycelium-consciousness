# Celery Worker

Background worker service for memory micro-batch processing.

For Lab 1, the worker exposes a minimal `memory.process_batch` task. Future
iterations will chunk memories, call the embedding provider, and upsert vectors
into PostgreSQL.
