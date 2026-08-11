"""FastAPI endpoint parser for detecting REST API routes.

Implements parsing of FastAPI route decorators and APIRouter routes
to extract API endpoint information including paths, HTTP methods, and
authentication requirements.
"""

# flake8: noqa: E501

import re
from typing import Any, Dict, List

from ..base import BaseDependencyParser


class FastAPIEndpointParser(BaseDependencyParser):
    """Parser for FastAPI endpoints.

    Detects FastAPI routes defined using decorators (@app.get, @app.post, etc.)
    and APIRouter route registration (@router.get, @router.post, etc.).

    Supports:
    - @app.get('/path'), @app.post('/path'), etc.
    - @router.get('/path'), @router.post('/path'), etc.
    - @app.api_route('/path', methods=['GET', 'POST'])
    - @router.api_route('/path', methods=['GET'])

    Detects authentication dependencies like Depends() patterns.
    FastAPI already uses OpenAPI path format: /users/{id}
    """

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the given file.

        Args:
            filename: Name of the file to check.

        Returns:
            True if the filename is a Python file (.py).
        """
        if not filename:
            return False

        return filename.lower().endswith(".py")

    def get_supported_files(self) -> list[str]:
        """Return list of supported file patterns.

        Returns:
            List containing Python file patterns.
        """
        return ["*.py"]

    def parse(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse Python source code to extract FastAPI endpoints.

        Extracts route information from FastAPI decorators,
        returning structured endpoint information.

        Args:
            content: Raw Python source code.
            filename: Name of the file being parsed.

        Returns:
            List of dictionaries containing endpoint information with fields:
            - path: str - API endpoint path
            - methods: List[str] - HTTP methods (GET, POST, etc.)
            - function_name: str - Decorated function name
            - line_number: int - Line where route is defined
            - framework: str - "fastapi"
            - source_file: str - Filename
            - auth_required: bool - Whether authentication is required

        Raises:
            ValueError: If content is invalid or unparseable.
        """
        if not self.validate_content(content):
            return []

        endpoints: list[dict[str, Any]] = []

        # Parse decorator-style routes
        endpoints.extend(self._parse_decorator_routes(content, filename))

        return endpoints

    def _parse_decorator_routes(
        self, content: str, filename: str
    ) -> list[dict[str, Any]]:
        """Parse FastAPI decorator-style routes.

        Detects patterns like:
        - @app.get('/path'), @app.post('/path')
        - @router.get('/path'), @router.post('/path')
        - @app.api_route('/path', methods=['GET', 'POST'])

        Args:
            content: Python source code.
            filename: Source filename.

        Returns:
            List of endpoint dictionaries.
        """
        endpoints: list[dict[str, Any]] = []
        lines = content.splitlines()

        # Pattern for route decorators
        # Matches: @app.get('/path'), @router.post('/path', ...), @app.api_route('/path', methods=[...])
        route_pattern = re.compile(
            r"@(?:app|router)\.(?:get|post|put|patch|delete|head|options|api_route)"
            r'\s*\(\s*["\']([^"\']+)["\'](?:.*?methods\s*=\s*\[([^\]]+)\])?',
            re.DOTALL,
        )

        # Pattern for Depends() authentication
        depends_pattern = re.compile(r"Depends\s*\(")

        i = 0
        while i < len(lines):
            line = lines[i]

            # Skip lines that are inside string literals (decorated string values)
            # Check if this line starts with a decorator pattern
            if line.strip().startswith("@"):
                # Check for route decorator
                route_match = route_pattern.search(line)
                if route_match:
                    path = route_match.group(1)
                    methods_str = route_match.group(2)

                    # Parse methods list
                    methods = self._parse_methods(methods_str, line)

                    # Check for Depends() in function signature
                    auth_required = self._check_auth_dependencies(
                        lines, i, depends_pattern
                    )

                    # Find the function name
                    function_name = self._find_function_name(lines, i)

                    endpoint = {
                        "path": path,
                        "methods": methods,
                        "function_name": function_name,
                        "line_number": i + 1,
                        "framework": "fastapi",
                        "source_file": filename,
                        "auth_required": auth_required,
                    }
                    endpoints.append(endpoint)

            i += 1

        return endpoints

    def _parse_methods(self, methods_str: str, full_line: str) -> list[str]:
        """Parse HTTP methods from decorator or shortcut.

        Args:
            methods_str: String containing methods list or None.
            full_line: Full decorator line for detecting shortcuts.

        Returns:
            List of HTTP method strings.
        """
        # Check for method-specific decorators (@app.get, @router.post, etc.)
        shortcut_match = re.search(
            r"@(?:app|router)\.(get|post|put|patch|delete|head|options)", full_line
        )
        if shortcut_match:
            method = shortcut_match.group(1).upper()
            return [method]

        # Parse methods list from api_route
        if methods_str:
            # Extract quoted strings from methods list
            method_matches = re.findall(r'["\']([A-Z]+)["\']', methods_str)
            if method_matches:
                return method_matches

        # Default to GET if no methods specified
        return ["GET"]

    def _check_auth_dependencies(
        self, lines: list[str], route_line_idx: int, depends_pattern: re.Pattern
    ) -> bool:
        """Check for Depends() authentication in function signature.

        Args:
            lines: All source lines.
            route_line_idx: Index of route decorator line.
            depends_pattern: Compiled regex pattern for Depends().

        Returns:
            True if Depends() found in function signature, False otherwise.
        """
        # Look for function signature and check for Depends()
        end_idx = min(len(lines), route_line_idx + 15)
        for i in range(route_line_idx, end_idx):
            line = lines[i]
            # Check if we found the function signature
            if "def " in line:
                # Check this line and following lines for Depends()
                # (function signature might span multiple lines)
                for j in range(i, min(len(lines), i + 10)):
                    if depends_pattern.search(lines[j]):
                        return True
                    # Stop if we hit the function body
                    if lines[j].strip().endswith(":"):
                        break
                break
        return False

    def _find_function_name(self, lines: list[str], route_line_idx: int) -> str:
        """Find the function name following the route decorator.

        Args:
            lines: All source lines.
            route_line_idx: Index of route decorator line.

        Returns:
            Function name or 'unknown' if not found.
        """
        # Look for "def function_name(" or "async def function_name(" in following lines
        func_pattern = re.compile(r"(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")

        end_idx = min(len(lines), route_line_idx + 10)
        for i in range(route_line_idx, end_idx):
            match = func_pattern.search(lines[i])
            if match:
                return match.group(1)

        return "unknown"
