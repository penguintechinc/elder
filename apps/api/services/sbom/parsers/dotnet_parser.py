"""Parser for .NET project dependency files (csproj, fsproj, packages.config).

Supports parsing of:
- .csproj files (C# project files with PackageReference elements)
- .fsproj files (F# project files with PackageReference elements)
- packages.config (legacy NuGet configuration files)

Extracts package dependencies and converts them to standardized component
information compatible with the SBOM service.
"""

# flake8: noqa: E501

import re
from typing import Any, Dict, List, Optional
from xml.etree.ElementTree import Element  # For type hints only

import defusedxml.ElementTree as ET

from apps.api.services.sbom.base import BaseDependencyParser


class DotnetParser(BaseDependencyParser):
    """Parser for .NET project dependency files.

    Handles parsing of .csproj, .fsproj, and packages.config files to extract
    NuGet package dependencies. Supports both modern PackageReference format
    (in project files) and legacy packages.config format.

    Attributes:
        SUPPORTED_FILES: List of supported file patterns.
        NUGET_REGISTRY: Standard NuGet package registry.
    """

    SUPPORTED_FILES = ["*.csproj", "*.fsproj", "packages.config"]
    NUGET_REGISTRY = "https://api.nuget.org/v3/index.json"

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the given file.

        Args:
            filename: Name of the dependency file.

        Returns:
            True if filename matches .csproj, .fsproj, or packages.config,
            False otherwise.
        """
        return any(
            filename.endswith(pattern.replace("*", ""))
            for pattern in self.SUPPORTED_FILES
        )

    def get_supported_files(self) -> list[str]:
        """Return list of supported dependency file patterns.

        Returns:
            List of filename patterns this parser supports.
        """
        return self.SUPPORTED_FILES.copy()

    def parse(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse a .NET dependency file and extract package information.

        Args:
            content: Raw file content as a string.
            filename: Name of the file being parsed.

        Returns:
            List of dictionaries containing package information with fields:
            - name: str - Package name
            - version: str - Semantic version
            - purl: str - Package URL (pkg:nuget/name@version)
            - package_type: str - "nuget"
            - scope: str - "runtime" or "dev"
            - direct: bool - True (always direct for csproj files)
            - source_file: str - Name of the source file

        Raises:
            ValueError: If content is invalid or not valid XML.
        """
        if not self.validate_content(content):
            return []

        try:
            if filename.endswith("packages.config"):
                return self._parse_packages_config(content, filename)
            else:
                return self._parse_project_file(content, filename)
        except ET.ParseError as e:
            raise ValueError(f"Invalid XML in {filename}: {str(e)}")
        except Exception as e:
            raise ValueError(f"Error parsing {filename}: {str(e)}")

    def _parse_project_file(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse .csproj or .fsproj file for package references.

        Args:
            content: Raw XML file content.
            filename: Name of the project file.

        Returns:
            List of parsed package dictionaries.
        """
        packages: list[dict[str, Any]] = []

        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            raise ValueError(f"Invalid XML: {str(e)}")

        # Define namespaces used in project files
        namespaces = {
            "": "http://schemas.microsoft.com/developer/msbuild/2003",
        }

        # Find all PackageReference elements (may be in ItemGroup)
        for elem in root.iter():
            # Check both with and without namespace
            tag_name = elem.tag
            if tag_name.endswith("}PackageReference"):
                package = self._extract_package_reference(elem, filename)
                if package:
                    packages.append(package)

        return packages

    def _parse_packages_config(
        self, content: str, filename: str
    ) -> list[dict[str, Any]]:
        """Parse legacy packages.config file for package definitions.

        Args:
            content: Raw XML file content.
            filename: Name of the packages.config file.

        Returns:
            List of parsed package dictionaries.
        """
        packages: list[dict[str, Any]] = []

        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            raise ValueError(f"Invalid XML: {str(e)}")

        # Find all package elements
        for elem in root.iter():
            tag_name = elem.tag
            if tag_name.endswith("}package") or tag_name == "package":
                package = self._extract_package_config(elem, filename)
                if package:
                    packages.append(package)

        return packages

    def _extract_package_reference(
        self, element: Element, filename: str
    ) -> dict[str, Any] | None:
        """Extract package information from PackageReference element.

        Args:
            element: XML element representing a PackageReference.
            filename: Source file name.

        Returns:
            Dictionary with package information, or None if extraction fails.
        """
        name = element.get("Include")
        version = element.get("Version")

        if not name or not version:
            return None

        # Extract scope from Condition attribute or metadata
        scope = self._extract_scope_from_element(element)

        # Normalize version string
        normalized_version = self.normalize_version(version)

        return {
            "name": name.strip(),
            "version": normalized_version,
            "purl": f"pkg:nuget/{name.strip()}@{normalized_version}",
            "package_type": "nuget",
            "scope": scope,
            "direct": True,
            "source_file": filename,
        }

    def _extract_package_config(
        self, element: Element, filename: str
    ) -> dict[str, Any] | None:
        """Extract package information from packages.config element.

        Args:
            element: XML element representing a package.
            filename: Source file name.

        Returns:
            Dictionary with package information, or None if extraction fails.
        """
        name = element.get("id")
        version = element.get("version")

        if not name or not version:
            return None

        # Extract scope from targetFramework or developmentDependency
        dev_dependency = element.get("developmentDependency", "false").lower() == "true"
        scope = "dev" if dev_dependency else "runtime"

        # Normalize version string
        normalized_version = self.normalize_version(version)

        return {
            "name": name.strip(),
            "version": normalized_version,
            "purl": f"pkg:nuget/{name.strip()}@{normalized_version}",
            "package_type": "nuget",
            "scope": scope,
            "direct": True,
            "source_file": filename,
        }

    def _extract_scope_from_element(self, element: Element) -> str:
        """Extract scope (runtime/dev) from element attributes or metadata.

        Args:
            element: XML element to examine.

        Returns:
            "dev" if element is a development dependency, "runtime" otherwise.
        """
        # Check PrivateAssets attribute - if it contains "All", it's typically dev only
        private_assets = element.get("PrivateAssets", "").lower()
        if "all" in private_assets:
            return "dev"

        # Check for output metadata or condition
        condition = element.get("Condition", "").lower()
        if "test" in condition or "debug" in condition:
            return "dev"

        return "runtime"

    def normalize_version(self, version: str) -> str:
        """Normalize .NET version strings to semantic versioning format.

        Handles various .NET version formats:
        - Standard: "1.0.0", "1.0.0-beta"
        - Floating: "1.0.*"
        - Ranges: "[1.0.0,2.0.0)", "[1.0.0]", etc.

        Args:
            version: Raw version string from dependency file.

        Returns:
            Normalized version string suitable for use in PURL.
        """
        if not version:
            return "unknown"

        version = version.strip()

        # Handle range notations like [1.0.0,2.0.0) -> use lower bound
        range_match = re.match(r"[\[\(]([^,\]\)]+)", version)
        if range_match:
            version = range_match.group(1).strip()

        # Handle floating versions like 1.0.* -> convert to 1.0.0
        if version.endswith(".*"):
            version = version[:-2] + ".0"

        # Remove any remaining brackets or parentheses
        version = re.sub(r"[\[\(\)\]]", "", version)

        return version if version else "unknown"
