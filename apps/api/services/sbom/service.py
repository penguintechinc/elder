"""SBOM service for parsing and managing software dependencies.

Provides the main service interface for dependency file parsing,
component extraction, and repository scanning with support for
multiple package managers and dependency formats.
"""

# flake8: noqa: E501

import os
from typing import Any, Dict, List, Optional

from .base import BaseDependencyParser


class SBOMService:
    """Service for Software Bill of Materials (SBOM) management.

    Handles parsing of dependency files, extraction of component information,
    and scanning of software repositories for dependencies. Supports multiple
    package managers and dependency file formats through a pluggable parser
    architecture.

    Attributes:
        db: Database connection/session reference for storing parsed components.
    """

    def __init__(self, db: Any | None = None) -> None:
        """Initialize SBOM service.

        Args:
            db: Optional database connection/session reference for storing
                parsed component information. Can be None if only parsing
                is performed without persistence.
        """
        self.db = db
        self._parsers: list[BaseDependencyParser] = []

    def register_parser(self, parser: BaseDependencyParser) -> None:
        """Register a dependency parser with the service.

        Args:
            parser: Instance of a BaseDependencyParser subclass to register.

        Raises:
            ValueError: If parser is not a BaseDependencyParser instance.
        """
        if not isinstance(parser, BaseDependencyParser):
            raise ValueError("Parser must be an instance of BaseDependencyParser")
        self._parsers.append(parser)

    def get_parser_for_file(self, filename: str) -> BaseDependencyParser | None:
        """Find appropriate parser for the given dependency file.

        Iterates through registered parsers and returns the first one
        that can handle the given filename.

        Args:
            filename: Name of the dependency file to find a parser for.

        Returns:
            Parser instance if one is found, None otherwise.
        """
        for parser in self._parsers:
            if parser.can_parse(filename):
                return parser
        return None

    def parse_dependency_file(
        self, filename: str, content: str
    ) -> list[dict[str, Any]]:
        """Parse a single dependency file and extract components.

        Finds an appropriate parser for the file and uses it to extract
        component information from the file content.

        Args:
            filename: Name of the dependency file being parsed.
            content: Raw file content as a string.

        Returns:
            List of component dictionaries with extracted dependency information.
            Returns empty list if no suitable parser is found.

        Raises:
            ValueError: If content is invalid or unparseable by the selected parser.
        """
        parser = self.get_parser_for_file(filename)
        if parser is None:
            return []

        return parser.parse(content, filename)

    def scan_repository(self, repo_path: str) -> dict[str, Any]:
        """Scan entire repository for dependency files.

        Recursively scans a repository directory for known dependency files
        and parses them to extract component information. This is a placeholder
        for Phase 2 implementation that will provide comprehensive repository
        scanning capabilities.

        Args:
            repo_path: Path to the repository root directory to scan.

        Returns:
            Dictionary containing scan results with structure:
            {
                'status': 'success' | 'error',
                'path': str,
                'files_scanned': int,
                'dependencies': List[Dict],
                'errors': List[str],
                'metadata': Dict
            }

        Raises:
            IOError: If repo_path does not exist or is not readable.
            ValueError: If repo_path is not a valid directory.
        """
        if not os.path.isdir(repo_path):
            raise ValueError(f"Invalid repository path: {repo_path}")

        result: dict[str, Any] = {
            "status": "success",
            "path": repo_path,
            "files_scanned": 0,
            "dependencies": [],
            "errors": [],
            "metadata": {
                "implementation": "placeholder",
                "description": "Full repository scanning will be implemented in Phase 2",
            },
        }

        return result
