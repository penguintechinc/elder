# Build stage
FROM --platform=$BUILDPLATFORM node:24-bookworm-slim@sha256:b506e7321f176aae77317f99d67a24b272c1f09f1d10f1761f2773447d8da26c AS builder

WORKDIR /app

# Copy lockfile + package.json + .npmrc first so npm install is cached independently
# of source changes and build-time vars (VITE_VERSION, VITE_BUILD_TIME)
COPY web/package.json web/package-lock.json web/.npmrc ./

# npm ci: strictly follows package-lock.json; never resolves beyond pinned versions.
# Optional deps (esbuild, lightningcss platform binaries) are still installed because
# npm ci respects optionalDependencies in the lockfile.
RUN npm ci

# Declare build-time Vite vars AFTER npm install so they don't bust that cache.
# Changing VITE_VERSION or VITE_BUILD_TIME only invalidates from here onward.
ARG VITE_API_URL=
ARG VITE_VERSION=0.0.0
ARG VITE_BUILD_TIME
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_VERSION=$VITE_VERSION
ENV VITE_BUILD_TIME=$VITE_BUILD_TIME

# Copy source code
COPY web/ .

# Build the application
RUN npm run build

# Production stage — serve built files via a rootless nginx reverse proxy.
# This is what makes login work from ANY browser, not just one on the
# cluster host: the SPA calls a RELATIVE /api/v1 base (see
# src/lib/api.ts — VITE_API_URL built empty), so the browser only ever
# talks to this container's own origin; nginx same-origin-proxies /api/*
# server-side to the elder-api Service (see nginx.conf). No more separate
# NodePort origin for the API that only resolves from the cluster host.
#
# nginxinc/nginx-unprivileged already runs as non-root (uid 101 "nginx")
# and listens on 8080 (not the privileged port 80) — no useradd juggling
# needed, unlike the old node-based `serve` runtime.
FROM nginxinc/nginx-unprivileged:1.27-bookworm@sha256:f9dfa9c20b2b0b7c5cc830374f22f23dee3f750b6c5291ca7e0330b5c88e6403 AS runner

# All of this image's writable paths (nginx pid, proxy/client/fastcgi temp
# dirs) live under /tmp per its baked-in nginx.conf — which the Helm chart
# already mounts as an emptyDir for the web component — so no extra
# chown/volume wiring is needed even under readOnlyRootFilesystem: true.
COPY --from=builder /app/dist /usr/share/nginx/html
COPY web/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

# No curl/wget/node in this image to build a native HEALTHCHECK around;
# k8s liveness/readiness httpGet probes (deployment.yaml) cover this
# instead — see devops-containers.md (native binary only, no curl/wget).
CMD ["nginx", "-g", "daemon off;"]
