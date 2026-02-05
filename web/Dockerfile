# Build stage
FROM node:18-slim AS builder

# Build arguments for Vite
# VITE_API_URL defaults to empty string - nginx proxies /api/* to API server
# Only set VITE_API_URL for development or when API is on a different domain
ARG VITE_API_URL=
ARG VITE_BUILD_TIME
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_BUILD_TIME=$VITE_BUILD_TIME

WORKDIR /app

# Copy package.json only (not lock file) for multi-arch compatibility
# This works around npm bug https://github.com/npm/cli/issues/4828 where
# optional platform-specific packages (rollup, esbuild, lightningcss) fail
# when package-lock.json was generated on a different architecture
COPY web/package.json ./

# Install dependencies fresh for current architecture
RUN npm install --ignore-scripts && \
    npm rebuild esbuild

# Copy pre-built shared react_libs (migrated to penguin-libs)
# The dist folder contains the built components from penguin-libs
COPY shared/react_libs ../shared/react_libs

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
