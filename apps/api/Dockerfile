# Elder Flask API Dockerfile
# Multi-stage build with debian-slim base for optimal size and security
# Python 3.12 for performance optimizations (dataclasses with slots, asyncio TaskGroups)

# Builder stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb AS builder

# Set working directory
WORKDIR /build

# Install build dependencies (versions pinned to the candidates resolved from
# the base image digest above; bump alongside a base image digest update)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc=4:12.2.0-3 \
    g++=4:12.2.0-3 \
    libpq-dev=15.18-0+deb12u1 \
    libsasl2-dev=2.1.28+dfsg-10 \
    libldap2-dev=2.5.13+dfsg-5 \
    libssl-dev=3.0.20-1~deb12u2 \
    pkg-config=1.8.1-1 \
    libxml2-dev=2.9.14+dfsg-1.3~deb12u6 \
    libxmlsec1-dev=1.2.37-2 \
    libxmlsec1-openssl=1.2.37-2 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies (includes penguin-libs and penguin-licensing)
RUN pip install --user --no-cache-dir --require-hashes -r requirements.txt

# Runtime stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

# Build arguments for JWT token expiration.
# False positive: names contain the substring "TOKEN" but the values are
# plain integer durations (hours/days), never a credential or secret value.
# hadolint ignore=DL3064
ARG JWT_ACCESS_TOKEN_HOURS=4
# hadolint ignore=DL3064
ARG JWT_REFRESH_TOKEN_DAYS=30

# Build argument for application version
ARG APP_VERSION=0.0.0

# Comment: Updated to Python 3.13 from 3.12

# Set working directory
WORKDIR /app

# Install runtime dependencies only (versions pinned to the candidates
# resolved from the base image digest above; bump alongside a digest update)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5=15.18-0+deb12u1 \
    libsasl2-2=2.1.28+dfsg-10 \
    libldap-2.5-0=2.5.13+dfsg-5 \
    libxml2=2.9.14+dfsg-1.3~deb12u6 \
    libxmlsec1-openssl=1.2.37-2 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd -m -u 1000 elder

# Copy Python dependencies from builder to elder user
COPY --from=builder /root/.local /home/elder/.local
RUN chown -R elder:elder /home/elder/.local

# Create instance directory for PyDAL migrations (before switching to elder user)
RUN mkdir -p /app/instance && chown -R elder:elder /app/instance

# Switch to non-root user (numeric UID: resolvable even without /etc/passwd)
USER 1000

# Make sure scripts in .local are usable
ENV PATH=/home/elder/.local/bin:$PATH

# Copy application code
COPY --chown=elder:elder apps /app/apps
COPY --chown=elder:elder shared /app/shared
COPY --chown=elder:elder requirements.txt /app/
COPY --chown=elder:elder alembic.ini /app/
COPY --chown=elder:elder alembic /app/alembic

# Expose ports
EXPOSE 5000 50051

# Health check (native Python). urlopen() raises on failure, which already
# yields a non-zero exit code, so no separate `|| exit 1` fallback is needed.
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5000/healthz')"]

# Set environment variables
ENV FLASK_APP=apps.api.main:create_app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# False positive: names contain the substring "TOKEN" but the values are
# plain integer durations (hours/days), never a credential or secret value.
# hadolint ignore=DL3064
ENV JWT_ACCESS_TOKEN_HOURS=${JWT_ACCESS_TOKEN_HOURS}
# hadolint ignore=DL3064
ENV JWT_REFRESH_TOKEN_DAYS=${JWT_REFRESH_TOKEN_DAYS}
ENV APP_VERSION=${APP_VERSION}

# Run unified startup script that launches both Flask and gRPC servers
CMD ["python3", "-m", "apps.api.start"]
