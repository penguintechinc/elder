# Build stage (expects repository root `.` as Docker build context)
FROM rust:1.98-slim-bookworm@sha256:ff521445a372125ed4f76e1453a1f8098f2d05332d1601d30db1c1f62757e730 AS builder

WORKDIR /build

COPY Cargo.toml Cargo.lock ./
COPY crates crates
COPY services services

RUN cargo build --release --locked -p svc-integrations

# Runtime stage
FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587

# Exact apt patch versions are omitted because Debian security updates rotate them.
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 elder \
    && useradd -m -u 1000 -g 1000 -s /usr/sbin/nologin elder \
    && mkdir -p /app \
    && chown -R 1000:1000 /app

WORKDIR /app

COPY --from=builder --chown=1000:1000 /build/target/release/svc-integrations /app/svc-integrations

USER 1000:1000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/app/svc-integrations", "healthcheck"]

ENTRYPOINT ["/app/svc-integrations"]
CMD ["run", "--once"]
