# Kubernetes deployment (DevOps lab 3)

Runs the whole stack on a local minikube cluster: the API, the Celery worker
and beat, the migration Job, and Postgres, Redis and MinIO with persistent
volumes. Everything lives in the `clone-factory` namespace, one file per object.

## Prerequisites

- Docker Desktop (running), `kubectl`, `minikube`
- A cluster with the `ingress` and `metrics-server` addons:

```bash
minikube start --driver=docker --memory=4096 --cpus=2
minikube addons enable metrics-server
minikube addons enable ingress
kubectl get nodes
kubectl get pods -n kube-system
```

## Images

Application images are built from the repository root and loaded straight into
the cluster. There is no registry, so the manifests use
`imagePullPolicy: IfNotPresent`. Two versions exist so that update and rollback
can be demonstrated; `1.1.0` differs from `1.0.0` by a `version` field in
`GET /health`. The Celery image is the same for both tags.

```bash
docker build -f services/api/src/api/Dockerfile.api       -t clone-api:1.0.0 .
docker build -f services/celery/src/Dockerfile.celery     -t clone-celery:1.0.0 .
# change APP_VERSION in services/api/src/api/main.py, then:
docker build -f services/api/src/api/Dockerfile.api       -t clone-api:1.1.0 .
docker tag clone-celery:1.0.0 clone-celery:1.1.0

minikube image load clone-api:1.0.0
minikube image load clone-api:1.1.0
minikube image load clone-celery:1.0.0
minikube image load clone-celery:1.1.0
```

MinIO's upstream registries no longer served the image when this was written, so
a locally pulled copy is pinned under a local tag and loaded the same way:

```bash
docker tag <IMAGE ID of minio/minio:latest> minio-local:lab
minikube image load minio-local:lab
```

## Deploy

One command applies the whole directory, in file-name order (hence the numeric
prefixes: namespace, config, storage, then workloads):

```bash
kubectl apply -f k8s/
kubectl get pods,deploy,rs,svc -n clone-factory
kubectl get ingress,pvc -n clone-factory
```

The `migrate` Job runs `alembic upgrade head`. `api`, `celery-worker` and
`celery-beat` each have an init container that waits until `alembic_version`
holds a row, so a cold start needs no manual ordering.

### Open the application

On Windows with the docker driver the tunnel only lives while the command runs,
and the port changes every time. Keep it in its own terminal:

```bash
minikube service api -n clone-factory --url
curl http://127.0.0.1:<port>/health
```

`/docs` serves the OpenAPI UI. The Ingress (`nginx` class, no host) also routes
`/` to the `api` Service once `minikube tunnel` is running.

## Objects

| File | Object | Purpose |
|---|---|---|
| `00-namespace` | Namespace `clone-factory` | Isolates everything below |
| `10-configmap` | ConfigMap `clone-config` | Non-secret settings |
| `11-secret` | Secret `clone-secret` | Passwords and keys (lab values only) |
| `20`-`22-postgres-*` | PVC, Deployment, Service | Database, 1Gi volume |
| `30`-`32-redis-*` | PVC, Deployment, Service | Stream buffer and broker, AOF on, 512Mi |
| `40`-`42-minio-*` | PVC, Deployment, Service | Backup object storage, 2Gi |
| `50-migrate-job` | Job | Applies Alembic migrations |
| `60-api-deployment` | Deployment, 2 replicas | REST API, rolling update with `maxUnavailable: 0` |
| `61-api-service` | Service, NodePort | `api:8000` in the cluster, node port from the host |
| `62-api-ingress` | Ingress | HTTP entry point |
| `70-celery-worker-deployment` | Deployment | Stream flush and rollup tasks |
| `71-celery-beat-deployment` | Deployment, 1 replica | Scheduler; never scale it past one |

## Configuration

### ConfigMap `clone-config`

