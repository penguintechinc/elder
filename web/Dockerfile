# Build stage
FROM node:20-slim AS builder

# GitHub token for private package access — declared early so validation
# runs before any expensive steps, but DOES NOT bust the npm install cache
ARG GITHUB_TOKEN
RUN test -n "$GITHUB_TOKEN" || (echo "ERROR: GITHUB_TOKEN build arg is required" && exit 1)

WORKDIR /app

# Copy lockfile + package.json + .npmrc first so npm ci is cached independently
# of source changes and build-time vars (VITE_VERSION, VITE_BUILD_TIME)
COPY web/package.json web/package-lock.json web/.npmrc ./

# Configure npm authentication for GitHub Packages
# Token is passed as build arg and not persisted in image layers
RUN echo "//npm.pkg.github.com/:_authToken=${GITHUB_TOKEN}" >> .npmrc

# Install dependencies — this layer is cached as long as package-lock.json
# and the token haven't changed.
# Note: npm install (not --ignore-scripts) is required so that native modules
# (esbuild, lightningcss) correctly resolve platform-specific optional deps.
# See: https://github.com/npm/cli/issues/4828
RUN npm install && \
    rm -f .npmrc

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
FROM node:20-slim AS runner
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN npm install -g serve
WORKDIR /app
COPY --from=builder /app/dist ./dist
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:3000/ || exit 1
CMD ["serve", "-s", "dist", "-l", "3000"]
