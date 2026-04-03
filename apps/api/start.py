"""Unified startup script for Flask REST API and gRPC server."""

import multiprocessing
import os
import signal
import sys
import time

import structlog

logger = structlog.get_logger(__name__)


def run_flask_server():
    """Run Flask REST API server using uvicorn."""
    import uvicorn

    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    workers = int(os.getenv("FLASK_WORKERS", "1"))

    logger.info(
        "starting_flask_server",
        host=host,
        port=port,
        workers=workers,
    )

    uvicorn.run(
        "apps.api.main:create_app",
        factory=True,
        host=host,
        port=port,
        workers=workers,
        loop="uvloop",
        http="h11",
        access_log=True,
        log_level="info",
    )


def run_grpc_server():
    """Run gRPC server if enabled."""
    from apps.api.grpc.server import serve

    grpc_enabled = os.getenv("GRPC_ENABLED", "false").lower() == "true"

    if not grpc_enabled:
        logger.info(
            "grpc_server_disabled", message="GRPC_ENABLED=false, skipping gRPC server"
        )
        # Keep process alive but idle
        while True:
            time.sleep(3600)
        return

    # Create a Flask app for the gRPC process so it shares the same DB setup
    # (same config, same init_db path, same schema reflection) as the Flask server.
    from apps.api.main import create_flask_app
    flask_app = create_flask_app()

    host = os.getenv("GRPC_HOST", "0.0.0.0")
    port = int(os.getenv("GRPC_PORT", "50051"))
    max_workers = int(os.getenv("GRPC_MAX_WORKERS", "10"))
    require_license = os.getenv("GRPC_REQUIRE_LICENSE", "true").lower() == "true"

    logger.info(
        "starting_grpc_server",
        host=host,
        port=port,
        max_workers=max_workers,
        require_license=require_license,
    )

    serve(
        app=flask_app,
        host=host,
        port=port,
        max_workers=max_workers,
        require_license=require_license,
    )


def main():
    """Start both Flask and gRPC servers in parallel processes."""
    # Use fork start method to ensure environment variables are inherited
    multiprocessing.set_start_method("fork", force=True)

    logger.info("starting_elder_api_services")

    # Create processes for both servers
    flask_process = multiprocessing.Process(
        target=run_flask_server, name="FlaskServer"
    )
    grpc_process = multiprocessing.Process(
        target=run_grpc_server, name="gRPCServer"
    )

    # Start both processes
    flask_process.start()
    grpc_process.start()

    logger.info(
        "services_started",
        flask_pid=flask_process.pid,
        grpc_pid=grpc_process.pid,
    )

    # Graceful shutdown handler
    def shutdown_handler(signum, frame):
        logger.info("shutting_down_services", signal=signum)

        if flask_process.is_alive():
            logger.info("terminating_flask_server")
            flask_process.terminate()
            flask_process.join(timeout=5)
            if flask_process.is_alive():
                flask_process.kill()

        if grpc_process.is_alive():
            logger.info("terminating_grpc_server")
            grpc_process.terminate()
            grpc_process.join(timeout=5)
            if grpc_process.is_alive():
                grpc_process.kill()

        logger.info("services_stopped")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Monitor processes and restart if needed
    try:
        while True:
            # Check if Flask process died
            if not flask_process.is_alive():
                exit_code = flask_process.exitcode
                logger.error("flask_server_died", exit_code=exit_code)
                sys.exit(1)

            # Check if gRPC process died (only if gRPC is enabled)
            grpc_enabled = os.getenv("GRPC_ENABLED", "false").lower() == "true"
            if grpc_enabled and not grpc_process.is_alive():
                exit_code = grpc_process.exitcode
                logger.error("grpc_server_died", exit_code=exit_code)
                sys.exit(1)

            time.sleep(5)
    except KeyboardInterrupt:
        shutdown_handler(signal.SIGINT, None)


if __name__ == "__main__":
    main()
