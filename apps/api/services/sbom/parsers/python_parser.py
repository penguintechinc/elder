"""Python dependency file parser for pip, Poetry, Pipenv, and setuptools.

Implements parsing of Python dependency files in multiple formats:
- requirements.txt (pip)
- pyproject.toml (Poetry, Flit, Hatch, PDM)
- Pipfile (Pipenv)
- setup.py (legacy setuptools)

Extracts package name, version, and scope information to generate
standardized component dictionaries with Package URLs (PURL).
"""

# flake8: noqa: E501

import re
import sys
from typing import Any, Dict, List, Optional

from ..base import BaseDependencyParser

# Use tomllib for Python 3.11+, fallback to toml library
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import toml as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore


class PythonDependencyParser(BaseDependencyParser):
    """Parser for Python dependency files.

    Supports multiple Python dependency file formats:
    - requirements.txt and requirements-*.txt (pip format)
    - pyproject.toml (Poetry, Flit, Hatch, PDM formats)
    - Pipfile (Pipenv format)
    - setup.py (legacy setuptools, basic extraction)

    Extracts package names, versions, and dependency scopes
    (runtime vs development).
    """

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the given dependency file.

        Args:
            filename: Name of the dependency file to check.

        Returns:
            True if the filename matches supported Python dependency file patterns.
        """
        if not filename:
            return False

        filename_lower = filename.lower()

        # requirements.txt patterns (including requirements-*.txt variants)
        if filename_lower == "requirements.txt":
            return True
        if filename_lower.startswith("requirements-") and filename_lower.endswith(
            ".txt"
        ):
            return True

        # pyproject.toml
        if filename_lower == "pyproject.toml":
            return True

        # Pipfile
        if filename_lower == "pipfile":
            return True

        # setup.py (legacy)
        if filename_lower == "setup.py":
            return True

        return False

    def get_supported_files(self) -> list[str]:
        """Return list of supported dependency filenames and patterns.

        Returns:
            List of supported Python dependency file patterns.
        """
        return [
            "requirements.txt",
            "requirements-*.txt",
            "pyproject.toml",
            "Pipfile",
            "setup.py",
        ]

    def parse(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse a Python dependency file and extract components.

        Routes to the appropriate parsing function based on file type
        and returns a list of structured component dictionaries.

        Args:
            content: Raw file content as a string.
            filename: Name of the file being parsed.

        Returns:
            List of dictionaries containing component information with fields:
            - name: str - Package name
            - version: str - Version string or None if not specified
            - purl: str - Package URL (pkg:pypi/name@version)
            - package_type: str - "pypi"
            - scope: str - "runtime" or "dev"
            - direct: bool - True (direct dependency)
            - source_file: str - Filename this was parsed from

        Raises:
            ValueError: If content is invalid or unparseable.
        """
        if not self.validate_content(content):
            return []

        filename_lower = filename.lower()

        # Route to appropriate parser
        if filename_lower == "requirements.txt" or (
            filename_lower.startswith("requirements-")
            and filename_lower.endswith(".txt")
        ):
            return self._parse_requirements_txt(content, filename)

        elif filename_lower == "pyproject.toml":
            return self._parse_pyproject_toml(content, filename)

        elif filename_lower == "pipfile":
            return self._parse_pipfile(content, filename)

        elif filename_lower == "setup.py":
            return self._parse_setup_py(content, filename)

        return []

    def _parse_requirements_txt(
        self, content: str, filename: str
    ) -> list[dict[str, Any]]:
        """Parse requirements.txt format.

        Handles pip requirements.txt format with support for:
        - Package names with version specifiers (==, >=, ~=, etc.)
        - Comments (lines starting with #)
        - Empty lines
        - Version ranges and pre-releases

        Args:
            content: Raw requirements.txt content.
            filename: Name of the file being parsed.

        Returns:
            List of component dictionaries.
        """
        components: list[dict[str, Any]] = []

        for line in content.splitlines():
            # Strip whitespace
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue

            # Skip git+https URLs and other VCS references
            if line.startswith(("git+", "hg+", "svn+", "bzr+")):
                continue

            # Skip URL references
            if line.startswith(("-r ", "-e ", "http://", "https://")):
                continue

            # Extract package name and version
            component = self._parse_requirement_line(line, filename, "runtime")
            if component:
                components.append(component)

        return components

    def _parse_pyproject_toml(
        self, content: str, filename: str
    ) -> list[dict[str, Any]]:
        """Parse pyproject.toml format.

        Supports multiple Python project formats:
        - Poetry (tool.poetry.dependencies, tool.poetry.dev-dependencies)
        - Flit (project.dependencies, project.optional-dependencies)
        - Hatch (project.dependencies, project.optional-dependencies)
        - PDM (project.dependencies, project.optional-dependencies)

        Args:
            content: Raw pyproject.toml content.
            filename: Name of the file being parsed.

        Returns:
            List of component dictionaries.
        """
        components: list[dict[str, Any]] = []

        if tomllib is None:
            raise ValueError(
                "toml library not available. Install 'toml' package to parse pyproject.toml"
            )

        try:
            # Parse TOML content
            if sys.version_info >= (3, 11):
                # tomllib requires binary mode
                data = tomllib.loads(content)  # type: ignore
            else:
                # toml library works with strings
                data = tomllib.loads(content)  # type: ignore
        except Exception as e:
            raise ValueError(f"Invalid pyproject.toml format: {e}")

        # Check for Poetry format
        if "tool" in data and "poetry" in data.get("tool", {}):
            poetry_data = data["tool"]["poetry"]

            # Parse main dependencies
            if "dependencies" in poetry_data:
                deps = poetry_data["dependencies"]
                for name, spec in deps.items():
                    if name.lower() != "python":  # Skip Python version constraint
                        component = self._parse_poetry_dependency(
                            name, spec, filename, "runtime"
                        )
                        if component:
                            components.append(component)

            # Parse dev dependencies
            if "dev-dependencies" in poetry_data:
                dev_deps = poetry_data["dev-dependencies"]
                for name, spec in dev_deps.items():
                    if name.lower() != "python":
                        component = self._parse_poetry_dependency(
                            name, spec, filename, "dev"
                        )
                        if component:
                            components.append(component)

        # Check for standard PEP 621 format
        elif "project" in data:
            project_data = data["project"]

            # Parse main dependencies
            if "dependencies" in project_data:
                deps = project_data["dependencies"]
                if isinstance(deps, list):
                    for dep_str in deps:
                        component = self._parse_requirement_line(
                            dep_str, filename, "runtime"
                        )
                        if component:
                            components.append(component)

            # Parse optional dependencies (dev, extras, etc.)
            if "optional-dependencies" in project_data:
                opt_deps = project_data["optional-dependencies"]
                if isinstance(opt_deps, dict):
                    for group, deps in opt_deps.items():
                        # Treat as dev scope if group name contains dev, test, etc.
                        scope = (
                            "dev"
                            if any(
                                x in group.lower()
                                for x in ("dev", "test", "lint", "type")
                            )
                            else "runtime"
                        )

                        if isinstance(deps, list):
                            for dep_str in deps:
                                component = self._parse_requirement_line(
                                    dep_str, filename, scope
                                )
                                if component:
                                    components.append(component)

        return components

    def _parse_pipfile(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse Pipfile format (Pipenv).

        Handles Pipfile format with support for:
        - [packages] section (runtime dependencies)
        - [dev-packages] section (development dependencies)
        - Version specifiers in both sections

        Args:
            content: Raw Pipfile content.
            filename: Name of the file being parsed.

        Returns:
            List of component dictionaries.
        """
        components: list[dict[str, Any]] = []

        if tomllib is None:
            raise ValueError(
                "toml library not available. Install 'toml' package to parse Pipfile"
            )

        try:
            if sys.version_info >= (3, 11):
                data = tomllib.loads(content)  # type: ignore
            else:
                data = tomllib.loads(content)  # type: ignore
        except Exception as e:
            raise ValueError(f"Invalid Pipfile format: {e}")

        # Parse [packages] section (runtime)
        if "packages" in data:
            packages = data["packages"]
            if isinstance(packages, dict):
                for name, spec in packages.items():
                    component = self._parse_pipfile_dependency(
                        name, spec, filename, "runtime"
                    )
                    if component:
                        components.append(component)

        # Parse [dev-packages] section (development)
        if "dev-packages" in data:
            dev_packages = data["dev-packages"]
            if isinstance(dev_packages, dict):
                for name, spec in dev_packages.items():
                    component = self._parse_pipfile_dependency(
                        name, spec, filename, "dev"
                    )
                    if component:
                        components.append(component)

        return components

    def _parse_setup_py(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse setup.py format (legacy setuptools).

        Uses regex to extract install_requires list from setup() calls.
        Note: This is a best-effort extraction and does not execute the file.

        Args:
            content: Raw setup.py content.
            filename: Name of the file being parsed.

        Returns:
            List of component dictionaries.
        """
        components: list[dict[str, Any]] = []

        # Extract install_requires list using regex
        # Matches: install_requires=[...] or install_requires = [...]
        install_requires_match = re.search(
            r"install_requires\s*=\s*\[([^\]]*)\]", content, re.DOTALL
        )

        if install_requires_match:
            requires_list = install_requires_match.group(1)

            # Extract individual requirement strings (quoted)
            # Matches: 'package' or "package" or 'package==1.0'
            requirement_matches = re.findall(r'["\']([^"\']+)["\']', requires_list)

            for req_str in requirement_matches:
                component = self._parse_requirement_line(req_str, filename, "runtime")
                if component:
                    components.append(component)

        # Also try to extract extras_require for dev dependencies
        extras_match = re.search(
            r"extras_require\s*=\s*\{([^\}]*)\}", content, re.DOTALL
        )

        if extras_match:
            extras_str = extras_match.group(1)

            # Look for dev-related extras
            dev_extras = re.findall(
                r'["\'](?:dev|development|test|lint|type)["\']?\s*:\s*\[([^\]]*)\]',
                extras_str,
                re.DOTALL | re.IGNORECASE,
            )

            for extras_list in dev_extras:
                requirement_matches = re.findall(r'["\']([^"\']+)["\']', extras_list)

                for req_str in requirement_matches:
                    component = self._parse_requirement_line(req_str, filename, "dev")
                    if component:
                        components.append(component)

        return components

    def _parse_requirement_line(
        self, line: str, filename: str, scope: str
    ) -> dict[str, Any] | None:
        """Parse a single pip requirement line.

        Handles various version specifier formats:
        - name==version (exact)
        - name>=version (minimum)
        - name<=version (maximum)
        - name~=version (compatible)
        - name>version, name<version
        - name (no version)

        Args:
            line: A single requirement line.
            filename: Source filename.
            scope: "runtime" or "dev"

        Returns:
            Component dictionary or None if line is invalid.
        """
        line = line.strip()

        if not line or line.startswith("#"):
            return None

        # Remove inline comments
        if "#" in line:
            line = line.split("#")[0].strip()

        if not line:
            return None

        # Extract package name and version using regex
        # Matches: name, name==1.0, name>=1.0, name~=1.0, etc.
        match = re.match(
            r"^([a-zA-Z0-9._-]+)(?:\s*([><=~!]+)\s*([a-zA-Z0-9._\-+]+))?", line
        )

        if not match:
            return None

        name = match.group(1)
        operator = match.group(2)
        version = match.group(3)

        # Normalize package name (lowercase with hyphens)
        name = name.lower()

        # For version, extract the first part if it's a range (e.g., ">=1.0,<2.0")
        if version and "," in line:
            # Handle complex version specs like ">=1.0,<2.0"
            version_part = line[len(name) :].strip()
            # Extract just the first version number for PURL
            version_match = re.search(r"[0-9]+\.[0-9.]*", version_part)
            if version_match:
                version = version_match.group(0)
            else:
                version = None
        elif not version:
            version = None

        # Build PURL
        if version:
            purl = f"pkg:pypi/{name}@{version}"
        else:
            purl = f"pkg:pypi/{name}"

        return {
            "name": name,
            "version": version,
            "purl": purl,
            "package_type": "pypi",
            "scope": scope,
            "direct": True,
            "source_file": filename,
        }

    def _parse_poetry_dependency(
        self, name: str, spec: Any, filename: str, scope: str
    ) -> dict[str, Any] | None:
        """Parse a Poetry dependency specification.

        Handles Poetry's flexible dependency format:
        - version string (e.g., "^1.0")
        - dictionary with version key (e.g., {"version": "^1.0"})
        - wildcard (e.g., "*")

        Args:
            name: Package name.
            spec: Dependency specification (string or dict).
            filename: Source filename.
            scope: "runtime" or "dev"

        Returns:
            Component dictionary or None if invalid.
        """
        # Normalize name
        name = name.lower()

        version = None

        if isinstance(spec, str):
            # Simple string version (e.g., "^1.0", "1.0", "*")
            if spec != "*":
                # Extract version number from constraint (e.g., "^1.0" -> "1.0")
                version_match = re.search(r"[0-9]+\.[0-9.]*", spec)
                if version_match:
                    version = version_match.group(0)

        elif isinstance(spec, dict):
            # Dictionary format
            if "version" in spec:
                version_str = spec["version"]
                if isinstance(version_str, str) and version_str != "*":
                    version_match = re.search(r"[0-9]+\.[0-9.]*", version_str)
                    if version_match:
                        version = version_match.group(0)

        # Build PURL
        if version:
            purl = f"pkg:pypi/{name}@{version}"
        else:
            purl = f"pkg:pypi/{name}"

        return {
            "name": name,
            "version": version,
            "purl": purl,
            "package_type": "pypi",
            "scope": scope,
            "direct": True,
            "source_file": filename,
        }

    def _parse_pipfile_dependency(
        self, name: str, spec: Any, filename: str, scope: str
    ) -> dict[str, Any] | None:
        """Parse a Pipfile dependency specification.

        Handles Pipfile's dependency format:
        - version string (e.g., "==1.0.0", ">=1.0")
        - dictionary with version key
        - wildcard ("*")

        Args:
            name: Package name.
            spec: Dependency specification (string or dict).
            filename: Source filename.
            scope: "runtime" or "dev"

        Returns:
            Component dictionary or None if invalid.
        """
        # Normalize name
        name = name.lower()

        version = None

        if isinstance(spec, str):
            # Simple string version
            if spec != "*":
                version_match = re.search(r"[0-9]+\.[0-9.]*", spec)
                if version_match:
                    version = version_match.group(0)

        elif isinstance(spec, dict):
            # Dictionary format (common in Pipfile)
            if "version" in spec:
                version_str = spec["version"]
                if isinstance(version_str, str) and version_str != "*":
                    version_match = re.search(r"[0-9]+\.[0-9.]*", version_str)
                    if version_match:
                        version = version_match.group(0)

        # Build PURL
        if version:
            purl = f"pkg:pypi/{name}@{version}"
        else:
            purl = f"pkg:pypi/{name}"

        return {
            "name": name,
            "version": version,
            "purl": purl,
            "package_type": "pypi",
            "scope": scope,
            "direct": True,
            "source_file": filename,
        }
