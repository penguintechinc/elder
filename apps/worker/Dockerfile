# Elder Worker Service Dockerfile
# Multi-stage build for optimal image size

# Build stage
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libldap2-dev \
    libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY apps/worker/requirements.txt /app/requirements.txt

# Install Python dependencies
RUN pip3 install --no-cache-dir --user -r requirements.txt

# Runtime stage
FROM python:3.12-slim

WORKDIR /app

# Install runtime dependencies and create user
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libldap2 \
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

# Fix permissions for non-root user
RUN chown -R worker:worker /app

# Set Python path and include site-packages
ENV PATH=/app/.local/bin:$PATH
ENV PYTHONPATH=/app:/app/.local/lib/python3.12/site-packages:$PYTHONPATH

# Switch to non-root user
USER worker

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${HEALTH_CHECK_PORT:-8000}/healthz || exit 1

# Run the connector service
CMD ["python3", "apps/worker/main.py"]
