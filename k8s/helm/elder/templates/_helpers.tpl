{{/*
Expand the name of the chart.
*/}}
{{- define "elder.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "elder.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "elder.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "elder.labels" -}}
helm.sh/chart: {{ include "elder.chart" . }}
{{ include "elder.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "elder.selectorLabels" -}}
app.kubernetes.io/name: {{ include "elder.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "elder.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "elder.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Component-specific labels
*/}}
{{- define "elder.componentLabels" -}}
{{- $component := index . 0 -}}
{{- $context := index . 1 -}}
{{ include "elder.labels" $context }}
app.kubernetes.io/component: {{ $component }}
{{- end }}

{{/*
Component-specific selector labels
*/}}
{{- define "elder.componentSelectorLabels" -}}
{{- $component := index . 0 -}}
{{- $context := index . 1 -}}
{{ include "elder.selectorLabels" $context }}
app.kubernetes.io/component: {{ $component }}
{{- end }}

{{/*
Image name helper.
global.imageRegistry only applies to the PenguinTech-built app images
(web/api/scanner/worker/flows-invoker) — postgres and redis are external
upstream images (pgvector/pgvector, redis) that must keep pulling from their
own registry (docker.io) regardless of the local alpha registry override,
otherwise setting global.imageRegistry: localhost:32000 breaks them.
*/}}
{{- define "elder.image" -}}
{{- $component := index . 0 -}}
{{- $context := index . 1 -}}
{{- $isAppImage := or (eq $component "web") (eq $component "api") (eq $component "scanner") (eq $component "worker") (eq $component "flows-invoker") -}}
{{- $registry := "" -}}
{{- if $isAppImage -}}
{{- $registry = $context.Values.global.imageRegistry -}}
{{- end -}}
{{- $repository := (index $context.Values $component).image.repository -}}
{{- $tag := (index $context.Values $component).image.tag -}}
{{- if $registry }}
{{- printf "%s/%s:%s" $registry $repository $tag }}
{{- else }}
{{- printf "%s:%s" $repository $tag }}
{{- end }}
{{- end }}

{{/*
Image pull policy helper
*/}}
{{- define "elder.imagePullPolicy" -}}
{{- $component := index . 0 -}}
{{- $context := index . 1 -}}
{{- (index $context.Values $component).image.pullPolicy | default "IfNotPresent" }}
{{- end }}

{{/*
Convert a camelCase module key to UPPER_SNAKE_CASE for ELDER_MODULE_<NAME> env vars.
e.g. servicesOncall -> SERVICES_ONCALL, aiSearch -> AI_SEARCH
*/}}
{{- define "elder.upperSnakeCase" -}}
{{- $input := . -}}
{{- $result := "" -}}
{{- range $i, $char := regexSplit "" $input -1 }}
{{- if and (regexMatch "[A-Z]" $char) (gt $i 0) }}
{{- $result = printf "%s_%s" $result $char }}
{{- else }}
{{- $result = printf "%s%s" $result ($char | upper) }}
{{- end }}
{{- end }}
{{- $result }}
{{- end }}
