# clone-factory Helm chart (DevOps lab 4)

The Kubernetes manifests from [`k8s/`](../k8s/README.md), packaged as one
parameterised Helm chart: API (2+ replicas), Celery worker and beat, the
migration Job, Postgres, Redis and MinIO with persistent volumes. One release
creates the same set of objects as `kubectl apply -f k8s/`, with every name
built from the release name, so the chart can be installed more than once in a
cluster.

```
chart/
  Chart.yaml            name, description, version, appVersion
  values.yaml           defaults = the development environment
  values-dev.yaml       dev, spelled out
  values-prod.yaml      prod: more replicas/resources, newer tag, Ingress on, no secrets
  templates/
    _helpers.tpl        names, labels, image and shared init container
    configmap.yaml  secret.yaml
    postgres-*.yaml  redis-*.yaml  minio-*.yaml     PVC (optional), Deployment, Service
    migrate-job.yaml
    api-deployment.yaml  api-service.yaml  api-ingress.yaml (optional)
    celery-worker-deployment.yaml  celery-beat-deployment.yaml
    hooks/pre-upgrade-check.yaml   pre-upgrade hook
    tests/test-api-health.yaml     `helm test`
    NOTES.txt
```

The Namespace object of the plain manifests is not part of the chart: Helm
installs into the namespace you pass with `-n`, and `--create-namespace` makes it.

## `version` and `appVersion`

- `version` (`0.1.0`) is the version of the **chart**: templates and the
  defaults in `values.yaml`. It changes whenever the packaging changes, for
  example a new template, a renamed value or a changed probe, even if the
  application is untouched.
- `appVersion` (`1.0.0`) is the version of the **application** the chart
  deploys by default. Here it matches the `clone-api` / `clone-celery` image
  tags and is the fallback tag when `image.tag` is empty. A new application
  release changes `appVersion` (and the tags in the values files) without
  necessarily changing the chart's own structure.

The two move independently: `helm list` shows both as `CHART` and
`APP VERSION`.

## Prerequisites

Same as lab 3: a running minikube with the `ingress` addon, and the images
loaded into it (`clone-api:1.0.0`, `clone-api:1.1.0`, `clone-celery:1.0.0`,
`clone-celery:1.1.0`, `minio-local:lab`). Helm 3. Remove the lab 3 release
first so the object names do not clash: `kubectl delete -f k8s/`.

## Install

Development (defaults):

```bash
helm install clone-factory chart/ -n clone-dev --create-namespace
# equivalent, explicit:
helm install clone-factory chart/ -n clone-dev --create-namespace -f chart/values-dev.yaml
```

Production values leave all credentials empty, so the install fails until you
pass them (the values below are only a shape; use real ones from your password
manager or a Secret you created yourself):

```bash
helm install clone-factory chart/ -n clone-prod --create-namespace \
  -f chart/values-prod.yaml \
  --set secret.postgresPassword='...' \
  --set secret.minioAccessKey='...' \
  --set secret.minioSecretKey='...' \
  --set secret.jwtSecretKey='...'      # at least 32 characters
```

or, with a Secret named `clone-credentials` that holds `POSTGRES_PASSWORD`,
`MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` and `JWT_SECRET_KEY`:

```bash
helm install clone-factory chart/ -n clone-prod --create-namespace \
  -f chart/values-prod.yaml --set secret.existingSecret=clone-credentials
```

## Value precedence

Helm merges, lowest to highest priority:

1. the chart's own `values.yaml`
2. each `-f/--values` file, in the order given (the **last** one wins)
3. `--set` / `--set-string` / `--set-file` flags (always win)

Maps merge key by key, so `values-prod.yaml` only needs the keys that differ;
lists are replaced whole. Example: with `-f values-prod.yaml --set api.replicas=5`
the API gets 5 replicas (flag), tag `1.1.0` (file), and anything else from the
defaults.

## Values

