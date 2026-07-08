# Elder Test Image - Reproducible Test Environment
# Combines API, worker, and test dependencies with Postgres pgvector support

# Builder stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb AS builder

WORKDIR /build

# Install build dependencies (same as apps/api/Dockerfile + pgvector build-time)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    libsasl2-dev \
    libldap2-dev \
    libssl-dev \
    pkg-config \
    libxml2-dev \
    libxmlsec1-dev \
    libxmlsec1-openssl \
    && rm -rf /var/lib/apt/lists/*

# Copy all requirements files
COPY requirements.txt .
COPY apps/worker/requirements.txt apps/worker/
COPY apps/scanner/requirements.txt apps/scanner/
COPY apps/mcp/requirements.txt apps/mcp/

# Install dependencies
# Step 1: Install root API requirements (includes test deps: pytest, pytest-cov, pytest-mock, pytest-asyncio)
# Note: uv generated hashes but grpcio-tools transitive setuptools is unpinned.
# Workaround: strip hashes and install without --require-hashes (lockfile itself is pinned)
RUN sed 's/[[:space:]]*--hash=.*//' requirements.txt > requirements-nohash.txt && \
    pip install --user --no-cache-dir -r requirements-nohash.txt && \
    rm requirements-nohash.txt

# Step 2: Install worker requirements (aiocron, backoff)
# Same approach: strip hashes if present
RUN sed 's/[[:space:]]*--hash=.*//' apps/worker/requirements.txt > /tmp/worker-req-nohash.txt && \
    pip install --user --no-cache-dir -r /tmp/worker-req-nohash.txt && \
    rm /tmp/worker-req-nohash.txt

# Runtime stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libsasl2-2 \
    libldap-2.5-0 \
    libxml2 \
    libxmlsec1-openssl \
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

USER tester

# Make sure scripts in .local are usable
ENV PATH=/home/tester/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV FLASK_ENV=testing

# Health check (basic)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python3 -c "import sys; sys.exit(0)" || exit 1

ENTRYPOINT ["python3", "-m", "pytest"]
