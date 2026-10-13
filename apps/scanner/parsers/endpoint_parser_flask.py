"""Flask API endpoint parser for detecting REST API routes.

Implements parsing of Flask route decorators and Flask-RESTful resources
to extract API endpoint information including paths, HTTP methods, and
authentication requirements.
"""

# flake8: noqa: E501

import re
from typing import Any, Dict, List


class FlaskEndpointParser:
    """Parser for Flask API endpoints.

    Detects Flask routes defined using decorators (@app.route, @bp.route)
    and Flask-RESTful resource registration (api.add_resource).

    Supports:
    - @app.route('/path', methods=['GET', 'POST'])
    - @bp.route('/path', methods=['GET'])
    - @blueprint.route('/path')
    - @app.get('/path'), @app.post('/path'), etc. (Flask 2.0+ shortcuts)
    - api.add_resource(ResourceClass, '/path')

    Detects authentication decorators like @login_required, @jwt_required.
    Converts Flask path parameters to OpenAPI format: /users/<int:id> -> /users/{id}
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
        """Parse Python source code to extract Flask API endpoints.

        Extracts route information from Flask decorators and resource registrations,
        returning structured endpoint information.

        Args:
            content: Raw Python source code.
            filename: Name of the file being parsed.

        Returns:
            List of dictionaries containing endpoint information with fields:
            - path: str - API endpoint path
            - methods: List[str] - HTTP methods (GET, POST, etc.)
            - function_name: str - Decorated function or resource class name
            - line_number: int - Line where route is defined
            - framework: str - "flask"
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

        # Parse Flask-RESTful resource registrations
        endpoints.extend(self._parse_restful_resources(content, filename))

        return endpoints

    def _parse_decorator_routes(
        self, content: str, filename: str
    ) -> list[dict[str, Any]]:
        """Parse Flask decorator-style routes.

        Detects patterns like:
        - @app.route('/path', methods=['GET', 'POST'])
        - @bp.route('/path')
        - @app.get('/path'), @app.post('/path')

        Args:
            content: Python source code.
            filename: Source filename.

        Returns:
            List of endpoint dictionaries.
        """
        endpoints: list[dict[str, Any]] = []
        lines = content.splitlines()

        # Pattern for route decorators
        # Matches: @app.route('/path'), @bp.route('/path', methods=[...])
        route_pattern = re.compile(
            r"@(?:app|bp|blueprint)\.(?:route|get|post|put|patch|delete|head|options)"
            r'\s*\(\s*["\']([^"\']+)["\'](?:.*?methods\s*=\s*\[([^\]]+)\])?',
            re.DOTALL,
        )

        # Pattern for authentication decorators
        auth_pattern = re.compile(
            r"@(?:login_required|jwt_required|auth_required|token_required|require_auth)"
        )

        i = 0
        while i < len(lines):
            line = lines[i]

            # Check for route decorator
            route_match = route_pattern.search(line)
            if route_match:
                path = route_match.group(1)
                methods_str = route_match.group(2)

                # Parse methods list
                methods = self._parse_methods(methods_str, line)

                # Check for auth decorators in surrounding lines
                auth_required = self._check_auth_decorators(lines, i, auth_pattern)

                # Find the function name
                function_name = self._find_function_name(lines, i)

                # Normalize path (convert Flask params to OpenAPI format)
                normalized_path = self._normalize_path(path)

                endpoint = {
                    "path": normalized_path,
                    "methods": methods,
                    "function_name": function_name,
                    "line_number": i + 1,
                    "framework": "flask",
                    "source_file": filename,
                    "auth_required": auth_required,
                }
                endpoints.append(endpoint)

            i += 1

        return endpoints

    def _parse_restful_resources(
        self, content: str, filename: str
    ) -> list[dict[str, Any]]:
        """Parse Flask-RESTful resource registrations.

        Detects patterns like:
        - api.add_resource(UserResource, '/users')
        - api.add_resource(UserResource, '/users/<int:id>')

        Args:
            content: Python source code.
            filename: Source filename.

        Returns:
            List of endpoint dictionaries.
        """
        endpoints: list[dict[str, Any]] = []
        lines = content.splitlines()

        # Pattern for api.add_resource calls
        resource_pattern = re.compile(
            r'api\.add_resource\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*["\']([^"\']+)["\']'
        )

        for i, line in enumerate(lines):
            match = resource_pattern.search(line)
            if match:
                class_name = match.group(1)
                path = match.group(2)

                # Flask-RESTful resources typically support all methods
                # We'll default to common REST methods
                methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]

                # Normalize path
                normalized_path = self._normalize_path(path)

                endpoint = {
                    "path": normalized_path,
                    "methods": methods,
                    "function_name": class_name,
                    "line_number": i + 1,
                    "framework": "flask",
                    "source_file": filename,
                    "auth_required": False,  # Cannot easily detect for resources
                }
                endpoints.append(endpoint)

        return endpoints

    def _parse_methods(self, methods_str: str, full_line: str) -> list[str]:
        """Parse HTTP methods from decorator or shortcut.

        Args:
            methods_str: String containing methods list or None.
            full_line: Full decorator line for detecting shortcuts.

        Returns:
            List of HTTP method strings.
        """
        # Check for method-specific shortcuts (@app.get, @app.post, etc.)
        shortcut_match = re.search(
            r"@(?:app|bp|blueprint)\.(get|post|put|patch|delete|head|options)",
            full_line,
        )
        if shortcut_match:
            method = shortcut_match.group(1).upper()
            return [method]

        # Parse methods list
        if methods_str:
            # Extract quoted strings from methods list
            method_matches = re.findall(r'["\']([A-Z]+)["\']', methods_str)
            if method_matches:
                return method_matches

        # Default to GET if no methods specified
        return ["GET"]

    def _check_auth_decorators(
        self, lines: list[str], route_line_idx: int, auth_pattern: re.Pattern
    ) -> bool:
        """Check for authentication decorators above route decorator.

        Args:
            lines: All source lines.
            route_line_idx: Index of route decorator line.
            auth_pattern: Compiled regex pattern for auth decorators.

        Returns:
            True if authentication decorator found, False otherwise.
        """
        # Check up to 5 lines above the route decorator
        start_idx = max(0, route_line_idx - 5)
        for i in range(start_idx, route_line_idx):
            if auth_pattern.search(lines[i]):
                return True
        return False

    def _find_function_name(self, lines: list[str], route_line_idx: int) -> str:
        """Find the function name following the route decorator.

        Args:
            lines: All source lines.
            route_line_idx: Index of route decorator line.

        Returns:
            Function name or 'unknown' if not found.
        """
        # Look for "def function_name(" in following lines (up to 10 lines)
        func_pattern = re.compile(r"def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")

        end_idx = min(len(lines), route_line_idx + 10)
        for i in range(route_line_idx, end_idx):
            match = func_pattern.search(lines[i])
            if match:
                return match.group(1)

        return "unknown"

    def _normalize_path(self, path: str) -> str:
        """Normalize Flask path parameters to OpenAPI format.

        Converts Flask-style path parameters to OpenAPI/Swagger format:
        - /users/<int:id> -> /users/{id}
        - /posts/<string:slug> -> /posts/{slug}
        - /items/<id> -> /items/{id}

        Args:
            path: Flask route path.

        Returns:
            Normalized path with OpenAPI-style parameters.
        """
        # Replace Flask path parameters with OpenAPI format
        # Matches: <int:id>, <string:name>, <id>, etc.
        normalized = re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", path)
        return normalized
