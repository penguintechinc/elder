"""
Structured logging module for Elder application.

Supports multiple log destinations:
- Console output (always enabled)
- UDP syslog to remote servers (legacy)
- HTTP3/QUIC to Kafka clusters (high-performance)
- Cloud-native services (AWS CloudWatch, GCP Cloud Logging)
"""

# flake8: noqa: E501


import logging
import os
import socket
import sys
from datetime import datetime, timezone
from logging.handlers import SysLogHandler
from typing import List, Optional

import structlog

try:
    from penguintechinc_utils import sanitize_log_data as _sanitize_log_data

    def _sanitize_processor(
        logger: logging.Logger,
        method_name: str,
        event_dict: dict,
    ) -> dict:
        """Structlog processor that sanitizes sensitive data from all log events."""
        return _sanitize_log_data(event_dict)

    _HAS_SANITIZER = True
except ImportError:
    _HAS_SANITIZER = False


class KafkaHTTP3Handler(logging.Handler):
    """
    Custom handler for sending logs to Kafka via HTTP3/QUIC.
    Uses httpx with HTTP/3 support for high-performance log streaming.
    """

    def __init__(self, kafka_url: str, topic: str, api_key: Optional[str] = None):
        super().__init__()
        self.kafka_url = kafka_url
        self.topic = topic
        self.api_key = api_key
        self.session = None

        # Lazy import to avoid dependency when not using Kafka
        try:
            import httpx

            self.httpx = httpx
            self.session = httpx.Client(
                http2=True
            )  # HTTP/3 support requires specific build
        except ImportError:
            structlog.get_logger().warning(
                "httpx_not_available",
                message="httpx not installed, Kafka HTTP3 logging disabled",
            )

    def emit(self, record: logging.LogRecord):
        """Send log record to Kafka via HTTP3."""
        if not self.session:
            return

        try:
            log_entry = self.format(record)
            payload = {"topic": self.topic, "value": log_entry}

            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            self.session.post(
                self.kafka_url, json=payload, headers=headers, timeout=5.0
            )
        except Exception as e:
            # Don't let logging errors crash the application
            structlog.get_logger().warning(
                "kafka_logging_error", error=str(e), record=record.getMessage()
            )

    def close(self):
        """Close the HTTP session."""
        if self.session:
            self.session.close()
        super().close()


class CloudWatchHandler(logging.Handler):
    """
    Custom handler for AWS CloudWatch Logs.
    Uses HTTP3 for high-performance streaming.
    """

    def __init__(
        self,
        log_group: str,
        log_stream: str,
        region: str = "us-east-1",
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
    ):
        super().__init__()
        self.log_group = log_group
        self.log_stream = log_stream
        self.region = region

        try:
            import boto3

            if access_key and secret_key:
                self.client = boto3.client(
                    "logs",
                    region_name=region,
                    aws_access_key_id=access_key,
                    aws_secret_access_key=secret_key,
                )
            else:
                self.client = boto3.client("logs", region_name=region)

            # Create log group and stream if they don't exist
            self._ensure_log_stream()
        except ImportError:
            structlog.get_logger().warning(
                "boto3_not_available",
                message="boto3 not installed, CloudWatch logging disabled",
            )
            self.client = None

    def _ensure_log_stream(self):
        """Ensure log group and stream exist."""
        try:
            self.client.create_log_group(logGroupName=self.log_group)
        except self.client.exceptions.ResourceAlreadyExistsException:
            pass

        try:
            self.client.create_log_stream(
                logGroupName=self.log_group, logStreamName=self.log_stream
            )
        except self.client.exceptions.ResourceAlreadyExistsException:
            pass

    def emit(self, record: logging.LogRecord):
        """Send log record to CloudWatch."""
        if not self.client:
            return

        try:
            log_entry = {
                "logGroupName": self.log_group,
                "logStreamName": self.log_stream,
                "logEvents": [
                    {
                        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
                        "message": self.format(record),
                    }
                ],
            }
            self.client.put_log_events(**log_entry)
        except Exception as e:
            structlog.get_logger().warning(
                "cloudwatch_logging_error", error=str(e), record=record.getMessage()
            )


