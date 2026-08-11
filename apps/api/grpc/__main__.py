"""Entry point for running gRPC server as a module."""

# flake8: noqa: E501

import os

from apps.api.grpc.server import serve

if __name__ == "__main__":
    # Get configuration from environment
    host = os.getenv("GRPC_HOST", "0.0.0.0")  # nosec B104
    port = int(os.getenv("GRPC_PORT", "50051"))
    max_workers = int(os.getenv("GRPC_MAX_WORKERS", "10"))
    require_license = os.getenv("GRPC_REQUIRE_LICENSE", "true").lower() == "true"

    serve(
        host=host, port=port, max_workers=max_workers, require_license=require_license
    )
