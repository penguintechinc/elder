# Elder chart: secrets, hardening and DB pool sizing

## Secrets (issue #385)

The chart never renders a credential as a literal env value. Every signing
key, password, token and credential-bearing URL is injected with
`valueFrom.secretKeyRef` from one Secret named by `secrets.existingSecret`
(default `elder-secrets`). Rendering **fails** when:

- `secrets.existingSecret` is empty (and `secrets.create` is false);
- `secrets.create: true` is used outside `environment: dev|development|local|alpha`;
- any component `env` map holds a credential literal (name contains
  SECRET/PASSWORD/TOKEN/API_KEY/LICENSE_KEY/ACCESS_KEY/…, or the value is a URL
  with embedded credentials) or duplicates a `secretEnv` entry.

Which env var maps to which Secret key is declared per component in
`<component>.secretEnv` (`ENV: { key: KEY, optional: bool }`).

| Key | Required | Consumers |
|-----|----------|-----------|
| `SECRET_KEY` | yes | hub-api |
| `JWT_SECRET_KEY` | yes | hub-api, svc-scanner, svc-integrations (must match) |
| `POSTGRES_PASSWORD` | yes | postgres |
| `DB_PASSWORD` | yes | hub-api |
| `DATABASE_URL` | yes (optional for hub-api) | svc-worker, svc-flows-invoker, hub-api |
| `REDIS_URL` | yes | hub-api, svc-worker, svc-flows-invoker |
| `ADMIN_PASSWORD` | yes | hub-api (bootstrap admin) |
| `LICENSE_KEY`, `REDIS_PASSWORD`, `DATABASE_READ_URL`, `ELDER_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `LDAP_BIND_PASSWORD`, `GUEST_PASSWORD` | no | see `values.yaml` |

A required key missing from the Secret keeps the pod in
`CreateContainerConfigError` (fail closed) rather than starting with a default.

### Operator steps (beta / gamma / production)

Create the Secret **before** `helm upgrade` (or manage it with
SealedSecrets/ExternalSecrets), using the existing values so data and sessions
keep working:

```bash
kubectl -n elder create secret generic elder-secrets \
  --from-literal=SECRET_KEY=... --from-literal=JWT_SECRET_KEY=... \
  --from-literal=POSTGRES_PASSWORD=... --from-literal=DB_PASSWORD=... \
  --from-literal=DATABASE_URL=postgresql://elder:...@elder-postgres:5432/elder \
  --from-literal=REDIS_URL=redis://elder-redis:6379/0 \
  --from-literal=ADMIN_PASSWORD=... [--from-literal=LICENSE_KEY=...]
```

- Previously the scanner/integrations CronJobs fell back to hub-api
  `SECRET_KEY`; set `JWT_SECRET_KEY` to the value hub-api already used for JWT
  signing (its `SECRET_KEY` if `JWT_SECRET_KEY` was never set) to keep
  existing tokens valid.
- `ADMIN_PASSWORD` is now required: the app otherwise falls back to a
  built-in default.
- Values passed with `--set <component>.env.<CREDENTIAL>=...` now fail the
  render; move them into the Secret.

### Local dev / alpha

`alpha.yml` sets `environment: alpha` and `secrets.create: true`; the chart
renders a dev-only `elder-secrets` Secret from `secrets.devValues` and pods
roll automatically when it changes (`checksum/secrets`).
`scripts/deploy-alpha.sh` needs no change.

## Pod hardening

Every pod gets `seccompProfile: RuntimeDefault` and `runAsNonRoot`; every
container gets `runAsNonRoot`, `allowPrivilegeEscalation: false`,
`capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault` and
`readOnlyRootFilesystem: true` (writable paths use `emptyDir`). These are
enforced by `elder.containerSecurityContext`; component overrides can change
the UID or opt out of `readOnlyRootFilesystem`, but cannot re-enable privilege
escalation or add capabilities.

Image tags default to the chart `appVersion` when empty; `latest` is rejected
everywhere and `*-latest` outside dev/alpha.

## Database connection pools

`<component>.database.poolSize` / `maxOverflow` render `DB_POOL_SIZE` (and
`SQLALCHEMY_MAX_OVERFLOW` for hub-api). The bundled Postgres runs with
`-c max_connections=<postgres.maxConnections>` (default 200). The chart fails
to render when `sum((poolSize + maxOverflow) x max pods)` (HPA `maxReplicas`
when autoscaling is enabled) exceeds
`postgres.maxConnections - postgres.reservedConnections`.

Defaults: hub-api 10+5 per pod, svc-worker 5, svc-flows-invoker 5. When using
an external Postgres, size these against that server's `max_connections`.
