# Elder Worker Service Dockerfile
# Multi-stage build for optimal image size

# Build stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb AS builder

WORKDIR /app

# Install build dependencies
# Exact apt patch versions are removed — they are dropped from Debian repos after security updates, breaking builds.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libldap2-dev \
    libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY apps/worker/requirements.txt /app/requirements.txt

# Install Python dependencies
RUN pip3 install --no-cache-dir --user --require-hashes -r requirements.txt

# Runtime stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

WORKDIR /app

# Install runtime dependencies and create user
# Exact apt patch versions are removed — they are dropped from Debian repos after security updates, breaking builds.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    libldap-2.5-0 \
    libsasl2-2 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* && \
    useradd -m -u 1000 worker

# Copy Python packages from builder to a location the worker user can access
RUN mkdir -p /app/.local
COPY --from=builder /root/.local /app/.local

# Copy application code
COPY apps/worker /app/apps/worker
COPY shared /app/shared
# The worker resolves its job-bus consumer groups from the module registry
# (apps.api.modules.registry). Without this the JobBus fails to initialise with
# ModuleNotFoundError: No module named 'apps.api'.
COPY apps/api /app/apps/api

# Fix permissions for non-root user
RUN chown -R worker:worker /app

# Set Python path and include site-packages
ENV PATH=/app/.local/bin:$PATH
ENV PYTHONPATH=/app:/app/.local/lib/python3.13/site-packages:$PYTHONPATH

# Switch to non-root user
USER 1000

# Health check (native Python). JSON exec form is used per policy, so the
# ${HEALTH_CHECK_PORT:-8000} shell-default substitution is preserved by
# explicitly invoking a shell as the JSON command rather than relying on
# Docker's (non-shell) exec semantics for the whole CMD.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["sh", "-c", "python3 -c \"import urllib.request; urllib.request.urlopen('http://localhost:${HEALTH_CHECK_PORT:-8000}/healthz')\""]

# Run the connector service
CMD ["python3", "apps/worker/main.py"]
