"""Go dependency file parser for go.mod and go.sum files.

Provides parsing capabilities for Go module files (go.mod and go.sum),
extracting dependency information including module paths, versions, and hashes.

Supports:
- go.mod: Module declarations, require blocks, replace and exclude directives
- go.sum: Hash verification for module versions (optional, for completeness)
"""

# flake8: noqa: E501

import re
from typing import Any, Dict, List, Optional

from ..base import BaseDependencyParser


class GoParser(BaseDependencyParser):
    """Parser for Go module dependency files (go.mod and go.sum).

    Extracts dependency information from Go module files, supporting both
    single-line and multi-line require/replace directives. Properly handles
    indirect dependencies marked with "// indirect" comments.

    Attributes:
        _go_mod_pattern: Regex pattern for parsing single require/replace/exclude lines
        _go_mod_block_pattern: Regex pattern for matching require block start
    """

    def __init__(self) -> None:
        """Initialize Go parser with regex patterns for go.mod parsing."""
        # Pattern for parsing go.mod require/replace/exclude lines
        # Matches: "require|replace|exclude module_path version [// indirect]"
        self._go_mod_pattern = re.compile(
            r"^\s*(require|replace|exclude)\s+"
            r"(?P<module>[\w\-\.\/]+)\s+"
            r"(?P<version>v[\d\.\-\+\w]+)"
            r"(?:\s*//\s*indirect)?"
            r"(?:\s*=>.*)?$",
            re.MULTILINE,
        )

        # Pattern for detecting multi-line blocks
        self._go_mod_block_pattern = re.compile(
            r"^\s*(require|replace|exclude)\s*\(\s*$", re.MULTILINE
        )

        # Pattern for parsing lines within require blocks
        self._go_mod_block_line_pattern = re.compile(
            r"^\s*(?P<module>[\w\-\.\/]+)\s+"
            r"(?P<version>v[\d\.\-\+\w]+)"
            r"(?:\s*//\s*indirect)?"
            r"(?:\s*=>.*)?$",
            re.MULTILINE,
        )

        # Pattern for go.sum entries (module, version, hash)
        # Hash format: h1:base64string= or similar
        self._go_sum_pattern = re.compile(
            r"^(?P<module>[\w\-\.\/]+)\s+"
            r"(?P<version>v[\d\.\-\+\w]+)\s+"
            r"(?P<hash>h[\d]:[\w\+\/=]+)$",
            re.MULTILINE,
        )

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the given file.

        Args:
            filename: Name of the file to check (e.g., 'go.mod', 'go.sum').

        Returns:
            True if filename is 'go.mod' or 'go.sum', False otherwise.
        """
        return filename.strip() in ("go.mod", "go.sum")

    def get_supported_files(self) -> list[str]:
        """Return list of supported Go dependency files.

        Returns:
            List containing ['go.mod', 'go.sum'].
        """
        return ["go.mod", "go.sum"]

    def parse(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse Go module files and extract dependency information.

        Args:
            content: Raw file content as a string.
            filename: Name of the file being parsed ('go.mod' or 'go.sum').

        Returns:
            List of dictionaries containing dependency information with fields:
            - name: Module path (str) - e.g., "github.com/gin-gonic/gin"
            - version: Semantic version with v prefix (str) - e.g., "v1.9.1"
            - purl: Package URL format (str) - "pkg:golang/{name}@{version}"
            - package_type: Package type (str) - "go"
            - scope: Scope of dependency (str) - "runtime" (Go has no dev deps)
            - direct: Whether it's a direct dependency (bool)
            - source_file: Source filename (str)

        Raises:
            ValueError: If content is invalid or unparseable.
        """
        if not self.validate_content(content):
            raise ValueError("Invalid or empty go file content")

        if filename == "go.mod":
            return self._parse_go_mod(content, filename)
        elif filename == "go.sum":
            return self._parse_go_sum(content, filename)
        else:
            raise ValueError(f"Unsupported file: {filename}")

    def _parse_go_mod(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse go.mod file and extract dependencies.

        Handles both single-line and multi-line require/replace/exclude blocks.
        Detects indirect dependencies marked with "// indirect" comments.

        Args:
            content: Raw go.mod file content.
            filename: Name of the file (always 'go.mod' in this context).

        Returns:
            List of parsed dependency dictionaries.
        """
        dependencies: list[dict[str, Any]] = []

        # First, normalize the content to handle multi-line blocks
        normalized_content = self._normalize_go_mod_blocks(content)

        # Parse all require/replace/exclude lines
        for match in self._go_mod_pattern.finditer(normalized_content):
            directive = match.group(1)
            module = match.group("module")
            version = match.group("version")

            # Skip replace and exclude directives - we only care about requires
            if directive != "require":
                continue

            # Check if this line has the "// indirect" comment
            line_start = match.start()
            line_end = normalized_content.find("\n", match.end())
            if line_end == -1:
                line_end = len(normalized_content)

            line = normalized_content[line_start:line_end]
            is_indirect = "// indirect" in line

            # Build the dependency dictionary
            dep = self._build_dependency_dict(
                name=module,
                version=version,
                source_file=filename,
                is_direct=not is_indirect,
            )
            dependencies.append(dep)

        return dependencies

    def _parse_go_sum(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse go.sum file for hash verification.

        Args:
            content: Raw go.sum file content.
            filename: Name of the file (always 'go.sum' in this context).

        Returns:
            List of parsed dependency dictionaries with hash information.
        """
        dependencies: list[dict[str, Any]] = []
        seen_modules: set = set()

        # Parse all go.sum entries
        for match in self._go_sum_pattern.finditer(content):
            module = match.group("module")
            version = match.group("version")
            hash_value = match.group("hash")

            # Skip duplicate entries (go.sum can have multiple hashes per module)
            module_version_key = f"{module}@{version}"
            if module_version_key in seen_modules:
                continue

            seen_modules.add(module_version_key)

            # Build the dependency dictionary
            dep = self._build_dependency_dict(
                name=module,
                version=version,
                source_file=filename,
                is_direct=False,  # go.sum entries are typically indirect
                hash_value=hash_value,
            )
            dependencies.append(dep)

        return dependencies

    def _normalize_go_mod_blocks(self, content: str) -> str:
        """Normalize multi-line require/replace/exclude blocks in go.mod.

        Converts multi-line blocks into single-line format for easier parsing.
        Example:
            require (
                github.com/module/a v1.0.0
                github.com/module/b v2.0.0 // indirect
            )

        Becomes multiple single-line requires that can be parsed uniformly.

        Args:
            content: Raw go.mod file content.

        Returns:
            Normalized content with blocks expanded to single lines.
        """
        # Find all require/replace/exclude blocks
        lines = content.split("\n")
        result = []
        i = 0

        while i < len(lines):
            line = lines[i]

            # Check if this line starts a block
            if self._go_mod_block_pattern.match(line):
                # Extract the directive type (require, replace, exclude)
                match = self._go_mod_block_pattern.match(line)
                directive = match.group(1)

                # Collect all lines until closing parenthesis
                i += 1
                while i < len(lines):
                    block_line = lines[i]

                    # Check for closing parenthesis
                    if block_line.strip() == ")":
                        i += 1
                        break

                    # Skip empty lines and comments
                    stripped = block_line.strip()
                    if not stripped or stripped.startswith("//"):
                        i += 1
                        continue

                    # Add the directive prefix to convert to single-line format
                    result.append(f"{directive} {stripped}")
                    i += 1
            else:
                result.append(line)
                i += 1

        return "\n".join(result)

    def _build_dependency_dict(
        self,
        name: str,
        version: str,
        source_file: str,
        is_direct: bool,
        hash_value: str | None = None,
    ) -> dict[str, Any]:
        """Build a standardized dependency dictionary.

        Args:
            name: Module path (e.g., 'github.com/gin-gonic/gin').
            version: Semantic version with v prefix (e.g., 'v1.9.1').
            source_file: Source filename ('go.mod' or 'go.sum').
            is_direct: Whether this is a direct dependency.
            hash_value: Optional hash value from go.sum.

        Returns:
            Dictionary with standardized dependency information.
        """
        # Ensure version has 'v' prefix
        if not version.startswith("v"):
            version = f"v{version}"

        # Build Package URL (PURL)
        purl = f"pkg:golang/{name}@{version}"

        dep_dict: dict[str, Any] = {
            "name": name,
            "version": version,
            "purl": purl,
            "package_type": "go",
            "scope": "runtime",  # Go doesn't have separate dev dependencies
            "direct": is_direct,
            "source_file": source_file,
        }

        # Add hash if provided
        if hash_value:
            dep_dict["hash"] = hash_value

        return dep_dict
