# Elder Scanner Service Dockerfile
# Multi-stage: gcc + ldap headers in builder only; runtime has neither.

# Build stage — compiler tools needed for python-ldap C extension
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libldap2-dev \
    libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*

COPY apps/scanner/requirements.txt .
# Install to /usr/local so site-packages can be trivially copied to runtime stage
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

# Runtime stage — no compiler; ldap runtime shared libs only
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

ARG SCANNER_POLL_INTERVAL=300

RUN apt-get update && apt-get install -y --no-install-recommends \
    masscan \
    libldap-2.5-0 \
    libsasl2-2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy compiled packages (including python-ldap .so files) into system Python path
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Download Chromium browser binary and system deps (playwright CLI already copied above)
RUN playwright install chromium && playwright install-deps chromium

COPY apps/scanner/ .
COPY shared /app/shared

RUN useradd --create-home --shell /bin/bash --uid 1000 appuser && \
    mkdir -p /app/screenshots && \
    chown -R appuser:appuser /app

# ROOT CAPABILITY EXCEPTION (approved): masscan requires CAP_NET_RAW for raw socket
# access (SYN scanning). Process runs as UID 1000 (non-root) with only NET_RAW
# capability granted via Kubernetes securityContext. See k8s/helm/elder/values.yaml.
USER appuser

ENV SCANNER_POLL_INTERVAL=${SCANNER_POLL_INTERVAL}
ENV ELDER_API_URL=http://api:5000
ENV SCREENSHOT_DIR=/app/screenshots
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PLAYWRIGHT_CHROMIUM_ARGS=--no-sandbox

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python healthcheck.py || exit 1

CMD ["python", "main.py"]
