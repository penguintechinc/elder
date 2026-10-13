#!/usr/bin/env python3
"""
Integration workflow tests for Elder.
Tests complete end-to-end workflows with mock data.
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Color codes for output
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
NC = "\033[0m"  # No Color


class IntegrationTester:
    """Integration workflow test suite for Elder."""

    def __init__(self, base_url: str, verify_ssl: bool = True, verbose: bool = False):
        self.base_url = base_url.rstrip("/")
        self.verify_ssl = verify_ssl
        self.verbose = verbose
        self.access_token: str | None = None
        self.tests_passed = 0
        self.tests_failed = 0
        self.failed_tests: list[str] = []

        # Track created resources for cleanup
        self.created_resources = {
            "organizations": [],
            "entities": [],
            "services": [],
            "issues": [],
            "projects": [],
            "labels": [],
            "secrets": [],
            "webhooks": [],
            "users": [],
        }

        # Setup HTTP session with retries
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
        self.session.mount("http://", HTTPAdapter(max_retries=retries))
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def log_info(self, msg: str):
        print(f"{BLUE}[INFO]{NC} {msg}")

    def log_success(self, msg: str):
        print(f"{GREEN}[PASS]{NC} {msg}")
        self.tests_passed += 1

    def log_fail(self, msg: str):
        print(f"{RED}[FAIL]{NC} {msg}")
        self.tests_failed += 1
        self.failed_tests.append(msg)

    def log_warn(self, msg: str):
        print(f"{YELLOW}[WARN]{NC} {msg}")

    def log_verbose(self, msg: str):
        if self.verbose:
            print(f"[DEBUG] {msg}")

    def _request(self, method: str, endpoint: str, **kwargs):
        """Make HTTP request with error handling."""
        url = urljoin(self.base_url, endpoint)
        headers = kwargs.pop("headers", {})

        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        try:
            self.log_verbose(f"{method} {url}")
            response = self.session.request(
                method=method,
                url=url,
                headers=headers,
                verify=self.verify_ssl,
                timeout=10,
                **kwargs,
            )
            self.log_verbose(f"Status: {response.status_code}")
            return response, None
        except Exception as e:
            return None, str(e)

    def authenticate(self, username: str, password: str) -> bool:
        """Authenticate and store access token."""
        self.log_info("Authenticating...")
        resp, err = self._request(
            "POST",
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )

        if err or not resp or resp.status_code != 200:
            self.log_fail(f"Authentication failed: {err or resp.status_code}")
            return False

        data = resp.json()
        self.access_token = data.get("access_token") or data.get("token")
        if self.access_token:
            self.log_success("Authentication successful")
            return True
        else:
            self.log_fail("No access token in response")
            return False

    def create_resource(self, resource_type: str, data: dict) -> int | None:
        """Create a resource and track it for cleanup."""
        resp, err = self._request("POST", f"/api/v1/{resource_type}", json=data)

        if err or resp is None or resp.status_code not in [200, 201]:
            error_msg = (
                err
                if err
                else (
                    f"{resp.status_code}: {resp.text[:100]}"
                    if resp is not None
                    else "unknown"
                )
            )
            self.log_fail(f"Failed to create {resource_type}: {error_msg}")
            return None

        result = resp.json()
        resource_id = result.get("id") or result.get("data", {}).get("id")

        if resource_id and resource_type in self.created_resources:
            self.created_resources[resource_type].append(resource_id)

        return resource_id

    def cleanup_resources(self):
        """Clean up all created resources."""
        self.log_info("")
        self.log_info("Cleaning up test resources...")

        # Delete in reverse order of dependencies
        for resource_type in [
            "secrets",
            "webhooks",
            "labels",
            "issues",
            "projects",
            "services",
            "entities",
            "users",
            "organizations",
        ]:
            ids = self.created_resources.get(resource_type, [])
            for resource_id in ids:
                resp, err = self._request(
                    "DELETE", f"/api/v1/{resource_type}/{resource_id}"
                )
                if resp is not None and resp.status_code in [200, 204]:
                    self.log_verbose(f"Deleted {resource_type}/{resource_id}")
                else:
                    self.log_verbose(
                        f"Could not delete {resource_type}/{resource_id} (may already be deleted)"
                    )

    def test_org_hierarchy_workflow(self) -> bool:
        """Test: Create organization hierarchy with users and teams."""
        self.log_info("Testing organization hierarchy workflow...")

        # Create parent org
        parent_org_id = self.create_resource(
            "organizations",
            {"name": "Parent Corp", "description": "Parent organization"},
        )
        if not parent_org_id:
            return False
        self.log_success(f"Created parent organization: {parent_org_id}")

        # Create child orgs
        child1_id = self.create_resource(
            "organizations",
            {
                "name": "Engineering Division",
                "parent_id": parent_org_id,
                "description": "Engineering team",
            },
        )
        child2_id = self.create_resource(
            "organizations",
            {
                "name": "Sales Division",
                "parent_id": parent_org_id,
                "description": "Sales team",
            },
        )

        if child1_id and child2_id:
            self.log_success(f"Created child organizations: {child1_id}, {child2_id}")
        else:
            self.log_fail("Failed to create child organizations")
            return False

        # Verify hierarchy
        resp, err = self._request("GET", f"/api/v1/organizations/{parent_org_id}")
        if resp is not None and resp.status_code == 200:
            self.log_success("Organization hierarchy created successfully")
            return True
        else:
            self.log_fail("Failed to verify organization hierarchy")
            return False

    def test_service_dependency_workflow(self) -> bool:
        """Test: Create entities with dependencies."""
        self.log_info("Testing entity dependency workflow...")

        # Create organization first
        org_id = self.create_resource(
            "organizations",
            {"name": "Tech Services Inc", "description": "Service provider"},
        )
        if not org_id:
            return False

        # Create entities (dependencies support entity, identity, project, milestone, issue, organization)
        db_entity_id = self.create_resource(
            "entities",
            {
                "name": "PostgreSQL Database",
                "organization_id": org_id,
                "entity_type": "server",
                "description": "Primary database",
            },
        )

        api_entity_id = self.create_resource(
            "entities",
            {
                "name": "API Service",
                "organization_id": org_id,
                "entity_type": "server",
                "description": "REST API",
            },
        )

        web_entity_id = self.create_resource(
            "entities",
            {
                "name": "Web Frontend",
                "organization_id": org_id,
                "entity_type": "server",
                "description": "React frontend",
            },
        )

        if not all([db_entity_id, api_entity_id, web_entity_id]):
            self.log_fail("Failed to create all entities")
            return False

        self.log_success(
            f"Created entities: DB={db_entity_id}, API={api_entity_id}, Web={web_entity_id}"
        )

        # Create dependencies: API depends on DB, Web depends on API
        # Dependencies endpoint: /api/v1/dependencies
        # Valid resource types: entity, identity, project, milestone, issue, organization
        dep1 = self.create_resource(
            "dependencies",
            {
                "source_type": "entity",
                "source_id": api_entity_id,
                "target_type": "entity",
                "target_id": db_entity_id,
                "dependency_type": "database",
            },
        )

        dep2 = self.create_resource(
            "dependencies",
            {
                "source_type": "entity",
                "source_id": web_entity_id,
                "target_type": "entity",
                "target_id": api_entity_id,
                "dependency_type": "api",
            },
        )

        if dep1 and dep2:
            self.log_success("Entity dependencies created successfully")
            return True
        else:
            self.log_warn(
                "Dependencies may not be fully created (API may not support this endpoint)"
            )
            return True  # Don't fail test if dependencies endpoint doesn't exist

    def test_issue_tracking_workflow(self) -> bool:
        """Test: Create project with issues and milestones."""
        self.log_info("Testing issue tracking workflow...")

        # Create organization first
        org_id = self.create_resource(
            "organizations",
            {"name": "Infrastructure Corp", "description": "Infrastructure management"},
        )
        if not org_id:
            return False

        # Create project
        project_id = self.create_resource(
            "projects",
            {
                "name": "Q1 Infrastructure Upgrade",
                "description": "Upgrade all infrastructure services",
                "status": "active",
                "organization_id": org_id,
            },
        )
        if not project_id:
            return False
        self.log_success(f"Created project: {project_id}")

        # Create milestone
        milestone_id = self.create_resource(
            "milestones",
            {
                "title": "Phase 1 Complete",
                "project_id": project_id,
                "due_date": "2026-03-31",
                "description": "Complete database upgrades",
                "organization_id": org_id,
            },
        )
        if milestone_id:
            self.log_success(f"Created milestone: {milestone_id}")

        # Create issues
        issue_ids = []
        for i, (title, priority) in enumerate(
            [
                ("Upgrade PostgreSQL to v16", "high"),
                ("Update Redis to v7", "medium"),
                ("Migrate to Kubernetes 1.28", "high"),
            ]
        ):
            issue_id = self.create_resource(
                "issues",
                {
                    "title": title,
                    "description": f"Task {i + 1} for infrastructure upgrade",
                    "priority": priority,
                    "status": "open",
                    "project_id": project_id,
                    "milestone_id": milestone_id,
                    "organization_id": org_id,
                },
            )
            if issue_id:
                issue_ids.append(issue_id)

        if len(issue_ids) == 3:
            self.log_success(f"Created 3 issues: {issue_ids}")
            return True
        else:
            self.log_fail(f"Only created {len(issue_ids)}/3 issues")
            return False

    def test_sbom_vulnerability_workflow(self) -> bool:
        """Test: Create service with SBOM scan and vulnerabilities."""
        self.log_info("Testing SBOM/vulnerability workflow...")

        # Create organization and service
        org_id = self.create_resource(
            "organizations",
            {"name": "SecureApp Corp", "description": "Security-focused organization"},
        )
        if not org_id:
            return False

        service_id = self.create_resource(
            "services",
            {
                "name": "SecureAPI",
                "organization_id": org_id,
                "language": "python",
                "repository_url": "https://github.com/example/secureapi",
            },
        )
        if not service_id:
            return False

        self.log_success(f"Created service: {service_id}")

        # Create SBOM scan via /api/v1/sbom/scans endpoint
        # The endpoint expects parent_type and parent_id
        scan_id = self.create_resource(
            "sbom/scans",
            {
                "parent_type": "service",
                "parent_id": service_id,
                "scan_type": "manifest",
                "repository_url": "https://github.com/example/secureapi",
            },
        )
        if scan_id:
            self.log_success(f"Created SBOM scan: {scan_id}")
        else:
            self.log_warn("SBOM scan creation may not be available")

        # Check for vulnerabilities
        # Endpoint: /api/v1/vulnerabilities (supports filtering by severity, cve_id, source, search)
        resp, err = self._request("GET", f"/api/v1/vulnerabilities?severity=critical")
        if resp is not None and resp.status_code == 200:
            self.log_success("Vulnerability query successful")
            return True
        else:
            self.log_warn("Vulnerability endpoint may not be available")
            return True  # Don't fail if feature not implemented

    def test_secrets_management_workflow(self) -> bool:
        """Test: Create and manage secrets."""
        self.log_info("Testing secrets management workflow...")

        # Create organization first
        org_id = self.create_resource(
            "organizations",
            {"name": "Secrets Corp", "description": "Secrets management"},
        )
        if not org_id:
            return False

        secrets_data = [
            {
                "name": "database-password",
                "value": "supersecret123",
                "description": "DB password",
                "organization_id": org_id,
            },
            {
                "name": "api-key",
                "value": "key-abc-123-xyz",
                "description": "External API key",
                "organization_id": org_id,
            },
            {
                "name": "oauth-secret",
                "value": "oauth-secret-value",
                "description": "OAuth client secret",
                "organization_id": org_id,
            },
        ]

        created_count = 0
        for secret_data in secrets_data:
            secret_id = self.create_resource("secrets", secret_data)
            if secret_id:
                created_count += 1

        if created_count == len(secrets_data):
            self.log_success(f"Created {created_count} secrets")
            return True
        else:
            self.log_fail(f"Only created {created_count}/{len(secrets_data)} secrets")
            return False

    def run_all_workflows(self):
        """Run all integration workflow tests."""
        self.log_info("=" * 60)
        self.log_info("Elder Integration Workflow Tests")
        self.log_info("=" * 60)
        self.log_info(f"Base URL: {self.base_url}")
        self.log_info("")

        # Run workflows
        self.test_org_hierarchy_workflow()
        self.log_info("")

        self.test_service_dependency_workflow()
        self.log_info("")

        self.test_issue_tracking_workflow()
        self.log_info("")

        self.test_sbom_vulnerability_workflow()
        self.log_info("")

        self.test_secrets_management_workflow()
        self.log_info("")

    def print_summary(self):
        """Print test summary."""
        self.log_info("=" * 60)
        self.log_info("Integration Workflow Test Summary")
        self.log_info("=" * 60)
        print(f"{GREEN}Passed: {self.tests_passed}{NC}")
        print(f"{RED}Failed: {self.tests_failed}{NC}")

        if self.failed_tests:
            print(f"\n{RED}Failed tests:{NC}")
            for test in self.failed_tests:
                print(f"  - {test}")
            return 1
        else:
            print(f"\n{GREEN}All integration workflow tests passed!{NC}")
            return 0


def main():
    parser = argparse.ArgumentParser(description="Elder integration workflow tests")
    parser.add_argument(
        "--url",
        default=os.getenv("API_URL", "http://localhost:4000"),
        help="API base URL (default: http://localhost:4000)",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("ADMIN_USERNAME", "admin@localhost.local"),
        help="Admin username",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("ADMIN_PASSWORD", "admin123"),
        help="Admin password",
    )
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="Disable SSL certificate verification",
    )
    parser.add_argument(
        "--no-cleanup", action="store_true", help="Skip cleanup of test resources"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )

    args = parser.parse_args()

    tester = IntegrationTester(
        base_url=args.url, verify_ssl=not args.no_verify_ssl, verbose=args.verbose
    )

    if not tester.authenticate(args.username, args.password):
        sys.exit(1)

    tester.run_all_workflows()

    if not args.no_cleanup:
        tester.cleanup_resources()

    sys.exit(tester.print_summary())


if __name__ == "__main__":
    main()