| Key | Default | Meaning |
|---|---|---|
| `nameOverride`, `fullnameOverride` | `""` | Replace the chart name / the whole `<release>-<chart>` prefix |
| `imagePullPolicy` | `IfNotPresent` | For every container (images are loaded into minikube, not pulled) |
| `config.*` | see `values.yaml` | Non-secret env vars, one key per variable (`POSTGRES_USER`, `LOG_LEVEL`, ...) |
| `secret.existingSecret` | `""` | Use this Secret instead of creating one |
| `secret.postgresPassword` | dev placeholder | Database password; **empty in prod, required** |
| `secret.minioAccessKey` / `minioSecretKey` | dev placeholders | MinIO root credentials and S3 keys; empty in prod |
| `secret.jwtSecretKey` | dev placeholder | Token signing key, >= 32 characters; empty in prod |
| `postgres.image.repository` / `.tag` | `postgres` / `16-alpine` | Database image |
| `postgres.replicas` | `1` | Keep at 1 (single `ReadWriteOnce` volume) |
| `postgres.service.port` | `5432` | Service port |
| `postgres.persistence.enabled` | `true` | `false` swaps the PVC for an `emptyDir` |
| `postgres.persistence.size` / `.storageClass` | `1Gi` / `""` | Volume size and class (not resizable on upgrade) |
| `postgres.resources` | 100m/256Mi - 500m/512Mi | Requests and limits |
| `redis.*` | like postgres | Image `redis:7-alpine`, port 6379, 512Mi, AOF on |
| `minio.image.*` | `minio-local:lab` | Locally tagged image (upstream registries dropped it) |
| `minio.service.port` / `.consolePort` | `9000` / `9001` | S3 and console ports |
| `minio.persistence.*` | `true`, `2Gi` | As for postgres |
| `migrate.enabled` | `true` | Run `alembic upgrade head` as a Job on install/upgrade |
| `migrate.backoffLimit` / `.resources` | `6` / small | Job retries and resources |
| `api.replicas` | `2` | API pods (prod: `3`) |
| `api.image.repository` / `.tag` | `clone-api` / `1.0.0` | Empty tag falls back to `appVersion` (prod: `1.1.0`) |
| `api.strategy.maxSurge` / `.maxUnavailable` | `1` / `0` | Rolling update budget |
| `api.preStopSleepSeconds` | `5` | Grace while the Service stops routing to a terminating pod |
| `api.service.type` / `.port` | `NodePort` / `8000` | Service |
| `api.service.nodePort` | `""` | Empty = auto-assigned (a fixed one blocks a second install) |
| `api.ingress.enabled` | `false` | Create the Ingress (prod: `true`) |
| `api.ingress.className` / `.host` / `.annotations` | `nginx` / `""` / `{}` | Ingress settings |
| `api.resources` | 100m/192Mi - 500m/512Mi | Requests and limits (prod larger) |
| `celeryWorker.replicas` | `1` | Workers (prod: `2`) |
| `celeryWorker.image.*` | `clone-celery` / `1.0.0` | Worker image |
| `celeryWorker.concurrency` | `2` | `CELERY_WORKER_CONCURRENCY` |
| `celeryWorker.resources` | 100m/192Mi - 500m/512Mi | Requests and limits |
| `celeryBeat.replicas` | `1` | **Never above 1**: each extra replica schedules every task again |
| `celeryBeat.image.*`, `.resources` | `clone-celery` / `1.0.0` | Scheduler image and resources |
| `initContainer.resources` | tiny | For the wait-for-* init containers, the hook and the test |

Hostnames the application needs (`POSTGRES_HOST`, `CELERY_BROKER_URL`,
`CELERY_RESULT_BACKEND`, `MINIO_ENDPOINT`) are derived from the release name in
the ConfigMap template; they are not values.

Pods roll automatically when the ConfigMap or Secret changes (a checksum
annotation on the pod template), which plain `envFrom` would not do.

## Check before installing (no cluster needed)

```bash
helm lint chart/ -f chart/values-dev.yaml
helm lint chart/ -f chart/values-prod.yaml --set secret.postgresPassword=x \
  --set secret.minioAccessKey=x --set secret.minioSecretKey=x --set secret.jwtSecretKey=x

helm template clone-factory chart/ -n clone-dev  -f chart/values-dev.yaml  > /tmp/dev.yaml
helm template clone-factory chart/ -n clone-prod -f chart/values-prod.yaml \
  --set secret.postgresPassword=x --set secret.minioAccessKey=x \
  --set secret.minioSecretKey=x --set secret.jwtSecretKey=x > /tmp/prod.yaml
diff /tmp/dev.yaml /tmp/prod.yaml
```

Without the `--set secret...` flags the prod render stops with
`secret.postgresPassword is required`, which is the intended guard.

## Release lifecycle

```bash
helm list -A
helm status clone-factory -n clone-dev
helm get values clone-factory -n clone-dev            # only what you overrode
helm get values clone-factory -n clone-dev --all      # merged with the defaults
helm get manifest clone-factory -n clone-dev

helm upgrade clone-factory chart/ -n clone-dev --set api.image.tag=1.1.0
helm upgrade clone-factory chart/ -n clone-dev -f chart/values-prod.yaml --set secret...=...
helm history clone-factory -n clone-dev
helm rollback clone-factory 1 -n clone-dev
helm history clone-factory -n clone-dev               # rollback adds a new revision
helm uninstall clone-factory -n clone-dev
kubectl get all,pvc,cm,secret,ingress -n clone-dev    # nothing of the release is left
```

Note: `helm upgrade` without `--reuse-values` starts again from the chart
defaults plus the files and flags you pass now, so repeat the `-f` file and the
`--set` secrets on every upgrade (or use `secret.existingSecret`). PVCs are
deleted with the release; the data goes with them.

Two things the lab run showed:

- A PVC cannot be resized by an upgrade on most storage classes, so
  `values-prod.yaml` does not override `*.persistence.size`. Choose the sizes
  with `--set` on the first install of a fresh release.
- A *failed* upgrade can leave objects Helm does not track (for example the
  `migrate-<revision>` Job it had already created). `helm uninstall` will not
  remove them; check with `kubectl get all -n <namespace>` and delete by hand.
- Switching an existing release to the prod values must keep the same
  credentials: Postgres only reads `POSTGRES_PASSWORD` when the volume is empty.

Where Helm keeps the state: one Secret per revision, type `helm.sh/release.v1`,
named `sh.helm.release.v1.<release>.v<revision>`, in the release's namespace:

```bash
kubectl get secret -n clone-dev -l owner=helm
```

## Hook and test

- `templates/hooks/pre-upgrade-check.yaml`: a Job annotated
  `helm.sh/hook: pre-upgrade` that runs `pg_isready` against the release's
  Postgres before an upgrade changes anything. If it fails the upgrade aborts
  and the running revision stays as it was.
- `templates/tests/test-api-health.yaml`: a Pod annotated `helm.sh/hook: test`
  that calls the API Service's `/health` (which also runs `SELECT 1`):

```bash
helm test clone-factory -n clone-dev
```

(`--logs` is not usable: the test Pod is deleted as soon as it succeeds, which
keeps the namespace clean after `helm uninstall`.)
