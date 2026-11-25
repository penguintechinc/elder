# Build stage
FROM --platform=$BUILDPLATFORM node:25-bookworm-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS builder

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

# Copy source code and .version for build metadata
COPY .version /tmp/.version
COPY web/ .

# Build the application (fall back to .version when VITE_VERSION / VITE_BUILD_TIME aren't passed)
RUN set -eu; \
    if [ -z "${VITE_VERSION:-}" ] || [ "${VITE_VERSION}" = "0.0.0" ]; then \
      if [ -f /tmp/.version ]; then \
        VITE_VERSION="$(tr -d '[:space:]' < /tmp/.version)"; \
      fi; \
    fi; \
    if [ -z "${VITE_BUILD_TIME:-}" ]; then \
      printf '%s' "${VITE_VERSION:-}" > /tmp/.ver; \
      EPOCH="$(awk -F. 'NF>=4 && $4 ~ /^[0-9]+$/ {print $4}' /tmp/.ver)"; \
      rm -f /tmp/.ver; \
      if [ -n "$EPOCH" ]; then \
        VITE_BUILD_TIME="$EPOCH"; \
      else \
        VITE_BUILD_TIME="$(date +%s)"; \
      fi; \
    fi; \
    export VITE_VERSION VITE_BUILD_TIME; \
    rm -f /tmp/.version; \
    npm run build

# Production stage — serve built SPA via rootless Express on Node 24 (PenguinTech standard).
# In-cluster, Envoy (Cilium Gateway API HTTPRoute) routes /api directly to hub-api
# and / to hub-webui. Express also proxies /api for local/port-forward access.
FROM node:25-bookworm-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV PORT=8080

COPY web/package.json web/package-lock.json web/.npmrc ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force

COPY --from=builder /app/dist ./dist
COPY web/server.ts ./server.ts

# Run as the built-in unprivileged `node` user (uid 1000, matching Helm securityContext.runAsUser: 1000).
USER 1000

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||8080)+'/livez').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]

CMD ["node", "server.ts"]
