"""Centralized logging configuration for Agentic KG.

This module provides a consistent logging setup across all components:
- CLI tools
- API services
- Background workers
- Tests

Usage:
    from agentic_kg.logging_config import setup_logging, get_logger

    # At application entry point (main.py, cli.py)
    setup_logging(level="INFO", format="json")

    # In each module
    logger = get_logger(__name__)
    logger.info("Message", extra={"request_id": "123"})
"""

import logging
import os
import sys
from typing import Literal, Optional

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["text", "json"]


def setup_logging(
    level: Optional[LogLevel] = None,
    format: LogFormat = "text",
    enable_cloud_logging: bool = False,
) -> None:
    """
    Configure logging for the entire application.

    Should be called once at application startup (main.py, cli.py, etc.)

    Args:
        level: Log level (default: from LOG_LEVEL env or INFO)
        format: Log format - "text" for human-readable, "json" for structured
        enable_cloud_logging: Enable Google Cloud Logging integration
    """
    # Determine log level
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO").upper()

    log_level = getattr(logging, level, logging.INFO)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    # Set format based on preference
    if format == "json":
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Configure specific loggers
    _configure_third_party_loggers()

    # Google Cloud Logging integration
    if enable_cloud_logging:
        _setup_cloud_logging()

    logging.info(f"Logging configured: level={level}, format={format}")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a specific module.

    Creates a hierarchical logger based on module name.
    Use __name__ to get the current module's fully qualified name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance

    Example:
        logger = get_logger(__name__)
        logger.info("Processing started", extra={"count": 10})
    """
    return logging.getLogger(name)


def _configure_third_party_loggers() -> None:
    """Reduce noise from third-party libraries."""
    # HTTP clients
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # Neo4j
    logging.getLogger("neo4j").setLevel(logging.WARNING)

    # LangChain/LangGraph (very verbose)
    logging.getLogger("langchain").setLevel(logging.WARNING)
    logging.getLogger("langgraph").setLevel(logging.WARNING)


def _setup_cloud_logging() -> None:
    """Setup Google Cloud Logging integration."""
    try:
        import google.cloud.logging as cloud_logging

        client = cloud_logging.Client()
        client.setup_logging()
        logging.info("Google Cloud Logging enabled")
    except ImportError:
        logging.warning(
            "google-cloud-logging not installed. "
            "Install with: pip install google-cloud-logging"
        )
    except Exception as e:
        logging.warning(f"Failed to setup Cloud Logging: {e}")


class _JsonFormatter(logging.Formatter):
    """Format log records as JSON for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        from datetime import datetime

        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields (like request_id)
        for key, value in record.__dict__.items():
            if key not in [
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "message", "pathname", "process", "processName", "relativeCreated",
                "thread", "threadName", "exc_info", "exc_text", "stack_info",
            ]:
                log_data[key] = value

        return json.dumps(log_data)


# Context management for request IDs
_request_context = {}


def set_request_id(request_id: str) -> None:
    """Set request ID for the current context (thread/async task)."""
    import threading
    _request_context[threading.get_ident()] = request_id


def get_request_id() -> Optional[str]:
    """Get request ID for the current context."""
    import threading
    return _request_context.get(threading.get_ident())


def clear_request_id() -> None:
    """Clear request ID for the current context."""
    import threading
    _request_context.pop(threading.get_ident(), None)


class RequestIdFilter(logging.Filter):
    """Add request_id to all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = get_request_id()
        if request_id:
            record.request_id = request_id
        return True


__all__ = [
    "setup_logging",
    "get_logger",
    "set_request_id",
    "get_request_id",
    "clear_request_id",
    "RequestIdFilter",
]
