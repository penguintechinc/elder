# CodeRuns Worker (`svc-runworker`) — Isolation Model

CodeRuns execute tenant-supplied code. This page describes where that code
runs, what it can reach, and what an operator must provide.

## Trust boundaries

| Component | Runs tenant code? | Credentials it holds |
|---|---|---|
| `svc-runworker` (controller) | No (default) | Its own provisioned worker token; a namespaced Kubernetes token for the execution namespace |
| Execution pod (`elder-runs` namespace) | Yes | None. Optionally a random per-execution gateway token |
| `hub-api` | No | Platform signing key |

- The worker **never** holds the platform signing key. It refuses to start if
  `JWT_SECRET_KEY` or `SECRET_KEY` is present in its environment.
- The worker authenticates with a provisioned, revocable, runs-scoped worker
  token (`ELDER_INTERNAL_TOKEN`, issued once by `POST /api/v1/admin/workers`),
  delivered from a Kubernetes Secret. The chart fails to render without
  `svc-runworker.internalTokenSecret.name`.

## Supported isolation modes

| `runnerBackend` | Where tenant code runs | Requirement | Status |
|---|---|---|---|
| `k8s_job` (default) | One pod per execution | `runtimeClassName` (default `kata`) | Supported |
| `sandbox` | Inside the worker pod (warm) | `podRuntimeClassName` is a Kata class | Supported, opt-in |
| `auto` | In-pod when possible, else a Job | `kataException.inPodSandbox` | Exception only |

Running tenant code without a Kata boundary is unsupported for production. It
requires a recorded approval and an explicit values flag
(`kataException.executionPods` / `kataException.inPodSandbox`), annotated in
the environment values file with `# KATA EXCEPTION (approved)`. The worker logs
`runworker_kata_exception_active` at ERROR on every start while a flag is set.
Any other combination fails at render time and again at worker start-up; there
is no silent fallback.

### Execution pods (`k8s_job`)

- Dedicated ServiceAccount (`elder-run-exec`) with no RBAC, no mounted token,
  service links disabled.
- Non-root, read-only root filesystem, all capabilities dropped,
  `RuntimeDefault` seccomp, CPU/memory limits, `activeDeadlineSeconds`,
  `ttlSecondsAfterFinished`, `restricted` Pod Security on the namespace.
- Network: default-deny. A function granted API scopes or an egress allowlist
  may reach **only** its per-execution restriction gateway on the worker pod,
  which enforces the scopes and the per-function domain allowlist. There is no
  direct route to `hub-api`, DNS, or the internet.

### In-pod sandbox (`sandbox`)

The subprocess shares the worker's UID; resource limits and a stripped
environment are not a trust boundary on their own. The Kata VM is the
boundary. Inside it, as defence in depth: the worker process is non-dumpable
(children cannot read its environment or memory, or attach to it), the worker
token is removed from the environment after start-up, children get a new
session, no inherited descriptors, and a private workspace. No Kubernetes API
token is mounted in this mode.

All executions on one warm worker pod share that pod. Use `k8s_job` when
executions from different tenants must be isolated from each other.

## Controller network policy

Egress: DNS, kube-apiserver (Job backend), `hub-api`, the OTLP collector, plus
anything listed in `svc-runworker.networkPolicy.egressFQDNs` (HTTPS only) and
`s3Egress`. There is no open internet rule. `egressFQDNs` is the upper bound
for function egress allowlists and is where a remote-cluster Elder API host
goes.

Controller RBAC (execution namespace only): `jobs` create, `jobs/status` get,
`pods` list, `pods/log` get, `configmaps` create/delete, `secrets`
create/delete. The controller has no `get`, `list` or `watch` on Secrets: it
can write and remove the per-execution gateway token but never read a Secret.

## Runtime class check

`runtimeClassName` (execution pods) and `podRuntimeClassName` (worker pod)
must be **members** of `svc-runworker.sandboxedRuntimeClasses`. A non-empty
value is not enough: `runc` or a misspelt class fails `helm template`, and the
worker, which receives the same list as `RUNS_SANDBOXED_RUNTIME_CLASSES`,
logs an error and refuses to start. The only other way to render or start is
the recorded exception (`kataException.executionPods` /
`kataException.inPodSandbox`), which is logged at ERROR on every start.

