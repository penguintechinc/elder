"""Node.js dependency parser for npm, Yarn, and pnpm package managers.

Supports parsing multiple Node.js dependency file formats:
- package.json: Project manifest with direct dependencies
- package-lock.json: npm lock file (v6 and v7+ formats)
- yarn.lock: Yarn lock file with package resolution
- pnpm-lock.yaml: pnpm lock file in YAML format

Each parser extracts dependency information and returns standardized
component dictionaries with package names, versions, and metadata.
"""

# flake8: noqa: E501


import json
import re
from typing import Any, Dict, List, Optional

import yaml

from apps.api.services.sbom.base import BaseDependencyParser


class NodeDependencyParser(BaseDependencyParser):
    """Parser for Node.js package manager dependency files.

    Supports npm (package.json, package-lock.json), Yarn (yarn.lock),
    and pnpm (pnpm-lock.yaml) dependency file formats.

    The parser extracts both runtime and development dependencies with
    full version and scope information, generating standard Package URLs
    (purl) for each dependency.
    """

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the given dependency file.

        Args:
            filename: Name of the dependency file to check.

        Returns:
            True if filename matches supported Node.js patterns, False otherwise.
        """
        supported_patterns = [
            "package.json",
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
        ]
        return filename in supported_patterns

    def get_supported_files(self) -> List[str]:
        """Return list of supported dependency filenames.

        Returns:
            List of supported Node.js dependency file names.
        """
        return [
            "package.json",
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
        ]

    def parse(self, content: str, filename: str) -> List[Dict[str, Any]]:
        """Parse a Node.js dependency file and extract components.

        Routes parsing to the appropriate handler based on filename.
        Handles package.json, package-lock.json, yarn.lock, and pnpm-lock.yaml.

        Args:
            content: Raw file content as a string.
            filename: Name of the file being parsed.

        Returns:
            List of dependency dictionaries with standardized format:
            - name: str - Package name (with optional @scope prefix)
            - version: str - Package version or None
            - purl: str - Package URL (pkg:npm/{name}@{version})
            - package_type: str - "npm"
            - scope: str - "runtime" or "dev"
            - direct: bool - True for direct dependencies, False for transitive
            - source_file: str - Name of source file

        Raises:
            ValueError: If content is invalid or cannot be parsed.
        """
        if not self.validate_content(content):
            raise ValueError(f"Invalid or empty content for {filename}")

        try:
            if filename == "package.json":
                return self._parse_package_json(content, filename)
            elif filename == "package-lock.json":
                return self._parse_package_lock_json(content, filename)
            elif filename == "yarn.lock":
                return self._parse_yarn_lock(content, filename)
            elif filename == "pnpm-lock.yaml":
                return self._parse_pnpm_lock_yaml(content, filename)
            else:
                raise ValueError(f"Unsupported Node.js dependency file: {filename}")
        except (json.JSONDecodeError, yaml.YAMLError, ValueError) as e:
            raise ValueError(f"Failed to parse {filename}: {str(e)}")

    def _parse_package_json(self, content: str, filename: str) -> List[Dict[str, Any]]:
        """Parse package.json and extract direct dependencies.

        Args:
            content: Raw package.json content.
            filename: Source filename (package.json).

        Returns:
            List of dependency dictionaries from the package.json file.
        """
        components: List[Dict[str, Any]] = []
        data = json.loads(content)

        # Process runtime dependencies
        if "dependencies" in data:
            for name, version in data["dependencies"].items():
                components.append(
                    self._create_component(name, version, "runtime", True, filename)
                )

        # Process development dependencies
        if "devDependencies" in data:
            for name, version in data["devDependencies"].items():
                components.append(
                    self._create_component(name, version, "dev", True, filename)
                )

        # Process peer dependencies (but mark them as runtime for distinction)
        if "peerDependencies" in data:
            for name, version in data["peerDependencies"].items():
                components.append(
                    self._create_component(name, version, "runtime", True, filename)
                )

        # Process optional dependencies (mark as runtime)
        if "optionalDependencies" in data:
            for name, version in data["optionalDependencies"].items():
                components.append(
                    self._create_component(name, version, "runtime", True, filename)
                )

        return components

    def _parse_package_lock_json(
        self, content: str, filename: str
    ) -> List[Dict[str, Any]]:
        """Parse package-lock.json and extract dependencies.

        Handles both npm v6 (dependencies key) and npm v7+ (packages key) formats.

        Args:
            content: Raw package-lock.json content.
            filename: Source filename (package-lock.json).

        Returns:
            List of dependency dictionaries from the lock file.
        """
        components: List[Dict[str, Any]] = []
        data = json.loads(content)

        # npm v7+ format uses 'packages' key
        if "packages" in data:
            packages = data["packages"]
            for package_path, package_info in packages.items():
                if not package_path or package_path == "":
                    # Skip root package entry
                    continue

                # Extract package name and version
                if isinstance(package_info, dict) and "version" in package_info:
                    # Extract name from path (e.g., "node_modules/lodash" -> "lodash")
                    name = self._extract_name_from_path(package_path)
                    version = package_info.get("version")
                    dev = package_info.get("dev", False)
                    scope = "dev" if dev else "runtime"
                    components.append(
                        self._create_component(name, version, scope, False, filename)
                    )

        # npm v6 format uses 'dependencies' key
        elif "dependencies" in data:
            dependencies = data["dependencies"]
            for name, dep_info in dependencies.items():
                if isinstance(dep_info, dict) and "version" in dep_info:
                    version = dep_info.get("version")
                    dev = dep_info.get("dev", False)
                    scope = "dev" if dev else "runtime"
                    components.append(
                        self._create_component(name, version, scope, False, filename)
                    )

        return components

    def _parse_yarn_lock(self, content: str, filename: str) -> List[Dict[str, Any]]:
        """Parse yarn.lock and extract dependencies.

        Parses Yarn's custom lock file format which uses:
        key@version_spec:
          resolved "actual-version"
          ...

        Args:
            content: Raw yarn.lock content.
            filename: Source filename (yarn.lock).

        Returns:
            List of dependency dictionaries from the Yarn lock file.
        """
        components: List[Dict[str, Any]] = []
        lines = content.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i].strip()
            i += 1

            # Skip comments and empty lines
            if not line or line.startswith("#"):
                continue

            # Look for dependency entries: "package-name@version-spec:"
            if ":" in line and line.endswith(":"):
                # Parse the key to extract package name and version
                key = line.rstrip(":")

                # Extract package name and version spec from the key
                # Format: "@scope/package@version" or "package@version"
                match = self._parse_yarn_key(key)
                if match:
                    name, version_spec = match

                    # Look ahead for resolved version
                    resolved_version = None
                    while i < len(lines):
                        next_line = lines[i].strip()
                        if not next_line:
                            i += 1
                            break
                        if next_line.startswith('resolved "'):
                            # Extract resolved version URL or version string
                            resolved_version = self._extract_yarn_resolved_version(
                                next_line
                            )
                            i += 1
                            break
                        elif next_line.endswith(":"):
                            # Next entry found, stop looking
                            break
                        i += 1

                    # Use resolved version if available, otherwise use spec
                    final_version = resolved_version or version_spec
                    components.append(
                        self._create_component(
                            name, final_version, "runtime", False, filename
                        )
                    )

        return components

    def _parse_yarn_key(self, key: str) -> Optional[tuple]:
        """Parse a Yarn lock file key to extract package name and version spec.

        Handles both scoped (@scope/package@version) and unscoped (package@version) formats.

        Args:
            key: The key line from yarn.lock (without trailing colon).

        Returns:
            Tuple of (name, version_spec) if parseable, None otherwise.
        """
        # Handle scoped packages: @scope/package@version-spec
        # Example: "@angular/core@16.0.0" -> parts = ["", "angular", "core@16.0.0"]
        if key.startswith("@"):
            # Split on @, first part is empty, second is scope
            parts = key.split("@", 2)  # Split into at most 3 parts
            if len(parts) == 3:
                scope = parts[1]
                rest = parts[2]
                # Now split the rest on the last @ to separate package name from version
                name_and_version = rest.rsplit("@", 1)
                if len(name_and_version) == 2:
                    name = f"@{scope}/{name_and_version[0]}"
                    version_spec = name_and_version[1]
                    return (name, version_spec)

        # Handle unscoped packages: package@version-spec
        if "@" in key:
            parts = key.rsplit("@", 1)
            if len(parts) == 2:
                return (parts[0], parts[1])

        return None

    def _extract_yarn_resolved_version(self, resolved_line: str) -> Optional[str]:
        """Extract version from Yarn's resolved line.

        Args:
            resolved_line: Line containing resolved version (e.g., 'resolved "https://..."').

        Returns:
            Extracted version or None if cannot be parsed.
        """
        # Extract content between quotes
        match = re.search(r'resolved "([^"]+)"', resolved_line)
        if match:
            resolved_url = match.group(1)
            # Try to extract version from npm registry URL
            # Format: https://registry.npmjs.org/package/-/package-1.2.3.tgz
            version_match = re.search(r"-/[^/]+-(.+?)\.tgz|npm/(.+?)#", resolved_url)
            if version_match:
                return version_match.group(1) or version_match.group(2)
            # If it's a git URL, return the commit hash or ref
            if "git" in resolved_url:
                return resolved_url
        return None

    def _parse_pnpm_lock_yaml(
        self, content: str, filename: str
    ) -> List[Dict[str, Any]]:
        """Parse pnpm-lock.yaml and extract dependencies.

        Args:
            content: Raw pnpm-lock.yaml content.
            filename: Source filename (pnpm-lock.yaml).

        Returns:
            List of dependency dictionaries from the pnpm lock file.
        """
        components: List[Dict[str, Any]] = []

        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {filename}: {str(e)}")

        if not data:
            return components

        # pnpm v6+ uses 'packages' key with format: "package-name@version"
        if "packages" in data and isinstance(data["packages"], dict):
            for package_key, package_info in data["packages"].items():
                # Extract name and version from key
                match = self._parse_pnpm_package_key(package_key)
                if match:
                    name, version = match
                    dev = (
                        package_info.get("dev", False)
                        if isinstance(package_info, dict)
                        else False
                    )
                    scope = "dev" if dev else "runtime"
                    components.append(
                        self._create_component(name, version, scope, False, filename)
                    )

        # Process dependencies section (direct dependencies from package.json)
        if "dependencies" in data and isinstance(data["dependencies"], dict):
            for name, dep_info in data["dependencies"].items():
                if isinstance(dep_info, dict):
                    version = dep_info.get("version")
                    if version:
                        components.append(
                            self._create_component(
                                name, version, "runtime", True, filename
                            )
                        )

        # Process devDependencies section
        if "devDependencies" in data and isinstance(data["devDependencies"], dict):
            for name, dep_info in data["devDependencies"].items():
                if isinstance(dep_info, dict):
                    version = dep_info.get("version")
                    if version:
                        components.append(
                            self._create_component(name, version, "dev", True, filename)
                        )

        return components

    def _parse_pnpm_package_key(self, package_key: str) -> Optional[tuple]:
        """Parse a pnpm package key to extract name and version.

        Handles both scoped and unscoped packages.

        Args:
            package_key: Package key from pnpm packages section.

        Returns:
            Tuple of (name, version) if parseable, None otherwise.
        """
        # Handle scoped packages: @scope/package@version
        if package_key.startswith("@"):
            parts = package_key.split("@")
            if len(parts) >= 3:
                scope = parts[1]
                name_and_version = "@".join(parts[2:])
                # Find the last @ which separates name from version
                last_at = name_and_version.rfind("@")
                if last_at != -1:
                    name = f"@{scope}/{name_and_version[:last_at]}"
                    version = name_and_version[last_at + 1 :]
                    return (name, version)

        # Handle unscoped packages: package@version
        if "@" in package_key:
            parts = package_key.rsplit("@", 1)
            if len(parts) == 2:
                return (parts[0], parts[1])

        return None

    def _extract_name_from_path(self, path: str) -> str:
        """Extract package name from node_modules path.

        Args:
            path: Path like "node_modules/@scope/package" or "node_modules/package".

        Returns:
            Extracted package name.
        """
        # Remove node_modules prefix if present
        if path.startswith("node_modules/"):
            path = path[13:]  # len("node_modules/") = 13

        # Return full path as name (includes scope if present)
        return path

    def _create_component(
        self,
        name: str,
        version: Optional[str],
        scope: str,
        direct: bool,
        source_file: str,
    ) -> Dict[str, Any]:
        """Create a standardized component dictionary.

        Args:
            name: Package name (may include @scope prefix).
            version: Package version string or None.
            scope: Dependency scope: "runtime" or "dev".
            direct: True if direct dependency, False if transitive.
            source_file: Source filename.

        Returns:
            Standardized component dictionary with all required fields.
        """
        # Normalize version
        normalized_version = self.normalize_version(version) if version else None

        # Create Package URL (purl)
        purl = self._generate_purl(name, normalized_version)

        return {
            "name": name,
            "version": normalized_version,
            "purl": purl,
            "package_type": "npm",
            "scope": scope,
            "direct": direct,
            "source_file": source_file,
        }

    def _generate_purl(self, name: str, version: Optional[str]) -> str:
        """Generate a Package URL (purl) for an npm package.

        Args:
            name: Package name (may include @scope prefix).
            version: Package version or None.

        Returns:
            Package URL in format "pkg:npm/{name}@{version}" or "pkg:npm/{name}".
        """
        # URL-encode the name and version if necessary
        # npm purl format handles scoped packages: pkg:npm/@scope/name@version
        if version:
            return f"pkg:npm/{name}@{version}"
        return f"pkg:npm/{name}"
