# Build stage
FROM node:20-slim AS builder

# Build arguments for Vite
# VITE_API_URL defaults to empty string - nginx proxies /api/* to API server
# Only set VITE_API_URL for development or when API is on a different domain
ARG VITE_API_URL=
ARG VITE_VERSION=0.0.0
ARG VITE_BUILD_TIME
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_VERSION=$VITE_VERSION
ENV VITE_BUILD_TIME=$VITE_BUILD_TIME

# GitHub token for private package access
ARG GITHUB_TOKEN
RUN test -n "$GITHUB_TOKEN" || (echo "ERROR: GITHUB_TOKEN build arg is required" && exit 1)

WORKDIR /app

# Copy package.json and .npmrc for dependency installation
COPY web/package.json web/.npmrc ./

# Configure npm authentication for GitHub Packages
# Token is passed as build arg and not persisted in image layers
RUN echo "//npm.pkg.github.com/:_authToken=${GITHUB_TOKEN}" >> .npmrc

# Install dependencies fresh for current architecture
# @penguintechinc/react-libs installed from GitHub Packages
# Note: npm install (not --ignore-scripts) is required so that native modules
# (esbuild, lightningcss) correctly resolve platform-specific optional deps.
# See: https://github.com/npm/cli/issues/4828
RUN npm install && \
    rm -f .npmrc

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
