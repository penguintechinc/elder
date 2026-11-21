"""
Centralized logging configuration for Elder API.

Provides secure logging that:
- Logs detailed errors to /var/log/elder.log
- Optionally ships logs to syslog server
- Never exposes stack traces to external users
- Follows OWASP security best practices
"""

# flake8: noqa: E501

import logging
import logging.handlers
import os
import sys
import traceback
from typing import Optional, Tuple

from quart import Quart, jsonify

# Default log file path
DEFAULT_LOG_FILE = "/var/log/elder.log"
FALLBACK_LOG_FILE = "/tmp/elder.log"  # nosec B108


def setup_logging(app: Quart) -> None:
    """
    Configure application logging with file and optional syslog handlers.

    Args:
        app: Flask application instance
    """
    log_level = getattr(logging, app.config.get("LOG_LEVEL", "INFO").upper())
    log_format = app.config.get("LOG_FORMAT", "text")

    # Create formatters
    if log_format == "json":
        formatter = logging.Formatter(
            '{"time":"%(asctime)s","level":"%(levelname)s","module":"%(module)s",'
            '"function":"%(funcName)s","line":%(lineno)d,"message":"%(message)s"}'
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - "
            "%(module)s:%(funcName)s:%(lineno)d - %(message)s"
        )

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear any existing handlers
    root_logger.handlers.clear()

    # Console handler (for container logs)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler - try /var/log/elder.log, fall back to /tmp/elder.log
    log_file = os.getenv("LOG_FILE", DEFAULT_LOG_FILE)
    try:
        # Create directory if it doesn't exist
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, mode=0o755, exist_ok=True)

        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,  # 10MB per file
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        app.logger.info(f"Logging to file: {log_file}")
    except (PermissionError, OSError) as e:
        # Fall back to /tmp if /var/log is not writable
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                FALLBACK_LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5
            )
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
            app.logger.warning(
                f"Could not write to {log_file}: {e}. Logging to {FALLBACK_LOG_FILE}"
            )
        except Exception as fallback_error:
            app.logger.error(
                f"Could not create file handler: {fallback_error}. "
                "Logging to console only."
            )

    # Syslog handler (optional)
    syslog_enabled = app.config.get("SYSLOG_ENABLED", False)
    syslog_server = os.getenv("SYSLOG_SERVER", app.config.get("SYSLOG_HOST"))
    syslog_port = int(os.getenv("SYSLOG_PORT", app.config.get("SYSLOG_PORT", 514)))

    if syslog_enabled and syslog_server:
        try:
            syslog_handler = logging.handlers.SysLogHandler(
                address=(syslog_server, syslog_port),
                facility=logging.handlers.SysLogHandler.LOG_USER,
            )
            syslog_handler.setLevel(logging.WARNING)
            syslog_handler.setFormatter(formatter)
            root_logger.addHandler(syslog_handler)
            app.logger.info(f"Syslog enabled: {syslog_server}:{syslog_port}")
        except Exception as e:
            app.logger.error(f"Could not connect to syslog server: {e}")

    # Set Quart app logger
    app.logger.handlers = root_logger.handlers
    app.logger.setLevel(log_level)

    app.logger.info(f"Logging initialized - Level: {log_level}")


def log_error_and_respond(
    logger: logging.Logger,
    error: Exception,
    message: str = "An internal error occurred",
    status_code: int = 500,
    include_error_type: bool = False,
) -> tuple[dict, int]:
    """
    Safely log an error and return a generic response to the user.

    This function follows OWASP best practices by:
    - Logging full error details (including stack trace) to the server logs
    - Returning only a generic error message to the user
    - Never exposing stack traces, file paths, or internal details to users

    Args:
        logger: Logger instance to use
        error: The exception that occurred
        message: Generic message to show to user
        status_code: HTTP status code to return
        include_error_type: If True, include error type in response (use with caution)

    Returns:
        Tuple of (response_dict, status_code)

    Example:
        try:
            do_something()
        except Exception as e:
            return log_error_and_respond(
                logger, e, "Failed to process request", 400
            )
    """
    # Log full error details to server logs
    logger.error(
        f"Error occurred: {type(error).__name__}: {str(error)}\n"
        f"Traceback:\n{traceback.format_exc()}"
    )

    # Build safe response for user
    response = {"error": message}

    if include_error_type:
        # Only include error type, not the message (which may contain sensitive data)
        response["error_type"] = type(error).__name__

    return jsonify(response), status_code


def safe_error_response(
    error: Exception,
    message: str = "An internal error occurred",
    status_code: int = 500,
    logger: logging.Logger | None = None,
) -> tuple[dict, int]:
    """
    Create a safe error response without exposing sensitive information.

    This is a convenience function that creates a logger if one isn't provided.

    Args:
        error: The exception that occurred
        message: Generic message to show to user
        status_code: HTTP status code to return
        logger: Optional logger instance (will create one if not provided)

    Returns:
        Tuple of (response_dict, status_code)
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    return log_error_and_respond(logger, error, message, status_code)
