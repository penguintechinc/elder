# Elder Scanner Service Dockerfile
FROM python:3.13-slim-bookworm@sha256:01f42367a0a94ad4bc17111776fd66e3500c1d87c15bbd6055b7371d39c124fb

# Build arguments
ARG SCANNER_POLL_INTERVAL=300

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    masscan \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and browsers
RUN pip install playwright && \
    playwright install chromium && \
    playwright install-deps chromium

# Copy application code
COPY . .

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser

# Create screenshots directory and set ownership
RUN mkdir -p /app/screenshots && \
    chown -R appuser:appuser /app

# ROOT CAPABILITY EXCEPTION (approved): masscan requires CAP_NET_RAW for raw socket
# access (SYN scanning). Process runs as UID 1000 (non-root) with only NET_RAW
# capability granted via Kubernetes securityContext. See k8s/helm/elder/values.yaml.
USER appuser

# Environment variables
ENV SCANNER_POLL_INTERVAL=${SCANNER_POLL_INTERVAL}
ENV ELDER_API_URL=http://api:5000
ENV SCREENSHOT_DIR=/app/screenshots
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PLAYWRIGHT_CHROMIUM_ARGS=--no-sandbox

# Health check - verifies imports, API config, and writability
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python healthcheck.py || exit 1

# Run the scanner
CMD ["python", "main.py"]
