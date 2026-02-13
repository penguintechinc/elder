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
Image name helper
*/}}
{{- define "elder.image" -}}
{{- $component := index . 0 -}}
{{- $context := index . 1 -}}
{{- $registry := $context.Values.global.imageRegistry -}}
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
