# Elder Runs Worker Service (svc-runworker) Dockerfile
# Multi-stage rootless build — completely decoupled from Elder's DB/Redis
# so it can run inside Elder's cluster or in a remote Kubernetes cluster.

# Build stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb AS builder

WORKDIR /app

# Copy hash-pinned requirements
COPY apps/runworker/requirements.txt /app/requirements.txt

# Install Python dependencies
RUN pip3 install --no-cache-dir --user --require-hashes -r requirements.txt

# Runtime stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

WORKDIR /app

# Install runtime CA certificates and create non-root user
# Exact apt patch versions are removed — they are dropped from Debian repos after security updates, breaking builds.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* && \
    useradd -m -u 1000 appuser

# Copy Python packages from builder to a location appuser can read
RUN mkdir -p /app/.local
COPY --from=builder /root/.local /app/.local

# Copy application code and version metadata
COPY apps/runworker /app/apps/runworker
COPY .version /app/.version

# Optional build-time version override (falls back to /app/.version at runtime)
ARG APP_VERSION=
ENV APP_VERSION=${APP_VERSION}

# Fix permissions for non-root user
RUN chown -R appuser:appuser /app

# Set Python path and runtime flags
ENV PATH=/app/.local/bin:$PATH
ENV PYTHONPATH=/app:/app/.local/lib/python3.13/site-packages:$PYTHONPATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user (appuser, UID 1000)
USER 1000

# Health check (native Python stdlib, no curl/wget)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python3", "apps/runworker/healthcheck.py"]

# Run the isolated runs worker poll loop
CMD ["python3", "-m", "apps.runworker.main"]
