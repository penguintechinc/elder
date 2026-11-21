# Build stage (expects repository root `.` as Docker build context)
FROM rust:1.98-slim-bookworm@sha256:ff521445a372125ed4f76e1453a1f8098f2d05332d1601d30db1c1f62757e730 AS builder

WORKDIR /build

COPY Cargo.toml Cargo.lock ./
COPY crates crates
COPY services services
COPY cli cli

RUN cargo build --release --locked -p svc-scanner

# Runtime stage
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587

# Exact apt patch versions are omitted because Debian security updates rotate them.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    chromium \
    git \
    masscan \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 elder \
    && useradd -m -u 1000 -g 1000 -s /usr/sbin/nologin elder \
    && mkdir -p /app /tmp/screenshots \
    && chown -R 1000:1000 /app /tmp/screenshots

WORKDIR /app

COPY --from=builder --chown=1000:1000 /build/target/release/svc-scanner /app/svc-scanner

# Runs unprivileged as UID 1000 with read-only rootfs and all capabilities
# dropped (compatible with Kubernetes PodSecurity Admission `restricted`).
USER 1000:1000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/app/svc-scanner", "healthcheck"]

ENTRYPOINT ["/app/svc-scanner"]
CMD ["run", "--once"]
