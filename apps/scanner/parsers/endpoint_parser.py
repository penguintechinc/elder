"""Unified endpoint parser that combines all framework parsers.

Provides a unified interface for parsing API endpoints from multiple frameworks
including Flask, FastAPI, Django, Express.js, and Go frameworks.
"""

# flake8: noqa: E501

import os
from typing import Dict, List

from .endpoint_parser_django import DjangoEndpointParser
from .endpoint_parser_express import ExpressEndpointParser
from .endpoint_parser_fastapi import FastAPIEndpointParser
from .endpoint_parser_flask import FlaskEndpointParser
from .endpoint_parser_go import GoEndpointParser


class EndpointParser:
    """Unified parser that combines all framework-specific parsers.

    Automatically detects which parser(s) to use based on file type and
    combines results from all applicable parsers. Handles deduplication
    of endpoints found multiple times.
    """

    def __init__(self):
        """Initialize all framework-specific parsers."""
        self.parsers = [
            FlaskEndpointParser(),
            FastAPIEndpointParser(),
            DjangoEndpointParser(),
            ExpressEndpointParser(),
            GoEndpointParser(),
        ]

    def can_parse(self, filename: str) -> bool:
        """Check if ANY parser can handle the given file.

        Args:
            filename: Name of the file to check.

        Returns:
            True if at least one parser can handle the file.
        """
        return any(parser.can_parse(filename) for parser in self.parsers)

    def parse(self, content: str, filename: str) -> list[dict]:
        """Parse content with all applicable parsers and combine results.

        Tries all parsers that can handle the file type and combines
        their results. Deduplicates endpoints with same path+method.

        Args:
            content: File content to parse.
            filename: Name of the source file.

        Returns:
            Combined list of endpoint dictionaries from all parsers.
        """
        all_endpoints = []
        seen = set()  # Track (path, methods_tuple) for deduplication

        for parser in self.parsers:
            if parser.can_parse(filename):
                try:
                    endpoints = parser.parse(content, filename)

                    # Deduplicate: keep first occurrence of each path+method combo
                    for endpoint in endpoints:
                        path = endpoint.get("path", "")
                        methods = tuple(sorted(endpoint.get("methods", [])))
                        key = (path, methods)

                        if key not in seen:
                            seen.add(key)
                            all_endpoints.append(endpoint)

                except Exception as e:
                    # Log parse errors but continue with other parsers
                    print(
                        f"Warning: Parser {parser.__class__.__name__} failed on {filename}: {e}"
                    )
                    continue

        return all_endpoints

    def parse_directory(self, directory: str) -> list[dict]:
        """Walk directory tree and parse all supported files.

        Recursively scans directory for files with supported extensions
        and parses them to extract all API endpoints.

        Args:
            directory: Root directory to scan.

        Returns:
            Combined list of all endpoints found in directory.
        """
        all_endpoints = []

        for root, _, files in os.walk(directory):
            for filename in files:
                if self.can_parse(filename):
                    filepath = os.path.join(root, filename)

                    try:
                        with open(filepath, encoding="utf-8") as f:
                            content = f.read()

                        endpoints = self.parse(content, filename)
                        all_endpoints.extend(endpoints)

                    except Exception as e:
                        print(f"Warning: Failed to parse {filepath}: {e}")
                        continue

        return all_endpoints

    def get_supported_extensions(self) -> list[str]:
        """Return list of all supported file extensions.

        Returns:
            List of file extensions that can be parsed.
        """
        return [".py", ".js", ".ts", ".go", ".mjs"]
