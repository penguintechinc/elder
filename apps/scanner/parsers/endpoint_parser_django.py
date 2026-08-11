"""Django endpoint parser for SBOM service.

Detects Django URL patterns from Python source code including:
- path() and re_path() patterns
- Legacy url() patterns
- Django REST Framework router registrations
"""

# flake8: noqa: E501

import re
from typing import Dict, List


class DjangoEndpointParser:
    """Parser for Django URL patterns."""

    # Regex patterns for Django URL definitions
    PATH_PATTERN = re.compile(
        r"path\(['\"]([^'\"]+)['\"],\s*([^,\)]+)(?:,\s*name=['\"]([^'\"]+)['\"])?\)",
        re.MULTILINE,
    )
    RE_PATH_PATTERN = re.compile(
        r"re_path\(r['\"]([^'\"]+)['\"],\s*([^,\)]+)(?:,\s*name=['\"]([^'\"]+)['\"])?\)",
        re.MULTILINE,
    )
    URL_PATTERN = re.compile(
        r"url\(r['\"]([^'\"]+)['\"],\s*([^,\)]+)(?:,\s*name=['\"]([^'\"]+)['\"])?\)",
        re.MULTILINE,
    )
    ROUTER_PATTERN = re.compile(
        r"router\.register\(r?['\"]([^'\"]+)['\"],\s*([^,\)]+)", re.MULTILINE
    )

    # Path converter mapping
    PATH_CONVERTERS = {
        "int": r"\d+",
        "str": r"[^/]+",
        "slug": r"[-a-zA-Z0-9_]+",
        "uuid": r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "path": r".+",
    }

    def can_parse(self, filename: str) -> bool:
        """Check if file can be parsed (Python files).

        Args:
            filename: Name of the file to check

        Returns:
            True if file is a Python file
        """
        return filename.endswith(".py")

    def parse(self, content: str, filename: str) -> list[dict]:
        """Parse Django URL patterns from content.

        Args:
            content: File content to parse
            filename: Name of the source file

        Returns:
            List of endpoint dictionaries
        """
        endpoints = []

        # Parse path() patterns
        for match in self.PATH_PATTERN.finditer(content):
            path, view_name, url_name = match.groups()
            line_number = content[: match.start()].count("\n") + 1

            endpoints.append(
                {
                    "path": self._normalize_path(path),
                    "methods": self._infer_methods(view_name),
                    "view_name": view_name.strip(),
                    "line_number": line_number,
                    "framework": "django",
                    "source_file": filename,
                    "url_name": url_name or None,
                }
            )

        # Parse re_path() patterns
        for match in self.RE_PATH_PATTERN.finditer(content):
            path, view_name, url_name = match.groups()
            line_number = content[: match.start()].count("\n") + 1

            endpoints.append(
                {
                    "path": self._normalize_regex_path(path),
                    "methods": self._infer_methods(view_name),
                    "view_name": view_name.strip(),
                    "line_number": line_number,
                    "framework": "django",
                    "source_file": filename,
                    "url_name": url_name or None,
                }
            )

        # Parse legacy url() patterns
        for match in self.URL_PATTERN.finditer(content):
            path, view_name, url_name = match.groups()
            line_number = content[: match.start()].count("\n") + 1

            endpoints.append(
                {
                    "path": self._normalize_regex_path(path),
                    "methods": self._infer_methods(view_name),
                    "view_name": view_name.strip(),
                    "line_number": line_number,
                    "framework": "django",
                    "source_file": filename,
                    "url_name": url_name or None,
                }
            )

        # Parse DRF router registrations
        for match in self.ROUTER_PATTERN.finditer(content):
            prefix, viewset_name = match.groups()
            line_number = content[: match.start()].count("\n") + 1

            # ViewSets get all CRUD methods
            endpoints.append(
                {
                    "path": f"/{prefix.strip('/')}/",
                    "methods": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    "view_name": viewset_name.strip(),
                    "line_number": line_number,
                    "framework": "django",
                    "source_file": filename,
                    "url_name": None,
                }
            )

        return endpoints

    def _normalize_path(self, path: str) -> str:
        """Normalize Django path() pattern to standard format.

        Converts path converters like <int:pk> to {pk}.

        Args:
            path: Raw path pattern

        Returns:
            Normalized path
        """
        # Convert <int:pk> to {pk}
        normalized = re.sub(r"<(?:[^:>]+):([^>]+)>", r"{\1}", path)

        # Ensure leading slash
        if not normalized.startswith("/"):
            normalized = "/" + normalized

        return normalized

    def _normalize_regex_path(self, path: str) -> str:
        """Normalize regex path pattern to standard format.

        Converts regex patterns to path parameter format.

        Args:
            path: Raw regex pattern

        Returns:
            Normalized path
        """
        # Remove regex anchors
        normalized = path.strip("^$")

        # Convert named groups (?P<name>...) to {name}
        normalized = re.sub(r"\(\?P<([^>]+)>[^)]+\)", r"{\1}", normalized)

        # Convert unnamed groups to generic parameter
        normalized = re.sub(r"\([^)]+\)", "{param}", normalized)

        # Remove common regex patterns
        normalized = normalized.replace(r"\d+", "{id}")
        normalized = normalized.replace(r"[^/]+", "{param}")

        # Ensure leading slash
        if not normalized.startswith("/"):
            normalized = "/" + normalized

        return normalized

    def _infer_methods(self, view_name: str) -> list[str]:
        """Infer HTTP methods from view name.

        Args:
            view_name: Name of the view function/class

        Returns:
            List of HTTP methods
        """
        view_lower = view_name.lower()

        # ViewSets get all methods
        if "viewset" in view_lower:
            return ["GET", "POST", "PUT", "PATCH", "DELETE"]

        # Infer from common naming patterns
        if "list" in view_lower or "index" in view_lower:
            return ["GET"]
        elif "create" in view_lower:
            return ["POST"]
        elif "update" in view_lower or "edit" in view_lower:
            return ["PUT", "PATCH"]
        elif "delete" in view_lower or "destroy" in view_lower:
            return ["DELETE"]
        elif "detail" in view_lower or "retrieve" in view_lower:
            return ["GET"]

        # Default to common methods
        return ["GET", "POST"]