| Key | Value | Meaning |
|---|---|---|
| `POSTGRES_USER` | `admin` | Database user |
| `POSTGRES_DB` | `clone_memory_db` | Database name |
| `POSTGRES_HOST` | `postgres` | Service name used as the DNS host |
| `POSTGRES_PORT` | `5432` | Database port |
| `DB_POOL_SIZE` | `5` | SQLAlchemy pool size, per process |
| `DB_MAX_OVERFLOW` | `2` | Extra connections, per process |
| `DB_POOL_RECYCLE` | `1800` | Connection recycle time in seconds |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Broker and the Redis stream store |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/0` | Task results |
| `CELERY_TASK_QUEUE` | `memory_batches` | Queue name |
| `ROLLUP_CHECK_INTERVAL_SECONDS` | `3600` | How often beat checks for due backups |
| `STREAM_FLUSH_INTERVAL_SECONDS` | `60` | How often beat drains the Redis streams |
| `MEMORY_STREAM_MAXLEN` | `10000` | Stream length that triggers an immediate flush |
| `MINIO_ENDPOINT` | `http://minio:9000` | Object storage endpoint |
| `MINIO_BUCKET` | `memory-backups` | Bucket for backups |
| `JWT_ALGORITHM` | `HS256` | Token signing algorithm |
| `ACCESS_TOKEN_TTL_MINUTES` | `60` | Token lifetime |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `8000` | API bind address |
| `API_WORKERS` | `1` | Uvicorn workers per pod |
| `API_RELOAD` | `false` | Hot reload off |
| `LOG_LEVEL` | `INFO` | Log level |

The pool and worker numbers are deliberately small: Postgres allows 100
connections, and the total is `(DB_POOL_SIZE + DB_MAX_OVERFLOW) x processes`.
`CELERY_WORKER_CONCURRENCY=2` is set on the worker Deployment itself, because
the default of one process per node CPU exceeds the memory limit.

### Secret `clone-secret`

| Key | Used by | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | postgres, api, celery, migrate | Database password |
| `MINIO_ACCESS_KEY` | minio, api, celery | MinIO root user / S3 access key |
| `MINIO_SECRET_KEY` | minio, api, celery | MinIO root password / S3 secret key |
| `JWT_SECRET_KEY` | api | Token signing key, at least 32 characters |

These are throwaway values for the lab. Do not reuse them anywhere real.

Check that values reached a container, without printing secrets:

```bash
kubectl exec -n clone-factory deploy/api -- printenv POSTGRES_HOST API_WORKERS MINIO_BUCKET
kubectl exec -n clone-factory deploy/api -- sh -c 'printenv JWT_SECRET_KEY | wc -c'
```

## Lifecycle

```bash
# self-healing: delete a pod, a new one appears
kubectl delete pod -n clone-factory <api pod>
kubectl get pods -n clone-factory -l app.kubernetes.io/component=api -w

# scaling
kubectl scale deploy/api -n clone-factory --replicas=4

# rolling update, history and rollback
kubectl set image deploy/api api=clone-api:1.1.0 -n clone-factory
kubectl annotate deploy/api -n clone-factory kubernetes.io/change-cause="update api to 1.1.0" --overwrite
kubectl rollout status deploy/api -n clone-factory
kubectl rollout history deploy/api -n clone-factory
kubectl rollout undo deploy/api -n clone-factory
```

Probe traffic hits every pod too, so to show traffic spreading, send requests
with a marker and filter the logs for it:

```bash
curl "http://127.0.0.1:<port>/health?demo=1"
kubectl logs -n clone-factory -l app.kubernetes.io/component=api --prefix --since=2m | grep demo=1
```

`maxUnavailable: 0` keeps the old pods until the new ones pass their readiness
probe, so the application stays up during an update. A request or two can still
be dropped as a pod is removed from the Service.

## Diagnostics

```bash
# a tag that does not exist
kubectl set image deploy/api api=clone-api:9.9.9 -n clone-factory
kubectl get pods -n clone-factory -l app.kubernetes.io/component=api   # ErrImagePull -> ImagePullBackOff
kubectl describe pod -n clone-factory <failing pod>                    # Events: pull access denied
kubectl get events -n clone-factory --field-selector involvedObject.name=<failing pod>

# the old pods keep serving; logs and a shell in a healthy pod
kubectl logs -n clone-factory deploy/api --tail=10
kubectl exec -it -n clone-factory deploy/api -- sh

# fix
kubectl set image deploy/api api=clone-api:1.1.0 -n clone-factory
kubectl rollout status deploy/api -n clone-factory
```

The failing pod has no logs: its container never started, so the reason is only
in `describe` and the events.

## Tear down

```bash
kubectl delete -f k8s/                   # removes every object in the directory, PVCs and their data included
kubectl delete namespace clone-factory   # same result, plus anything created by hand
```
