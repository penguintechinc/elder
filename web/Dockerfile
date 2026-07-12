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

# Production stage — serve built files with a lightweight static server
FROM node:24-bookworm-slim@sha256:b506e7321f176aae77317f99d67a24b272c1f09f1d10f1761f2773447d8da26c AS runner
RUN npm install -g serve
# The node image already ships a non-root `node` user at uid 1000 — creating
# another user at that uid fails (useradd exit 4, "UID already in use") and
# broke every multi-arch build. Reuse it; rootless requirement is satisfied.
WORKDIR /app
COPY --chown=node:node --from=builder /app/dist ./dist
RUN chown -R node:node /app
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD node -e "require('http').get('http://localhost:3000/', r => process.exit(r.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"
USER node
CMD ["serve", "-s", "dist", "-l", "3000"]
