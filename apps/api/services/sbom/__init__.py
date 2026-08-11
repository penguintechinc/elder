"""SBOM (Software Bill of Materials) service for dependency tracking and vulnerability management.

Provides comprehensive dependency parsing, SBOM generation, and vulnerability
scanning across multiple package managers and dependency formats.
"""

# flake8: noqa: E501

from .service import SBOMService

__all__ = ["SBOMService"]
