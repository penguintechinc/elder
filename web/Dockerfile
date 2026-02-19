# Build stage
FROM node:18-slim AS builder

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

# Production stage
FROM nginx:stable-bookworm

# Copy built files
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy nginx configuration
COPY web/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# Health check - verify nginx is serving content
# Use 127.0.0.1 (not localhost) to avoid IPv6 resolution issues
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