## Gateway token delivery

The per-execution gateway token is never written into a Job or Pod spec. For
each execution that needs the gateway, the controller creates one immutable
Secret `elder-run-gw-<execution-id>` (labelled with the execution id, owned by
the Job so garbage collection removes it) and the pod reads it through
`secretKeyRef`. The controller deletes the Secret when the execution ends,
whatever the outcome. Execution pods have no API token and no RBAC, so they
cannot read each other's Secret. The token is bound to its execution and
tenant and is accepted only by the gateway instance started for that
execution.

## Execution namespace prerequisites

Execution pods depend on the `restricted` Pod Security Admission labels of
their namespace.

- `createExecutionNamespace: true` (default): the chart creates
  `executionNamespace` with `pod-security.kubernetes.io/{enforce,audit,warn}=restricted`.
- `createExecutionNamespace: false`: the chart cannot label a namespace it
  does not own. Label it yourself, then set
  `svc-runworker.executionNamespacePodSecurityVerified=true`. Rendering fails
  without it.
- `executionNamespace` must differ from the release namespace; rendering fails
  otherwise.

```bash
kubectl label namespace elder-runs \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/audit=restricted \
  pod-security.kubernetes.io/warn=restricted
```

## Enabling the worker in an environment overlay

`alpha.yml`, `values-beta.yaml` and `do-beta.yml` do not enable the worker.
To enable it with any of them the operator must set:

| Value | Why |
|---|---|
| `svc-runworker.enabled=true` | Renders the worker |
| `svc-runworker.internalTokenSecret.name` | Secret with the onboarded worker token; rendering fails without it |
| A `kata` RuntimeClass on the cluster (or another listed class in `runtimeClassName`) | Execution pods are created with it; without the RuntimeClass they are rejected by the API server |
| `networkPolicy.egressFQDNs` | Only if the worker must reach hosts other than hub-api |

## Worker tenant scope

Run workers are platform-wide: the internal claim endpoints accept only
workers with no tenant (`tenant_id` null). A worker token therefore carries
access to every tenant's run queue; store it only in a Secret and rotate it
(`POST /api/v1/admin/workers/<id>/rotate`) if exposure is suspected. Per-tenant
worker scoping is planned work.

Unusual claims are flagged with the structured warning `run_claim_anomaly`
and the counter `elder.runs.claim_anomalies` (`reason` = `new_tenant` when a
worker claims for a tenant it has not served before, `rate_spike` when a
worker exceeds `RUNS_CLAIM_RATE_WARN_THRESHOLD` claims per 60 s, default 120).
A new worker pod name produces one `new_tenant` warning per tenant after a
rollout; alert on warnings outside rollouts.

## Upgrading from earlier 4.0 builds

1. **Onboard the worker** (`POST /api/v1/admin/workers`, type `runworker`) and
   store the one-time token in a Secret; set
   `svc-runworker.internalTokenSecret.name`. The in-cluster signing-key
   authentication path was removed.
2. **Default backend changed from `auto` to `k8s_job`.** Executions that
   previously ran inside the worker pod now run as Jobs. The cluster needs the
   `kata` RuntimeClass (or set `runtimeClassName`), and the runtime images must be
   pullable from the execution namespace.
   `runtimeClassName` must be one of `sandboxedRuntimeClasses`.
3. **Network policy:** the worker's open HTTPS egress rule is gone. List
   required hosts in `networkPolicy.egressFQDNs`. The
   `elder-runs-api-access` policy is replaced by `elder-runs-gateway-access`
   (pod label `elder.penguintech.io/gateway-access`).
4. Remove `JWT_SECRET_KEY` / `SECRET_KEY` from any out-of-chart worker
   deployment (Docker, remote cluster); the worker will not start with them.

Verify a rendered chart with `make test-helm`.