class StructuredLogger:
    """
    Structured logging configuration with multiple destinations.
    """

    def __init__(self, app_name: str = "elder", verbosity: int = 2):
        """
        Initialize structured logger.

        Args:
            app_name: Application name for log tagging
            verbosity: Log verbosity level (1=WARNING, 2=INFO, 3=DEBUG)
        """
        self.app_name = app_name
        self.verbosity = verbosity
        self.logger = None
        self.handlers: List[logging.Handler] = []

        # Configure based on verbosity
        self.log_level = self._get_log_level(verbosity)

    def _get_log_level(self, verbosity: int) -> int:
        """Map verbosity to log level."""
        levels = {1: logging.WARNING, 2: logging.INFO, 3: logging.DEBUG}
        return levels.get(verbosity, logging.INFO)

    def configure(
        self,
        enable_console: bool = True,
        enable_syslog: bool = False,
        syslog_host: Optional[str] = None,
        syslog_port: int = 514,
        enable_kafka: bool = False,
        kafka_url: Optional[str] = None,
        kafka_topic: str = "elder-logs",
        kafka_api_key: Optional[str] = None,
        enable_cloudwatch: bool = False,
        cloudwatch_log_group: Optional[str] = None,
        cloudwatch_log_stream: Optional[str] = None,
        cloudwatch_region: str = "us-east-1",
        enable_gcp: bool = False,
        gcp_project_id: Optional[str] = None,
        gcp_log_name: str = "elder",
    ) -> structlog.BoundLogger:
        """
        Configure structured logging with multiple destinations.

        Args:
            enable_console: Enable console logging (always recommended)
            enable_syslog: Enable UDP syslog logging
            syslog_host: Syslog server hostname
            syslog_port: Syslog server port
            enable_kafka: Enable HTTP3 Kafka logging
            kafka_url: Kafka REST API URL
            kafka_topic: Kafka topic name
            kafka_api_key: Kafka API key for authentication
            enable_cloudwatch: Enable AWS CloudWatch logging
            cloudwatch_log_group: CloudWatch log group name
            cloudwatch_log_stream: CloudWatch log stream name
            cloudwatch_region: AWS region
            enable_gcp: Enable GCP Cloud Logging
            gcp_project_id: GCP project ID
            gcp_log_name: GCP log name

        Returns:
            Configured structlog logger
        """
        # Build processor chain; insert sanitizer before final rendering
        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
        ]
        if _HAS_SANITIZER:
            processors.append(_sanitize_processor)
        processors.append(structlog.processors.JSONRenderer())

        # Configure structlog
        structlog.configure(
            processors=processors,
            wrapper_class=structlog.make_filtering_bound_logger(self.log_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )

        # Get root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(self.log_level)

        # Clear existing handlers
        root_logger.handlers = []

        # Console handler (always enabled)
        if enable_console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(self.log_level)
            console_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            root_logger.addHandler(console_handler)
            self.handlers.append(console_handler)

        # UDP Syslog handler
        if enable_syslog and syslog_host:
            try:
                syslog_handler = SysLogHandler(
                    address=(syslog_host, syslog_port), socktype=socket.SOCK_DGRAM
                )
                syslog_handler.setLevel(self.log_level)
                root_logger.addHandler(syslog_handler)
                self.handlers.append(syslog_handler)
                structlog.get_logger().info(
                    "syslog_enabled", host=syslog_host, port=syslog_port
                )
            except Exception as e:
                structlog.get_logger().warning(
                    "syslog_setup_failed", error=str(e), host=syslog_host
                )

        # Kafka HTTP3 handler
        if enable_kafka and kafka_url:
            try:
                kafka_handler = KafkaHTTP3Handler(kafka_url, kafka_topic, kafka_api_key)
                kafka_handler.setLevel(self.log_level)
                kafka_handler.setFormatter(logging.Formatter("%(message)s"))
                root_logger.addHandler(kafka_handler)
                self.handlers.append(kafka_handler)
                structlog.get_logger().info(
                    "kafka_logging_enabled", url=kafka_url, topic=kafka_topic
                )
            except Exception as e:
                structlog.get_logger().warning("kafka_setup_failed", error=str(e))

        # AWS CloudWatch handler
        if enable_cloudwatch and cloudwatch_log_group and cloudwatch_log_stream:
            try:
                cloudwatch_handler = CloudWatchHandler(
                    cloudwatch_log_group, cloudwatch_log_stream, cloudwatch_region
                )
                cloudwatch_handler.setLevel(self.log_level)
                cloudwatch_handler.setFormatter(logging.Formatter("%(message)s"))
                root_logger.addHandler(cloudwatch_handler)
                self.handlers.append(cloudwatch_handler)
                structlog.get_logger().info(
                    "cloudwatch_enabled",
                    log_group=cloudwatch_log_group,
                    log_stream=cloudwatch_log_stream,
                )
            except Exception as e:
                structlog.get_logger().warning("cloudwatch_setup_failed", error=str(e))

        # GCP Cloud Logging handler
        if enable_gcp and gcp_project_id:
            try:
                from google.cloud import logging as gcp_logging

                client = gcp_logging.Client(project=gcp_project_id)
                gcp_handler = gcp_logging.handlers.CloudLoggingHandler(
                    client, name=gcp_log_name
                )
                gcp_handler.setLevel(self.log_level)
                root_logger.addHandler(gcp_handler)
                self.handlers.append(gcp_handler)
                structlog.get_logger().info(
                    "gcp_logging_enabled", project=gcp_project_id, log_name=gcp_log_name
                )
            except ImportError:
                structlog.get_logger().warning(
                    "gcp_logging_unavailable",
                    message="google-cloud-logging not installed",
                )
            except Exception as e:
                structlog.get_logger().warning("gcp_setup_failed", error=str(e))

        self.logger = structlog.get_logger(self.app_name)
        return self.logger

    def get_logger(self, name: Optional[str] = None) -> structlog.BoundLogger:
        """Get a logger instance."""
        if name:
            return structlog.get_logger(name)
        return self.logger or structlog.get_logger(self.app_name)

    def close(self):
        """Close all handlers."""
        for handler in self.handlers:
            handler.close()


def configure_logging_from_env(app_name: str = "elder") -> structlog.BoundLogger:
    """
    Configure logging from environment variables.

    Environment variables:
        LOG_LEVEL: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        VERBOSITY: Verbosity level (1, 2, 3) - overrides LOG_LEVEL
        SYSLOG_ENABLED: Enable syslog (true/false)
        SYSLOG_HOST: Syslog server hostname
        SYSLOG_PORT: Syslog server port (default: 514)
        KAFKA_ENABLED: Enable Kafka logging (true/false)
        KAFKA_URL: Kafka REST API URL
        KAFKA_TOPIC: Kafka topic name
        KAFKA_API_KEY: Kafka API key
        CLOUDWATCH_ENABLED: Enable CloudWatch (true/false)
        CLOUDWATCH_LOG_GROUP: CloudWatch log group
        CLOUDWATCH_LOG_STREAM: CloudWatch log stream
        CLOUDWATCH_REGION: AWS region
        GCP_LOGGING_ENABLED: Enable GCP Cloud Logging (true/false)
        GCP_PROJECT_ID: GCP project ID
        GCP_LOG_NAME: GCP log name
    """
    # Determine verbosity
    verbosity = int(os.getenv("VERBOSITY", "2"))
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()

    # Map LOG_LEVEL to verbosity if VERBOSITY not set
    if "VERBOSITY" not in os.environ:
        level_to_verbosity = {"WARNING": 1, "INFO": 2, "DEBUG": 3}
        verbosity = level_to_verbosity.get(log_level_name, 2)

    logger_config = StructuredLogger(app_name=app_name, verbosity=verbosity)

    return logger_config.configure(
        enable_console=True,
        enable_syslog=os.getenv("SYSLOG_ENABLED", "false").lower() == "true",
        syslog_host=os.getenv("SYSLOG_HOST"),
        syslog_port=int(os.getenv("SYSLOG_PORT", "514")),
        enable_kafka=os.getenv("KAFKA_ENABLED", "false").lower() == "true",
        kafka_url=os.getenv("KAFKA_URL"),
        kafka_topic=os.getenv("KAFKA_TOPIC", "elder-logs"),
        kafka_api_key=os.getenv("KAFKA_API_KEY"),
        enable_cloudwatch=os.getenv("CLOUDWATCH_ENABLED", "false").lower() == "true",
        cloudwatch_log_group=os.getenv("CLOUDWATCH_LOG_GROUP"),
        cloudwatch_log_stream=os.getenv("CLOUDWATCH_LOG_STREAM"),
        cloudwatch_region=os.getenv("CLOUDWATCH_REGION", "us-east-1"),
        enable_gcp=os.getenv("GCP_LOGGING_ENABLED", "false").lower() == "true",
        gcp_project_id=os.getenv("GCP_PROJECT_ID"),
        gcp_log_name=os.getenv("GCP_LOG_NAME", "elder"),
    )
