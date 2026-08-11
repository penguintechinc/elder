# Elder Test Image - Reproducible Test Environment
# Combines API, worker, and test dependencies with Postgres pgvector support

# Builder stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb AS builder

WORKDIR /build

# Install build dependencies (same as apps/api/Dockerfile + pgvector build-time)
# Versions pinned to the candidates resolved from the base image digest above;
# bump alongside a base image digest update.
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

# Copy all requirements files
COPY requirements.txt .
COPY apps/worker/requirements.txt apps/worker/
COPY apps/scanner/requirements.txt apps/scanner/
COPY apps/mcp/requirements.txt apps/mcp/

# Install dependencies
# Step 1: root API requirements (includes test deps: pytest, pytest-cov, pytest-mock, pytest-asyncio)
# Step 2: worker requirements (aiocron, backoff)
# All hashes now verified (setuptools included with hash in lockfile)
RUN pip install --user --no-cache-dir --require-hashes -r requirements.txt \
    && pip install --user --no-cache-dir --require-hashes -r apps/worker/requirements.txt

# Runtime stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

WORKDIR /app

# Install runtime dependencies (git: flows-invoker gitops/sandbox tests).
# Versions pinned to the candidates resolved from the base image digest above;
# bump alongside a base image digest update.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5=15.18-0+deb12u1 \
    libsasl2-2=2.1.28+dfsg-10 \
    libldap-2.5-0=2.5.13+dfsg-5 \
    libxml2=2.9.14+dfsg-1.3~deb12u6 \
    libxmlsec1-openssl=1.2.37-2 \
    git=1:2.39.5-0+deb12u3 \
    ca-certificates=20250419~deb12u1 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root test user
RUN useradd -m -u 1000 tester

# Copy dependencies from builder
COPY --from=builder /root/.local /home/tester/.local
RUN chown -R tester:tester /home/tester/.local

# Copy application code (must be before USER tester)
COPY --chown=tester:tester . /app

# Create writable directories for test artifacts
RUN mkdir -p /app/instance /app/.pytest_cache /app/htmlcov /tmp/playwright-elder && \
    chown -R tester:tester /app /tmp/playwright-elder

USER 1000

# Make sure scripts in .local are usable
ENV PATH=/home/tester/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV FLASK_ENV=testing

# Health check (basic)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD ["python3", "-c", "import sys; sys.exit(0)"]

ENTRYPOINT ["python3", "-m", "pytest"]
