# Mycelium Consciousness

Mycelium Consciousness is a high-load memory synchronization system for clone
implants. I chose this domain because it naturally requires data-intensive
architecture decisions: continuous ingestion, async processing, durable storage,
semantic vector search, caching, and horizontal scaling.

The system receives binary memory frames from clone implants, buffers them
through Redis, processes micro-batches in background workers, enriches memories
with embeddings, and stores metadata plus vectors in PostgreSQL with pgvector.
An administrative FastAPI service exposes health and future CRUD operations for
operators.

## Architecture

![Architecture of Mycelium Consciousness](docs/media/mycelium-consciousness-architecture.svg)

Main components:

- Clone implants stream memory frames over HTTP/2 gRPC.
- Nginx acts as an API gateway and load-balancing entry point.
- `services/grpc_ingester` accepts streaming memory payloads.
- Redis is used as a broker/cache between ingestion and processing.
- `services/celery` processes queued micro-batches asynchronously.
- PostgreSQL with pgvector stores clone profiles, memory chunks, and embeddings.
- `services/api` provides the administrative REST API.

## Development

Install dependencies and run quality checks:

```bash
uv sync --all-packages --dev
uv run ruff check .
uv run ruff format --check .
uv run pre-commit run --all-files
```

Run local infrastructure:

```bash
docker compose -f infra/docker-compose.yaml up --build
```
