"""SBOM scanner for analyzing git repositories and extracting dependencies."""

# flake8: noqa: E501

import asyncio
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from parsers.endpoint_parser_django import DjangoEndpointParser
from parsers.endpoint_parser_express import ExpressEndpointParser
from parsers.endpoint_parser_flask import FlaskEndpointParser
from parsers.endpoint_parser_go import GoEndpointParser

from .base import BaseScanner

logger = logging.getLogger("scanner.sbom")


class SBOMScanner(BaseScanner):
    """SBOM scanner that clones git repos and extracts dependencies using parsers."""

    # Mapping of file patterns to their package types
    DEPENDENCY_FILES = {
        # Python
        "requirements.txt": "python",
        "requirements-*.txt": "python",
        "pyproject.toml": "python",
        "Pipfile": "python",
        "Pipfile.lock": "python",
        "setup.py": "python",
        "setup.cfg": "python",
        "poetry.lock": "python",
        # Node.js
        "package.json": "node",
        "package-lock.json": "node",
        "yarn.lock": "node",
        "pnpm-lock.yaml": "node",
        # Go
        "go.mod": "go",
        "go.sum": "go",
        # Rust
        "Cargo.toml": "rust",
        "Cargo.lock": "rust",
        # Java
        "pom.xml": "java",
        "build.gradle": "java",
        "build.gradle.kts": "java",
        "gradle.lockfile": "java",
        # .NET
        "*.csproj": "dotnet",
        "*.fsproj": "dotnet",
        "packages.config": "dotnet",
        "Directory.Packages.props": "dotnet",
        # Ruby
        "Gemfile": "ruby",
        "Gemfile.lock": "ruby",
    }

    async def scan(self, config: dict[str, Any]) -> dict[str, Any]:
        """Execute SBOM scan on a git repository.

        Config schema:
        {
            "repository_url": "https://github.com/user/repo",  # Required
            "repository_branch": "main",                       # Optional, default: main
            "api_url": "http://api:5000",                      # Required for parser API
            "scan_id": 123,                                    # Scan job ID
        }

        Returns:
            {
                "success": true,
                "components": [
                    {
                        "name": "flask",
                        "version": "2.3.0",
                        "purl": "pkg:pypi/flask@2.3.0",
                        "package_type": "pypi",
                        "scope": "runtime",
                        "direct": true,
                        "source_file": "requirements.txt"
                    }
                ],
                "files_scanned": ["requirements.txt", "package.json"],
                "commit_hash": "abc123...",
                "scan_duration_ms": 5432
            }
        """
        self.validate_config(config, ["repository_url", "api_url", "scan_id"])

        start_time = time.time()
        repository_url = config["repository_url"]
        repository_branch = config.get("repository_branch", "main")
        api_url = config["api_url"].rstrip("/")
        token = config.get("_resolved_token")

        # Create temp directory for clone
        temp_dir = tempfile.mkdtemp(prefix="sbom_scan_")

        try:
            logger.info(
                f"Cloning repository {repository_url} (branch: {repository_branch})"
            )

            # Clone repository (shallow for speed)
            clone_result = await self._clone_repository(
                repository_url, repository_branch, temp_dir, token
            )

            if not clone_result["success"]:
                return {
                    "success": False,
                    "error": clone_result.get("error", "Clone failed"),
                    "scan_duration_ms": int((time.time() - start_time) * 1000),
                    "endpoints": [],
                    "endpoints_found": 0,
                }

            commit_hash = clone_result.get("commit_hash")

            # Find all dependency files
            logger.info(f"Scanning for dependency files in {temp_dir}")
            dependency_files = self._find_dependency_files(temp_dir)

            if not dependency_files:
                logger.warning("No dependency files found")
                # Still try to detect endpoints even if no dependencies found
                endpoints = self._detect_endpoints(temp_dir)
                return {
                    "success": True,
                    "components": [],
                    "files_scanned": [],
                    "commit_hash": commit_hash,
                    "scan_duration_ms": int((time.time() - start_time) * 1000),
                    "endpoints": endpoints,
                    "endpoints_found": len(endpoints),
                }

            logger.info(f"Found {len(dependency_files)} dependency file(s)")

            # Parse each file using API parsers
            all_components = []
            files_scanned = []

            for dep_file in dependency_files:
                logger.info(f"Parsing {dep_file['filename']}")

                # Read file content
                try:
                    with open(dep_file["path"], encoding="utf-8") as f:
                        content = f.read()
                except Exception as e:
                    logger.error(f"Failed to read {dep_file['path']}: {e}")
                    continue

                # Parse using API
                components = await self._parse_file_via_api(
                    api_url, dep_file["filename"], content, dep_file["ecosystem"]
                )

                if components:
                    all_components.extend(components)
                    files_scanned.append(dep_file["filename"])

            # Deduplicate components by (name, version)
            unique_components = {}
            for comp in all_components:
                key = (comp["name"], comp.get("version"))
                if key not in unique_components:
                    unique_components[key] = comp

            # Detect API endpoints
            endpoints = self._detect_endpoints(temp_dir)

            scan_duration_ms = int((time.time() - start_time) * 1000)

            logger.info(
                f"Scan complete: {len(unique_components)} components from {len(files_scanned)} files, {len(endpoints)} endpoint(s)"
            )

            return {
                "success": True,
                "components": list(unique_components.values()),
                "files_scanned": files_scanned,
                "commit_hash": commit_hash,
                "scan_duration_ms": scan_duration_ms,
                "endpoints": endpoints,
                "endpoints_found": len(endpoints),
            }

        except Exception as e:
            logger.error(f"SBOM scan failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "scan_duration_ms": int((time.time() - start_time) * 1000),
                "endpoints": [],
                "endpoints_found": 0,
            }

        finally:
            # Cleanup temp directory
            if os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"Failed to cleanup temp dir {temp_dir}: {e}")

    async def _clone_repository(
        self, repo_url: str, branch: str, dest_dir: str, token: str | None = None
    ) -> dict[str, Any]:
        """Clone git repository using subprocess.

        Returns:
            {"success": true, "commit_hash": "abc123"}
            or
            {"success": false, "error": "message"}
        """
        # Build authenticated URL if token provided
        clone_url = self._build_authenticated_url(repo_url, token)
        safe_url = self._sanitize_url_for_logging(clone_url)

        cmd = [
            "git",
            "clone",
            "--depth=1",  # Shallow clone for speed
            "--branch",
            branch,
            "--single-branch",
            clone_url,
            dest_dir,
        ]

        try:
            logger.info(f"Cloning repository from {safe_url}")

            # Run git clone
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace")
                logger.error(f"Git clone failed for {safe_url}: {error_msg}")
                return {"success": False, "error": f"Git clone failed: {error_msg}"}

            # Get commit hash
            commit_hash = await self._get_commit_hash(dest_dir)

            logger.info(f"Successfully cloned {safe_url}")
            return {"success": True, "commit_hash": commit_hash}

        except Exception as e:
            logger.error(f"Git clone exception for {safe_url}: {e}")
            return {"success": False, "error": str(e)}

    async def _get_commit_hash(self, repo_dir: str) -> str | None:
        """Get current commit hash from cloned repository."""
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                repo_dir,
                "rev-parse",
                "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                return stdout.decode("utf-8").strip()

        except Exception as e:
            logger.warning(f"Failed to get commit hash: {e}")

        return None

    def _build_authenticated_url(self, repo_url: str, token: str | None) -> str:
        """Build authenticated git URL with token for private repositories.

        Args:
            repo_url: Original repository URL
            token: Authentication token (optional)

        Returns:
            Authenticated URL with embedded token, or original URL if no token

        Examples:
            GitHub: https://x-access-token:TOKEN@github.com/user/repo.git
            GitLab: https://oauth2:TOKEN@gitlab.com/user/repo.git
            Bitbucket: https://x-token-auth:TOKEN@bitbucket.org/user/repo.git
            Azure DevOps: https://TOKEN@dev.azure.com/org/project/_git/repo
        """
        if not token:
            return repo_url

        parsed = urlparse(repo_url)
        scheme = parsed.scheme
        netloc = parsed.netloc
        path = parsed.path

        # Remove any existing credentials
        if "@" in netloc:
            netloc = netloc.split("@")[-1]

        # Build authenticated URL based on git hosting provider
        hostname = netloc.lower()

        if "github.com" in hostname:
            # GitHub: x-access-token:TOKEN
            auth_netloc = f"x-access-token:{token}@{netloc}"
        elif "gitlab" in hostname:
            # GitLab (any host containing "gitlab"): oauth2:TOKEN
            auth_netloc = f"oauth2:{token}@{netloc}"
        elif "bitbucket.org" in hostname:
            # Bitbucket: x-token-auth:TOKEN
            auth_netloc = f"x-token-auth:{token}@{netloc}"
        elif "dev.azure.com" in hostname or "visualstudio.com" in hostname:
            # Azure DevOps: TOKEN@ (no username prefix)
            auth_netloc = f"{token}@{netloc}"
        else:
            # Generic: TOKEN@
            auth_netloc = f"{token}@{netloc}"

        return urlunparse((scheme, auth_netloc, path, "", "", ""))

    def _sanitize_url_for_logging(self, url: str) -> str:
        """Remove credentials from URL for safe logging.

        Args:
            url: URL potentially containing credentials

        Returns:
            URL with credentials removed

        Example:
            https://token@github.com/repo.git -> https://github.com/repo.git
        """
        parsed = urlparse(url)
        netloc = parsed.netloc

        # Remove credentials if present
        if "@" in netloc:
            netloc = netloc.split("@")[-1]

        return urlunparse(
            (
                parsed.scheme,
                netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

    def _find_dependency_files(self, root_dir: str) -> list[dict[str, Any]]:
        """Recursively find all dependency files in directory.

        Returns:
            [
                {
                    "path": "/path/to/requirements.txt",
                    "filename": "requirements.txt",
                    "ecosystem": "python"
                }
            ]
        """
        found_files = []
        root_path = Path(root_dir)

        for pattern, ecosystem in self.DEPENDENCY_FILES.items():
            # Handle wildcard patterns
            if "*" in pattern:
                # Convert to glob pattern
                glob_pattern = f"**/{pattern}"
                matches = root_path.glob(glob_pattern)
            else:
                # Exact filename match
                glob_pattern = f"**/{pattern}"
                matches = root_path.glob(glob_pattern)

            for match in matches:
                # Skip hidden directories and common exclusions
                parts = match.parts
                if any(
                    p.startswith(".")
                    or p
                    in ["node_modules", "vendor", ".git", "__pycache__", "venv", "env"]
                    for p in parts
                ):
                    continue

                found_files.append(
                    {
                        "path": str(match),
                        "filename": match.name,
                        "ecosystem": ecosystem,
                    }
                )

        return found_files

    async def _parse_file_via_api(
        self, api_url: str, filename: str, content: str, ecosystem: str
    ) -> list[dict[str, Any]]:
        """Parse dependency file by calling Elder API parser endpoint.

        This delegates parsing to the API's parser service which has all
        the ecosystem-specific parsers available.

        Note: In a real implementation, this would make an HTTP request to
        an API endpoint that invokes the appropriate parser. For now, we'll
        import and use parsers directly.
        """
        try:
            # Import parsers dynamically
            from apps.api.services.sbom.parsers import (
                DotnetParser,
                GoParser,
                JavaDependencyParser,
                NodeDependencyParser,
                PythonDependencyParser,
                RustDependencyParser,
            )

            # Map ecosystems to parser classes
            parser_map = {
                "python": PythonDependencyParser,
                "node": NodeDependencyParser,
                "go": GoParser,
                "rust": RustDependencyParser,
                "java": JavaDependencyParser,
                "dotnet": DotnetParser,
            }

            parser_class = parser_map.get(ecosystem)
            if not parser_class:
                logger.warning(f"No parser available for ecosystem: {ecosystem}")
                return []

            parser = parser_class()

            if not parser.can_parse(filename):
                logger.warning(f"Parser cannot handle file: {filename}")
                return []

            components = parser.parse(content, filename)
            return components

        except Exception as e:
            logger.error(f"Parser failed for {filename}: {e}")
            return []

    def _detect_endpoints(self, repo_dir: str) -> list[dict]:
        """Detect API endpoints in the cloned repository.

        Args:
            repo_dir: Path to cloned repository

        Returns:
            List of endpoint dictionaries with path, methods, framework, etc.
        """
        endpoints = []

        try:
            # Initialize endpoint parsers
            parsers = [
                FlaskEndpointParser(),
                DjangoEndpointParser(),
                ExpressEndpointParser(),
                GoEndpointParser(),
            ]

            # Walk through repository and scan Python/JavaScript/Go files
            repo_path = Path(repo_dir)

            for file_path in repo_path.rglob("*"):
                # Skip hidden directories and common exclusions
                if any(
                    p.startswith(".")
                    or p
                    in ["node_modules", "vendor", ".git", "__pycache__", "venv", "env"]
                    for p in file_path.parts
                ):
                    continue

                # Skip non-files
                if not file_path.is_file():
                    continue

                filename = file_path.name

                # Try each parser
                for parser in parsers:
                    if parser.can_parse(filename):
                        try:
                            # Read file content
                            with open(
                                file_path, encoding="utf-8", errors="ignore"
                            ) as f:
                                content = f.read()

                            # Parse endpoints
                            file_endpoints = parser.parse(
                                content, str(file_path.relative_to(repo_path))
                            )

                            if file_endpoints:
                                endpoints.extend(file_endpoints)
                                logger.info(
                                    f"Found {len(file_endpoints)} endpoint(s) in {filename}"
                                )

                        except Exception as e:
                            logger.warning(
                                f"Failed to parse endpoints from {file_path}: {e}"
                            )

                        # Only one parser per file
                        break

            logger.info(f"Total endpoints detected: {len(endpoints)}")

        except Exception as e:
            logger.warning(f"Endpoint detection failed: {e}")

        return endpoints
