# Elder Hosted MCP Service Dockerfile (hub-mcp)
# Build context: repository root (.)
# Multi-stage, non-root, read-only-rootfs safe, zero DB/Redis dependencies.

# Builder stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb AS builder

WORKDIR /app

COPY apps/mcp/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --user --require-hashes -r requirements.txt

# Runtime stage
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

ARG APP_VERSION=0.0.0

WORKDIR /app

RUN useradd -m -u 1000 appuser

COPY --from=builder /root/.local /app/.local
COPY apps/mcp /app/apps/mcp
COPY shared /app/shared
COPY openapi/v4.yaml /app/openapi/v4.yaml
COPY .version /app/.version

RUN chown -R appuser:appuser /app && chmod -R go-w /app

USER 1000

EXPOSE 8080

ENV PATH=/app/.local/bin:$PATH \
    PYTHONPATH=/app:/app/.local/lib/python3.13/site-packages \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8080 \
    OTEL_SERVICE_NAME=hub-mcp \
    ELDER_API_URL=http://hub-api:5000 \
    APP_VERSION=${APP_VERSION} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"]

CMD ["python3", "-m", "apps.mcp.start"]
