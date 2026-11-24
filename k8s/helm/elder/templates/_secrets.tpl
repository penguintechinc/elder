{{/*
=============================================================================
Secret handling (issue #385)
=============================================================================
Every credential (signing keys, passwords, tokens, URLs with embedded
credentials) is injected via valueFrom.secretKeyRef from ONE Secret:

  secrets.existingSecret  - name of an operator-managed Secret (default
                            "elder-secrets"). Required outside dev/alpha.
  secrets.create          - dev/alpha ONLY: the chart renders the Secret
                            from secrets.devValues. Refused (render fails)
                            when `environment` is not dev/alpha/local.

Each component lists the env vars it needs in `<component>.secretEnv`:
  ENV_NAME: { key: <Secret data key>, optional: <bool> }
*/}}

{{- define "elder.isDevEnvironment" -}}
{{- if has (.Values.environment | default "production" | lower) (list "dev" "development" "local" "alpha") -}}true{{- end -}}
{{- end -}}

{{/* Name of the Secret every secretKeyRef points at; fails closed. */}}
{{- define "elder.secretName" -}}
{{- $s := .Values.secrets | default dict -}}
{{- if $s.create -}}
{{- if not (include "elder.isDevEnvironment" .) -}}
{{- fail (printf "secrets.create=true is only allowed when environment is dev/alpha/local (got %q). Create the Secret out-of-band and set secrets.existingSecret instead." (.Values.environment | default "production")) -}}
{{- end -}}
{{- $s.existingSecret | default "elder-secrets" -}}
{{- else if $s.existingSecret -}}
{{- $s.existingSecret -}}
{{- else -}}
{{- fail (printf "secrets.existingSecret is required (environment %q): create a Secret holding SECRET_KEY, JWT_SECRET_KEY, POSTGRES_PASSWORD, DB_PASSWORD, DATABASE_URL, REDIS_URL, ADMIN_PASSWORD and set secrets.existingSecret to its name. See k8s/helm/elder/SECRETS.md." (.Values.environment | default "production")) -}}
{{- end -}}
{{- end -}}

{{/*
Fail if a component's plain `env` map carries a credential literal.
Args: (list $component $componentConfig $root)
*/}}
{{- define "elder.assertNoSecretLiterals" -}}
{{- $component := index . 0 -}}
{{- $cfg := index . 1 -}}
{{- $secretEnv := $cfg.secretEnv | default dict -}}
{{- range $k, $v := ($cfg.env | default dict) -}}
{{- if hasKey $secretEnv $k -}}
{{- fail (printf "%s.env.%s duplicates %s.secretEnv.%s - remove it from env; the value comes from the Secret." $component $k $component $k) -}}
{{- end -}}
{{- $val := toString $v -}}
{{- $sensitiveName := and (regexMatch "(SECRET|PASSWORD|PASSWD|TOKEN|API_KEY|LICENSE_KEY|ACCESS_KEY|PRIVATE_KEY|SIGNING_KEY|(^|_)DSN$)" $k) (not (regexMatch "_(HOURS|DAYS|SECONDS|MINUTES|PATH|FILE|URL)$" $k)) -}}
{{- $credUrl := regexMatch "^[A-Za-z][A-Za-z0-9+.-]*://[^/@]*:[^/@]+@" $val -}}
{{- if and $val (or $sensitiveName $credUrl) -}}
{{- fail (printf "%s.env.%s must not be a literal value (credential). Move it to the Secret named by secrets.existingSecret and reference it via %s.secretEnv." $component $k $component) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Render secretKeyRef env entries for a component.
Args: (list $componentConfig $root) ; caller controls indentation via nindent.
*/}}
{{- define "elder.secretEnv" -}}
{{- $cfg := index . 0 -}}
{{- $root := index . 1 -}}
{{- $name := include "elder.secretName" $root -}}
{{- range $envName, $ref := ($cfg.secretEnv | default dict) }}
- name: {{ $envName }}
  valueFrom:
    secretKeyRef:
      name: {{ $name | quote }}
      key: {{ ($ref).key | default $envName | quote }}
      {{- if ($ref).optional }}
      optional: true
      {{- end }}
{{- end }}
{{- end -}}

