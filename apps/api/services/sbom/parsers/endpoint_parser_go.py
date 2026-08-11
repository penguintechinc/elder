"""Go HTTP endpoint parser for detecting routes in Go source code.

Provides parsing capabilities for Go HTTP routes from various frameworks,
including Gin, Chi, Gorilla Mux, and net/http standard library.

Supports:
- Gin: r.GET(), r.POST(), router.Group()
- Chi: r.Get(), r.Post(), r.Route()
- Gorilla Mux: r.HandleFunc().Methods()
- net/http: http.HandleFunc(), mux.Handle()
"""

# flake8: noqa: E501

import re
from typing import Any, Dict, List


class GoEndpointParser:
    """Parser for Go HTTP endpoints from various frameworks.

    Extracts endpoint information from Go source files, supporting multiple
    HTTP routing frameworks and patterns. Normalizes path parameters to
    consistent {param} format.

    Attributes:
        _gin_pattern: Regex pattern for Gin routes
        _chi_pattern: Regex pattern for Chi routes
        _gorilla_pattern: Regex pattern for Gorilla Mux routes
        _nethttp_pattern: Regex pattern for net/http routes
        _group_pattern: Regex pattern for route groups
        _middleware_pattern: Regex pattern for middleware detection
    """

    def __init__(self) -> None:
        """Initialize Go endpoint parser with regex patterns."""
        # Gin framework patterns: r.GET("/path", handler)
        self._gin_pattern = re.compile(
            r"(?:r|router|engine)\."
            r"(?P<method>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|Any)\s*\(\s*"
            r'["\'](?P<path>[^"\']+)["\']\s*,\s*'
            r"(?P<handler>\w+)",
            re.MULTILINE,
        )

        # Chi framework patterns: r.Get("/path", handler)
        self._chi_pattern = re.compile(
            r"(?:r|router)\."
            r"(?P<method>Get|Post|Put|Delete|Patch|Head|Options)\s*\(\s*"
            r'["\'](?P<path>[^"\']+)["\']\s*,\s*'
            r"(?P<handler>\w+)",
            re.MULTILINE,
        )

        # Gorilla Mux patterns: r.HandleFunc("/path", handler).Methods("GET")
        self._gorilla_pattern = re.compile(
            r"(?:r|router)\."
            r"HandleFunc\s*\(\s*"
            r'["\'](?P<path>[^"\']+)["\']\s*,\s*'
            r"(?P<handler>\w+)\s*\)"
            r"(?:\.Methods\s*\(\s*"
            r'["\'](?P<methods>[^"\']+)["\']'
            r"(?:\s*,\s*[\"'][^\"']+[\"'])*\s*\))?",
            re.MULTILINE,
        )

        # net/http patterns: http.HandleFunc("/path", handler)
        self._nethttp_pattern = re.compile(
            r"http\.HandleFunc\s*\(\s*"
            r'["\'](?P<path>[^"\']+)["\']\s*,\s*'
            r"(?P<handler>\w+)",
            re.MULTILINE,
        )

        # Route group patterns: router.Group("/api"), r.Route("/api", func...)
        self._gin_group_pattern = re.compile(
            r"(?:r|router|engine)\.Group\s*\(\s*" r'["\'](?P<path>[^"\']+)["\']',
            re.MULTILINE,
        )

        self._chi_route_pattern = re.compile(
            r"(?:r|router)\.Route\s*\(\s*"
            r'["\'](?P<path>[^"\']+)["\']\s*,\s*'
            r"func\s*\(\s*(?P<subrouter>\w+)\s+chi\.Router\s*\)",
            re.MULTILINE,
        )

        # Middleware detection: r.Use(middleware)
        self._middleware_pattern = re.compile(
            r"(?:r|router|engine)\.Use\s*\(\s*(?P<middleware>\w+)", re.MULTILINE
        )

    def can_parse(self, filename: str) -> bool:
        """Check if this parser can handle the given file.

        Args:
            filename: Name of the file to check.

        Returns:
            True if filename ends with '.go', False otherwise.
        """
        return filename.endswith(".go")

    def parse(self, content: str, filename: str) -> list[dict[str, Any]]:
        """Parse Go source file and extract HTTP endpoint information.

        Args:
            content: Raw file content as a string.
            filename: Name of the file being parsed.

        Returns:
            List of dictionaries containing endpoint information with fields:
            - path: URL path (str)
            - methods: HTTP methods (List[str])
            - handler_name: Handler function name (str)
            - line_number: Line number in source file (int)
            - framework: Framework name (str)
            - source_file: Source filename (str)
            - middleware: Middleware list (List[str])
        """
        endpoints: list[dict[str, Any]] = []

        # Detect middleware
        middleware = self._extract_middleware(content)

        # Parse Gin routes
        endpoints.extend(self._parse_gin_routes(content, filename, middleware))

        # Parse Chi routes
        endpoints.extend(self._parse_chi_routes(content, filename, middleware))

        # Parse Gorilla Mux routes
        endpoints.extend(self._parse_gorilla_routes(content, filename, middleware))

        # Parse net/http routes
        endpoints.extend(self._parse_nethttp_routes(content, filename, middleware))

        return endpoints

    def _extract_middleware(self, content: str) -> list[str]:
        """Extract middleware from Go source code.

        Args:
            content: Raw file content.

        Returns:
            List of middleware function names.
        """
        middleware = []
        for match in self._middleware_pattern.finditer(content):
            middleware.append(match.group("middleware"))
        return middleware

    def _parse_gin_routes(
        self, content: str, filename: str, middleware: list[str]
    ) -> list[dict[str, Any]]:
        """Parse Gin framework routes.

        Args:
            content: Raw file content.
            filename: Source filename.
            middleware: Global middleware list.

        Returns:
            List of endpoint dictionaries.
        """
        endpoints = []
        for match in self._gin_pattern.finditer(content):
            method = match.group("method").upper()
            path = self._normalize_path_params(match.group("path"), "gin")
            handler = match.group("handler")
            line_number = content[: match.start()].count("\n") + 1

            # Handle Any method (all HTTP methods)
            methods = (
                ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]
                if method == "ANY"
                else [method]
            )

            endpoints.append(
                {
                    "path": path,
                    "methods": methods,
                    "handler_name": handler,
                    "line_number": line_number,
                    "framework": "gin",
                    "source_file": filename,
                    "middleware": middleware.copy(),
                }
            )
        return endpoints

    def _parse_chi_routes(
        self, content: str, filename: str, middleware: list[str]
    ) -> list[dict[str, Any]]:
        """Parse Chi framework routes.

        Args:
            content: Raw file content.
            filename: Source filename.
            middleware: Global middleware list.

        Returns:
            List of endpoint dictionaries.
        """
        endpoints = []
        for match in self._chi_pattern.finditer(content):
            method = match.group("method").upper()
            path = self._normalize_path_params(match.group("path"), "chi")
            handler = match.group("handler")
            line_number = content[: match.start()].count("\n") + 1

            endpoints.append(
                {
                    "path": path,
                    "methods": [method],
                    "handler_name": handler,
                    "line_number": line_number,
                    "framework": "chi",
                    "source_file": filename,
                    "middleware": middleware.copy(),
                }
            )
        return endpoints

    def _parse_gorilla_routes(
        self, content: str, filename: str, middleware: list[str]
    ) -> list[dict[str, Any]]:
        """Parse Gorilla Mux routes.

        Args:
            content: Raw file content.
            filename: Source filename.
            middleware: Global middleware list.

        Returns:
            List of endpoint dictionaries.
        """
        endpoints = []
        for match in self._gorilla_pattern.finditer(content):
            path = self._normalize_path_params(match.group("path"), "gorilla")
            handler = match.group("handler")
            line_number = content[: match.start()].count("\n") + 1

            # Extract methods if specified, otherwise default to all methods
            methods_str = match.group("methods")
            if methods_str:
                methods = [m.strip().upper() for m in methods_str.split(",")]
            else:
                methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]

            endpoints.append(
                {
                    "path": path,
                    "methods": methods,
                    "handler_name": handler,
                    "line_number": line_number,
                    "framework": "gorilla",
                    "source_file": filename,
                    "middleware": middleware.copy(),
                }
            )
        return endpoints

    def _parse_nethttp_routes(
        self, content: str, filename: str, middleware: list[str]
    ) -> list[dict[str, Any]]:
        """Parse net/http standard library routes.

        Args:
            content: Raw file content.
            filename: Source filename.
            middleware: Global middleware list.

        Returns:
            List of endpoint dictionaries.
        """
        endpoints = []
        for match in self._nethttp_pattern.finditer(content):
            path = match.group("path")
            handler = match.group("handler")
            line_number = content[: match.start()].count("\n") + 1

            # net/http handlers accept all methods by default
            methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]

            endpoints.append(
                {
                    "path": path,
                    "methods": methods,
                    "handler_name": handler,
                    "line_number": line_number,
                    "framework": "net/http",
                    "source_file": filename,
                    "middleware": middleware.copy(),
                }
            )
        return endpoints

    def _normalize_path_params(self, path: str, framework: str) -> str:
        """Normalize path parameters to consistent {param} format.

        Args:
            path: Original path string.
            framework: Framework name (gin, chi, gorilla, net/http).

        Returns:
            Normalized path with {param} style parameters.
        """
        if framework in ("gin", "chi"):
            # Convert :param to {param}
            path = re.sub(r":(\w+)", r"{\1}", path)
        # Gorilla already uses {param} format
        return path
