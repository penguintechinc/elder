"""gRPC server for Elder application."""

# flake8: noqa: E501


import os
import signal
import sys
import time
from concurrent import futures

import grpc
import structlog
from grpc_reflection.v1alpha import reflection

from apps.api.grpc.generated import elder_pb2, elder_pb2_grpc
from apps.api.grpc.servicers.elder_servicer import ElderServicer
from apps.api.licensing_fallback import get_license_client

logger = structlog.get_logger(__name__)


def serve(
    host: str = "0.0.0.0",
    port: int = 50051,
    max_workers: int = 10,
    require_license: bool = True,
):
    """
    Start the gRPC server.

    Args:
        host: Host to bind to
        port: Port to bind to
        max_workers: Maximum number of worker threads
        require_license: Whether to require enterprise license
    """
    # Validate license if required
    if require_license:
        if get_license_client is None:
            logger.warning(
                "grpc_server_no_licensing_module",
                message="penguin_licensing not available, skipping license check"
            )
        else:
            try:
                license_client = get_license_client()
                validation = license_client.validate()

                if not validation.get("valid"):
                    logger.error(
                        "grpc_server_license_invalid", message=validation.get("message")
                    )
                    sys.exit(1)

                if validation.get("tier") != "enterprise":
                    logger.error(
                        "grpc_server_requires_enterprise", tier=validation.get("tier")
                    )
                    sys.exit(1)

                logger.info(
                    "grpc_server_license_validated",
                    tier=validation.get("tier"),
                    customer=validation.get("customer"),
                )
            except Exception as e:
                logger.error("grpc_server_license_error", error=str(e))
                sys.exit(1)

    # Create gRPC server
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        options=[
            ("grpc.max_send_message_length", 100 * 1024 * 1024),  # 100MB
            ("grpc.max_receive_message_length", 100 * 1024 * 1024),  # 100MB
            ("grpc.keepalive_time_ms", 30000),  # 30 seconds
            ("grpc.keepalive_timeout_ms", 10000),  # 10 seconds
            ("grpc.http2.max_pings_without_data", 0),
            ("grpc.http2.min_time_between_pings_ms", 10000),
        ],
    )

    # Add servicer to server
    elder_pb2_grpc.add_ElderServiceServicer_to_server(ElderServicer(), server)

    # Enable reflection for grpcurl/grpcui
    service_names = (
        elder_pb2.DESCRIPTOR.services_by_name["ElderService"].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)

    # Bind to address
    server_address = f"{host}:{port}"
    server.add_insecure_port(server_address)

    # Start server
    server.start()
    logger.info(
        "grpc_server_started",
        address=server_address,
        max_workers=max_workers,
        enterprise_only=require_license,
    )

    # Graceful shutdown handler
    def shutdown_handler(signum, frame):
        logger.info("grpc_server_shutting_down", signal=signum)
        server.stop(grace=5)
        logger.info("grpc_server_stopped")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Keep server running
    try:
        while True:
            time.sleep(86400)  # Sleep for a day
    except KeyboardInterrupt:
        server.stop(0)


if __name__ == "__main__":
    # Get configuration from environment
    host = os.getenv("GRPC_HOST", "0.0.0.0")
    port = int(os.getenv("GRPC_PORT", "50051"))
    max_workers = int(os.getenv("GRPC_MAX_WORKERS", "10"))
    require_license = os.getenv("GRPC_REQUIRE_LICENSE", "true").lower() == "true"

    serve(
        host=host, port=port, max_workers=max_workers, require_license=require_license
    )
