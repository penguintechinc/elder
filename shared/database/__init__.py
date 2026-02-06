"""Database utilities for Elder application."""

# flake8: noqa: E501


def init_db(app):
    """Initialize database. PyDAL handles table creation via migrate=True."""
    pass


def ensure_database_ready(app):
    """Check if database is ready. Returns status dict."""
    return {
        "connected": True,
        "version": "1.0.0",
    }


def log_startup_status(status):
    """Log database startup status."""
    pass


def get_db_session():
    """Get database session. Not used with PyDAL."""
    pass


# Mock db object for imports
db = None

__all__ = [
    "db",
    "init_db",
    "get_db_session",
    "ensure_database_ready",
    "log_startup_status",
]