{{/*
Pod-level securityContext: chart baseline merged with podSecurityContext
overrides. seccompProfile RuntimeDefault + runAsNonRoot are always enforced.
*/}}
{{- define "elder.podSecurityContext" -}}
{{- $base := dict "runAsNonRoot" true "runAsUser" 1000 "fsGroup" 1000 "seccompProfile" (dict "type" "RuntimeDefault") -}}
{{- $sc := mustMergeOverwrite $base (deepCopy (.Values.podSecurityContext | default dict)) -}}
{{- $_ := set $sc "runAsNonRoot" true -}}
{{- $_ := set $sc "seccompProfile" (dict "type" "RuntimeDefault") -}}
{{- toYaml $sc -}}
{{- end -}}

{{/*
Container securityContext: restricted baseline merged with the component's
(or global) override. Hard-enforced regardless of overrides:
runAsNonRoot, allowPrivilegeEscalation=false, privileged=false,
capabilities.drop=[ALL] (no adds), seccompProfile RuntimeDefault.
readOnlyRootFilesystem defaults true but may be set false per component.
Args: (list $componentSecurityContext $root)
*/}}
{{- define "elder.containerSecurityContext" -}}
{{- $override := index . 0 | default dict -}}
{{- $root := index . 1 -}}
{{- if not (index . 0) -}}{{- $override = $root.Values.securityContext | default dict -}}{{- end -}}
{{- $base := dict "runAsNonRoot" true "runAsUser" 1000 "allowPrivilegeEscalation" false "readOnlyRootFilesystem" true -}}
{{- $sc := mustMergeOverwrite $base (deepCopy $override) -}}
{{- $_ := set $sc "runAsNonRoot" true -}}
{{- $_ := set $sc "allowPrivilegeEscalation" false -}}
{{- $_ := unset $sc "privileged" -}}
{{- $_ := set $sc "capabilities" (dict "drop" (list "ALL")) -}}
{{- $_ := set $sc "seccompProfile" (dict "type" "RuntimeDefault") -}}
{{- toYaml $sc -}}
{{- end -}}

{{/*
Database connection budget guard (bundled Postgres only). Sums
(poolSize + maxOverflow) x max pods for every DB client component and fails
when it exceeds postgres.maxConnections - postgres.reservedConnections.
*/}}
{{- define "elder.assertDbBudget" -}}
{{- $pg := .Values.postgres | default dict -}}
{{- if $pg.enabled -}}
{{- $total := 0 -}}
{{- $detail := list -}}
{{- range $c := list "hub-api" "svc-worker" "svc-flows-invoker" -}}
{{- $cfg := index $.Values $c | default dict -}}
{{- if and $cfg $cfg.enabled $cfg.database -}}
{{- $pods := int ($cfg.replicaCount | default 1) -}}
{{- if and $cfg.autoscaling $cfg.autoscaling.enabled -}}{{- $pods = int ($cfg.autoscaling.maxReplicas | default $pods) -}}{{- end -}}
{{- $per := add (int ($cfg.database.poolSize | default 5)) (int ($cfg.database.maxOverflow | default 0)) -}}
{{- $total = add $total (mul $per $pods) -}}
{{- $detail = append $detail (printf "%s=%dx%d" $c $per $pods) -}}
{{- end -}}
{{- end -}}
{{- $budget := sub (int ($pg.maxConnections | default 100)) (int ($pg.reservedConnections | default 10)) -}}
{{- if gt (int $total) (int $budget) -}}
{{- fail (printf "DB connection budget exceeded: %d connections (%s) > postgres.maxConnections - reservedConnections = %d. Lower <component>.database.poolSize/maxOverflow or raise postgres.maxConnections." (int $total) (join ", " $detail) (int $budget)) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Env entries for a component's DB pool settings. Args: (list $component $cfg) */}}
{{- define "elder.dbPoolEnv" -}}
{{- $component := index . 0 -}}
{{- $cfg := index . 1 -}}
{{- with $cfg.database }}
- name: DB_POOL_SIZE
  value: {{ .poolSize | default 5 | quote }}
{{- if eq $component "hub-api" }}
- name: SQLALCHEMY_MAX_OVERFLOW
  value: {{ .maxOverflow | default 0 | quote }}
{{- end }}
{{- end }}
{{- end -}}
