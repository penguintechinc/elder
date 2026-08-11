# Elder Flows Invoker Service Dockerfile
# Multi-stage build for optimal image size.
#
# This service executes user-defined CI/CD stage commands inside a hardened,
# process-level sandbox (see sandbox.py) — it is intentionally an isolated
# image with a minimal dependency surface, separate from the consolidated
# worker's connector SDKs (no boto3/google-cloud/ldap3 here).

# Build stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb AS builder

WORKDIR /app

# Copy requirements
COPY apps/flows_invoker/requirements.txt /app/requirements.txt

# Install Python dependencies
RUN pip3 install --no-cache-dir --user --require-hashes -r requirements.txt

# Runtime stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

WORKDIR /app

# Install runtime dependencies and create user.
# git: required to clone/merge/push user repos ahead of sandboxed stage execution.
# ca-certificates: required for TLS-verified git-over-https and outbound calls.
# Versions pinned to the candidates resolved from the base image digest above;
# bump alongside a base image digest update.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git=1:2.39.5-0+deb12u3 \
    ca-certificates=20250419~deb12u1 \
    && rm -rf /var/lib/apt/lists/* && \
    useradd -m -u 1000 appuser

# Copy Python packages from builder to a location the appuser can access
RUN mkdir -p /app/.local
COPY --from=builder /root/.local /app/.local

# Copy application code
COPY apps/flows_invoker /app/apps/flows_invoker
COPY apps/worker/__init__.py /app/apps/worker/__init__.py
COPY apps/worker/config /app/apps/worker/config
COPY shared /app/shared

# Writable sandbox workspace — git clones + stage execution happen here.
# readOnlyRootFilesystem is enforced in K8s; this is backed by an emptyDir
# volume mounted at runtime (see k8s manifests).
# Fix permissions for non-root user in the same layer.
RUN mkdir -p /workspace && chown -R appuser:appuser /app /workspace

# Set Python path and include site-packages
ENV PATH=/app/.local/bin:$PATH
ENV PYTHONPATH=/app:/app/.local/lib/python3.13/site-packages:$PYTHONPATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV FLOWS_WORKSPACE_ROOT=/workspace

VOLUME ["/workspace"]

# Switch to non-root user
USER 1000

# Health check (native Python, no HTTP server in this service)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python3", "apps/flows_invoker/healthcheck.py"]

# Run the flows invoker service (Redis Streams consumer)
CMD ["python3", "-m", "apps.flows_invoker.main"]
