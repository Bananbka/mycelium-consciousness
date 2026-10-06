{{/*
Chart name, shortened to fit Kubernetes' 63-character name limit.
*/}}
{{- define "clone-factory.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Full name every object is built from: <release>-<chart>, or just the release
name when it already contains the chart name. Kept to 45 characters so a
component suffix such as "-celery-worker" still fits in 63.
*/}}
{{- define "clone-factory.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 45 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 45 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 45 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Name of one component's objects. Call with (dict "ctx" . "component" "api").
Also the Service DNS name other components use to reach it.
*/}}
{{- define "clone-factory.componentName" -}}
{{- printf "%s-%s" (include "clone-factory.fullname" .ctx) .component -}}
{{- end }}

{{/*
Chart name and version, as shown by `helm list`.
*/}}
{{- define "clone-factory.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Labels that identify a component's pods. Immutable once a Deployment exists, so
they stay minimal. Call with (dict "ctx" . "component" "api").
*/}}
{{- define "clone-factory.selectorLabels" -}}
app.kubernetes.io/name: {{ include "clone-factory.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Full label set for an object. Call with (dict "ctx" . "component" "api").
*/}}
{{- define "clone-factory.labels" -}}
helm.sh/chart: {{ include "clone-factory.chart" .ctx }}
{{ include "clone-factory.selectorLabels" . }}
app.kubernetes.io/version: {{ .ctx.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .ctx.Release.Service }}
{{- end }}

{{/*
Name of the ConfigMap and of the Secret the pods read their environment from.
*/}}
{{- define "clone-factory.configName" -}}
{{- printf "%s-config" (include "clone-factory.fullname" .) }}
{{- end }}

{{- define "clone-factory.secretName" -}}
{{- default (printf "%s-secret" (include "clone-factory.fullname" .)) .Values.secret.existingSecret }}
{{- end }}

{{/*
repository:tag. The tag falls back to the chart's appVersion.
Call with (dict "image" .Values.api.image "root" .).
*/}}
{{- define "clone-factory.image" -}}
{{- printf "%s:%s" .image.repository (.image.tag | default .root.Chart.AppVersion | toString) -}}
{{- end }}

{{/*
Init container that blocks until the migration Job has stamped alembic_version.
Shared by the API, the worker and beat.
*/}}
{{- define "clone-factory.waitForMigrations" -}}
- name: wait-for-migrations
  image: {{ include "clone-factory.image" (dict "image" .Values.postgres.image "root" .) }}
  imagePullPolicy: {{ .Values.imagePullPolicy }}
  command:
    - sh
    - -c
    - until psql -h {{ include "clone-factory.componentName" (dict "ctx" . "component" "postgres") }} -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT count(*) FROM alembic_version" 2>/dev/null | grep -q '^[1-9]'; do echo waiting for migrations; sleep 3; done
  envFrom:
    - configMapRef:
        name: {{ include "clone-factory.configName" . }}
  env:
    - name: PGPASSWORD
      valueFrom:
        secretKeyRef:
          name: {{ include "clone-factory.secretName" . }}
          key: POSTGRES_PASSWORD
  resources:
    {{- toYaml .Values.initContainer.resources | nindent 4 }}
{{- end }}
