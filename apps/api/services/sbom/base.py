"""Abstract base class for dependency file parsers.

Defines the interface that all dependency format parsers must implement,
including support for package managers like npm, pip, go.mod, Composer, etc.
"""

# flake8: noqa: E501

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class BaseDependencyParser(ABC):
    """Abstract base class for dependency file parsers.

    Provides the interface for parsing various dependency file formats
    (package.json, requirements.txt, go.mod, composer.json, etc.) and
    extracting structured component information.

    All concrete parser implementations must inherit from this class
    and implement all abstract methods.
    """

    @abstractmethod
    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the given dependency file.

        Args:
            filename: Name of the dependency file (e.g., 'package.json', 'requirements.txt').

        Returns:
            True if this parser can handle the file, False otherwise.
        """

    @abstractmethod
    def parse(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse a dependency file and extract components.

        Extracts structured information about dependencies from the file content,
        returning a list of component dictionaries with standardized fields.

        Args:
            content: Raw file content as a string.
            filename: Name of the file being parsed.

        Returns:
            List of dictionaries containing component information with fields:
            - name: str - Package/component name
            - version: str - Semantic version
            - type: str - Package type (npm, pip, go, composer, etc.)
            - source: str - Package source/registry
            - dev_dependency: bool - Whether this is a development dependency
            Additional fields may be included as needed.

        Raises:
            ValueError: If content is invalid or unparseable.
            IOError: If file reading fails.
        """

    @abstractmethod
    def get_supported_files(self) -> list[str]:
        """Return list of supported dependency filenames and patterns.

        Defines which files this parser is responsible for handling.

        Returns:
            List of filename strings and/or glob patterns that this parser supports.
            Examples: ['package.json'], ['requirements.txt', 'setup.py'],
                      ['go.mod', 'go.sum'].
        """

    def validate_content(self, content: str) -> bool:
        """Validate that content appears to be a valid dependency file.

        Basic validation that can be overridden by subclasses for format-specific checks.

        Args:
            content: Raw file content to validate.

        Returns:
            True if content appears valid, False otherwise.
        """
        return bool(content and content.strip())

    def normalize_version(self, version: str) -> str:
        """Normalize version strings to semantic versioning format.

        Handles various version formats (ranges, pre-releases, etc.)
        and attempts to normalize to semantic versioning.

        Args:
            version: Raw version string from dependency file.

        Returns:
            Normalized version string.
        """
        return version.strip() if version else "unknown"
