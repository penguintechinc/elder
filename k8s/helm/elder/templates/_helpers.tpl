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
Resource name helper for components.
Postgres and Redis keep the `<release>-postgres` / `<release>-redis` prefix so
existing cluster PVCs and DATABASE_URL/REDIS_URL DNS names stay intact; app
workloads use their exact standard service name (`hub-webui`, `hub-api`,
`svc-worker`, `svc-scanner`, `svc-integrations`, `svc-flows-invoker`).
*/}}
{{- define "elder.componentResourceName" -}}
{{- $component := index . 0 -}}
{{- $context := index . 1 -}}
{{- if or (eq $component "postgres") (eq $component "redis") -}}
{{- printf "%s-%s" (include "elder.fullname" $context) $component | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $component | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
Image name helper.
global.imageRegistry only applies to the PenguinTech-built app images
(hub-webui/hub-api/svc-scanner/svc-integrations/svc-worker/svc-flows-invoker) —
postgres and redis are external upstream images (pgvector/pgvector, redis) that
must keep pulling from their own registry (docker.io).
*/}}
{{- define "elder.image" -}}
{{- $component := index . 0 -}}
{{- $context := index . 1 -}}
{{- $isAppImage := or (eq $component "hub-webui") (eq $component "hub-api") (eq $component "hub-mcp") (eq $component "svc-scanner") (eq $component "svc-integrations") (eq $component "svc-worker") (eq $component "svc-flows-invoker") (eq $component "svc-runworker") -}}
{{- $registry := "" -}}
{{- if $isAppImage -}}
{{- $registry = $context.Values.global.imageRegistry -}}
{{- end -}}
{{- $repository := (index $context.Values $component).image.repository -}}
{{- $tag := toString ((index $context.Values $component).image.tag | default $context.Chart.AppVersion) -}}
{{- if or (eq $tag "latest") (and (hasSuffix "latest" $tag) (not (include "elder.isDevEnvironment" $context))) -}}
{{- fail (printf "%s.image.tag %q is a mutable 'latest' tag; pin an explicit version tag or digest (defaults to the chart appVersion when empty)." $component $tag) -}}
{{- end -}}
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


{{/*
svc-runworker isolation policy. Validates the runner backend against the
isolation boundary and returns the effective backend name. Rendering fails
when tenant code would run without a VM isolation boundary and no
"KATA EXCEPTION (approved)" flag is recorded in values.
*/}}
{{- define "elder.runworker.backend" -}}
{{- $cfg := index .Values "svc-runworker" -}}
{{- $backend := $cfg.runnerBackend | default "k8s_job" -}}
{{- if not (has $backend (list "k8s_job" "sandbox" "auto")) -}}
{{- fail (printf "svc-runworker.runnerBackend %q is not supported (k8s_job | sandbox | auto)" $backend) -}}
{{- end -}}
{{- $exc := $cfg.kataException | default dict -}}
{{- $classes := $cfg.sandboxedRuntimeClasses | default (list "kata" "kata-qemu" "kata-clh" "kata-fc") -}}
{{- $podSandboxed := has ($cfg.podRuntimeClassName | default "") $classes -}}
{{- if and (eq $backend "sandbox") (not $podSandboxed) (not $exc.inPodSandbox) -}}
{{- fail "svc-runworker.runnerBackend=sandbox runs tenant code inside the worker pod: set svc-runworker.podRuntimeClassName to a Kata runtime class, or record svc-runworker.kataException.inPodSandbox=true (KATA EXCEPTION approved)" -}}
{{- end -}}
{{- if and (eq $backend "auto") (not $exc.inPodSandbox) -}}
{{- fail "svc-runworker.runnerBackend=auto runs tenant code in a pod that also holds a Kubernetes API token: use k8s_job, or record svc-runworker.kataException.inPodSandbox=true (KATA EXCEPTION approved)" -}}
{{- end -}}
{{- /* Membership, not mere presence: "runc" or a misspelt class is not a VM boundary. */ -}}
{{- $execSandboxed := has ($cfg.runtimeClassName | default "") $classes -}}
{{- if and (ne $backend "sandbox") (not $execSandboxed) (not $exc.executionPods) -}}
{{- fail (printf "svc-runworker.runtimeClassName %q is not one of svc-runworker.sandboxedRuntimeClasses: execution pods must run under a Kata runtime class, or record svc-runworker.kataException.executionPods=true (KATA EXCEPTION approved)" ($cfg.runtimeClassName | default "")) -}}
{{- end -}}
{{- /* Execution pods rely on the "restricted" Pod Security labels of their namespace. */ -}}
{{- $execNs := $cfg.executionNamespace | default "elder-runs" -}}
{{- if ne $backend "sandbox" -}}
{{- if eq $execNs .Values.namespace -}}
{{- fail (printf "svc-runworker.executionNamespace %q must differ from the release namespace: tenant execution pods may not share a namespace with platform workloads" $execNs) -}}
{{- end -}}
{{- if and (not $cfg.createExecutionNamespace) (not $cfg.executionNamespacePodSecurityVerified) -}}
{{- fail (printf "svc-runworker.createExecutionNamespace=false: the chart cannot guarantee the pod-security.kubernetes.io/enforce=restricted label on namespace %q. Label it yourself and record svc-runworker.executionNamespacePodSecurityVerified=true, or let the chart create it" $execNs) -}}
{{- end -}}
{{- end -}}
{{- $backend -}}
{{- end }}

{{/*
Restriction gateway ports opened between execution pods and the controller:
one per concurrent execution, starting at svc-runworker.gateway.portBase.
*/}}
{{- define "elder.runworker.gatewayPorts" -}}
{{- $cfg := index .Values "svc-runworker" -}}
{{- $base := int (($cfg.gateway).portBase | default 18080) -}}
{{- $count := int (($cfg.env).RUNS_MAX_CONCURRENT | default "5") -}}
{{- if or (lt $count 1) (gt $count 40) -}}
{{- fail "svc-runworker.env.RUNS_MAX_CONCURRENT must be between 1 and 40 (one NetworkPolicy gateway port per concurrent execution)" -}}
{{- end -}}
{{- range $i := until $count }}
- port: {{ add $base $i | quote }}
  protocol: TCP
{{- end }}
{{- end }}
