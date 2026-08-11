"""Rust Cargo dependency file parser.

Parses Cargo.toml and Cargo.lock files to extract Rust crate dependencies
and version information. Handles standard dependencies, dev-dependencies,
and build-dependencies with support for various version specifiers.
"""

# flake8: noqa: E501

import re
import tomllib
from typing import Any, Dict, List

from apps.api.services.sbom.base import BaseDependencyParser


class RustDependencyParser(BaseDependencyParser):
    """Parser for Rust Cargo dependency files.

    Supports parsing Cargo.toml and Cargo.lock files to extract crate
    dependencies with version information, scopes (runtime/dev/build),
    and metadata for git and path dependencies.

    Attributes:
        SUPPORTED_FILES: List of filenames this parser can handle.
    """

    SUPPORTED_FILES = ["Cargo.toml", "Cargo.lock"]

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the given file.

        Args:
            filename: Name of the dependency file to check.

        Returns:
            True if filename matches Cargo.toml or Cargo.lock, False otherwise.
        """
        return filename in self.SUPPORTED_FILES

    def get_supported_files(self) -> list[str]:
        """Return list of supported dependency filenames.

        Returns:
            List containing 'Cargo.toml' and 'Cargo.lock'.
        """
        return self.SUPPORTED_FILES

    def parse(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse Cargo.toml or Cargo.lock and extract dependencies.

        Parses TOML format to extract crate dependencies, distinguishing
        between runtime, dev, and build dependencies for Cargo.toml files.
        For Cargo.lock files, extracts pinned versions of all dependencies.

        Args:
            content: Raw file content as a string.
            filename: Name of the file being parsed (Cargo.toml or Cargo.lock).

        Returns:
            List of dictionaries with keys:
            - name: Crate name (str)
            - version: Semantic version string (str)
            - purl: Package URL in format pkg:cargo/{name}@{version} (str)
            - package_type: "cargo" (str)
            - scope: "runtime", "dev", or "build" (str)
            - direct: True for Cargo.toml, False for Cargo.lock (bool)
            - source_file: Name of source file (str)

        Raises:
            ValueError: If content is not valid TOML or file format is unsupported.
        """
        if not self.validate_content(content):
            raise ValueError("Empty or invalid file content")

        try:
            data = tomllib.loads(content)
        except Exception as e:
            raise ValueError(f"Failed to parse TOML file: {str(e)}")

        dependencies: list[dict[str, Any]] = []

        if filename == "Cargo.toml":
            dependencies = self._parse_cargo_toml(data)
        elif filename == "Cargo.lock":
            dependencies = self._parse_cargo_lock(data)

        return dependencies

    def _parse_cargo_toml(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse Cargo.toml file content.

        Extracts dependencies from [dependencies], [dev-dependencies],
        and [build-dependencies] sections with appropriate scope labels.

        Args:
            data: Parsed TOML data dictionary.

        Returns:
            List of dependency dictionaries.
        """
        dependencies: list[dict[str, Any]] = []

        # Parse runtime dependencies
        if "dependencies" in data:
            deps = self._extract_dependencies_from_section(
                data["dependencies"], scope="runtime"
            )
            dependencies.extend(deps)

        # Parse development dependencies
        if "dev-dependencies" in data:
            deps = self._extract_dependencies_from_section(
                data["dev-dependencies"], scope="dev"
            )
            dependencies.extend(deps)

        # Parse build dependencies
        if "build-dependencies" in data:
            deps = self._extract_dependencies_from_section(
                data["build-dependencies"], scope="build"
            )
            dependencies.extend(deps)

        return dependencies

    def _parse_cargo_lock(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse Cargo.lock file content.

        Extracts pinned versions from [[package]] entries.

        Args:
            data: Parsed TOML data dictionary.

        Returns:
            List of dependency dictionaries.
        """
        dependencies: list[dict[str, Any]] = []

        if "package" not in data:
            return dependencies

        packages = data["package"]
        if not isinstance(packages, list):
            return dependencies

        for package in packages:
            if not isinstance(package, dict):
                continue

            name = package.get("name")
            version = package.get("version")

            if not name or not version:
                continue

            # Skip root package (workspace root has no source)
            if "source" not in package:
                continue

            dep_dict = self._create_dependency_dict(
                name=name,
                version=str(version),
                scope="runtime",
                direct=False,
                source_file="Cargo.lock",
            )
            dependencies.append(dep_dict)

        return dependencies

    def _extract_dependencies_from_section(
        self, section: dict[str, Any], scope: str
    ) -> list[dict[str, Any]]:
        """Extract dependencies from a specific section of Cargo.toml.

        Handles various dependency formats:
        - Simple string version: "1.0"
        - Version objects with 'version' key
        - Git dependencies
        - Path dependencies

        Args:
            section: Dictionary of dependencies from a section.
            scope: Scope label (runtime, dev, or build).

        Returns:
            List of dependency dictionaries.
        """
        dependencies: list[dict[str, Any]] = []

        for name, spec in section.items():
            if isinstance(spec, str):
                # Simple version string: crate = "1.0"
                version = self._normalize_cargo_version(spec)
                dep_dict = self._create_dependency_dict(
                    name=name,
                    version=version,
                    scope=scope,
                    direct=True,
                    source_file="Cargo.toml",
                )
                dependencies.append(dep_dict)

            elif isinstance(spec, dict):
                # Complex specification: crate = { version = "1.0", ... }
                version = spec.get("version")

                if version is None:
                    # Handle git or path dependencies
                    if "git" in spec:
                        version = spec.get("rev", spec.get("branch", "git"))
                    elif "path" in spec:
                        version = spec.get("version", "path")
                    else:
                        continue

                version = str(version)
                dep_dict = self._create_dependency_dict(
                    name=name,
                    version=version,
                    scope=scope,
                    direct=True,
                    source_file="Cargo.toml",
                )

                # Add metadata for special dependency types
                if "git" in spec:
                    dep_dict["git"] = spec["git"]
                if "path" in spec:
                    dep_dict["path"] = spec["path"]

                dependencies.append(dep_dict)

        return dependencies

    def _create_dependency_dict(
        self,
        name: str,
        version: str,
        scope: str,
        direct: bool,
        source_file: str,
    ) -> dict[str, Any]:
        """Create a standardized dependency dictionary.

        Args:
            name: Crate name.
            version: Version string.
            scope: Dependency scope (runtime, dev, or build).
            direct: Whether this is a direct dependency.
            source_file: Source filename.

        Returns:
            Dictionary with standardized dependency fields.
        """
        normalized_version = self.normalize_version(version)
        purl = f"pkg:cargo/{name}@{normalized_version}"

        return {
            "name": name,
            "version": normalized_version,
            "purl": purl,
            "package_type": "cargo",
            "scope": scope,
            "direct": direct,
            "source_file": source_file,
        }

    def _normalize_cargo_version(self, version_spec: str) -> str:
        """Normalize Cargo version specifications to semantic version.

        Handles various version specifier formats:
        - "1.0" -> "1.0"
        - "^1.0" -> "1.0" (compatible with version)
        - "~1.0" -> "1.0" (approximately version)
        - ">=1.0" -> "1.0" (minimum version)
        - "1.0.0" -> "1.0.0" (exact version)

        Args:
            version_spec: Version specifier from Cargo.toml.

        Returns:
            Normalized semantic version string.
        """
        if not version_spec:
            return "unknown"

        version_spec = version_spec.strip()

        # Remove version operators but keep the core version
        # Pattern matches: ^, ~, >=, >, <=, <, =, !=
        match = re.match(r"^[=~^><!\s]*(.+?)(?:\s*-\s*.+)?$", version_spec)
        if match:
            version = match.group(1).strip()
            if version:
                return version

        return version_spec if version_spec else "unknown"

    def normalize_version(self, version: str) -> str:
        """Normalize version strings to semantic versioning format.

        Handles Cargo-specific version formats and delegates to
        normalization helper for complex version specs.

        Args:
            version: Raw version string from dependency file.

        Returns:
            Normalized version string in semantic versioning format.
        """
        if not version:
            return "unknown"

        # Try specialized Cargo version normalization
        normalized = self._normalize_cargo_version(version)
        if normalized != version:
            return normalized

        # Fallback to basic normalization from parent class
        return super().normalize_version(version)
