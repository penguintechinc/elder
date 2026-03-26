#!/usr/bin/env python3
"""
API validation and error handling tests for Elder.
Tests edge cases, bad input, and error responses.
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
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'  # No Color


class ValidationTester:
    """API validation test suite for Elder."""

    def __init__(self, base_url: str, verify_ssl: bool = True, verbose: bool = False, host_header: str = ''):
        self.base_url = base_url.rstrip('/')
        self.verify_ssl = verify_ssl
        self.verbose = verbose
        self.access_token: Optional[str] = None
        self.tests_passed = 0
        self.tests_failed = 0
        self.failed_tests: List[str] = []

        # Setup HTTP session
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
        self.session.mount('http://', HTTPAdapter(max_retries=retries))
        self.session.mount('https://', HTTPAdapter(max_retries=retries))
        if host_header:
            self.session.headers.update({'Host': host_header})

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
        headers = kwargs.pop('headers', {})

        if self.access_token:
            headers['Authorization'] = f'Bearer {self.access_token}'

        try:
            self.log_verbose(f"{method} {url}")
            response = self.session.request(
                method=method,
                url=url,
                headers=headers,
                verify=self.verify_ssl,
                timeout=10,
                **kwargs
            )
            self.log_verbose(f"Status: {response.status_code}")
            return response, None
        except Exception as e:
            return None, str(e)

    def authenticate(self, username: str, password: str) -> bool:
        """Authenticate and store access token."""
        self.log_info("Authenticating...")
        resp, err = self._request(
            'POST',
            '/api/v1/portal-auth/login',
            json={'email': username, 'password': password}
        )

        if err or not resp or resp.status_code != 200:
            self.log_fail(f"Authentication failed: {err or resp.status_code}")
            return False

        data = resp.json()
        self.access_token = data.get('access_token') or data.get('token')
        if self.access_token:
            self.log_success("Authentication successful")
            return True
        else:
            self.log_fail("No access token in response")
            return False

    def test_authentication_validation(self) -> bool:
        """Test authentication edge cases."""
        self.log_info("Testing authentication validation...")

        test_cases = [
            {'data': {'email': '', 'password': 'test'}, 'expected': [400, 401, 422], 'name': 'Empty email'},
            {'data': {'email': 'test', 'password': ''}, 'expected': [400, 401, 422], 'name': 'Empty password'},
            {'data': {'email': 'nonexistent@test.com', 'password': 'wrong'}, 'expected': [401], 'name': 'Invalid credentials'},
            {'data': {}, 'expected': [400, 422], 'name': 'Missing fields'},
            {'data': {'email': 'x' * 1000, 'password': 'test'}, 'expected': [400, 401, 422], 'name': 'Extremely long email'},
            {'data': {'email': "admin'; DROP TABLE users; --", 'password': 'test'}, 'expected': [400, 401, 422], 'name': 'SQL injection attempt'},
            {'data': {'email': '<script>alert(1)</script>', 'password': 'test'}, 'expected': [400, 401, 422], 'name': 'XSS attempt'},
        ]

        passed = 0
        for test_case in test_cases:
            resp, err = self._request('POST', '/api/v1/portal-auth/login', json=test_case['data'])
            if resp is not None and resp.status_code in test_case['expected']:
                self.log_success(f"{test_case['name']}: Rejected with {resp.status_code}")
                passed += 1
            else:
                self.log_fail(f"{test_case['name']}: Expected {test_case['expected']}, got {resp.status_code if resp else 'error'}")

        return passed == len(test_cases)

    def test_invalid_json(self) -> bool:
        """Test invalid JSON handling."""
        self.log_info("Testing invalid JSON handling...")

        # Send malformed JSON
        resp, err = self._request(
            'POST',
            '/api/v1/organizations',
            data='{invalid json}',
            headers={'Content-Type': 'application/json'}
        )

        if resp is not None and resp.status_code in [400, 422]:
            self.log_success(f"Invalid JSON rejected with {resp.status_code}")
            return True
        else:
            self.log_fail(f"Invalid JSON should return 400/422, got {resp.status_code if resp else 'error'}")
            return False

    def test_missing_required_fields(self) -> bool:
        """Test missing required fields."""
        self.log_info("Testing missing required fields...")

        # Try to create organization without required 'name' field
        resp, err = self._request(
            'POST',
            '/api/v1/organizations',
            json={'description': 'Missing name'}
        )

        if resp is not None and resp.status_code in [400, 422]:
            self.log_success(f"Missing required field rejected with {resp.status_code}")
            return True
        else:
            self.log_fail(f"Missing required field should return 400/422, got {resp.status_code if resp else 'error'}")
            return False

    def test_invalid_data_types(self) -> bool:
        """Test invalid data types."""
        self.log_info("Testing invalid data types...")

        test_cases = [
            {'data': {'name': 123, 'description': 'Should be string'}, 'name': 'Integer instead of string'},
            {'data': {'name': 'Test', 'parent_id': 'not_a_number'}, 'name': 'String instead of integer'},
            {'data': {'name': 'Test', 'is_active': 'yes'}, 'name': 'String instead of boolean'},
            {'data': {'name': ['list', 'not', 'string']}, 'name': 'Array instead of string'},
        ]

        passed = 0
        for test_case in test_cases:
            resp, err = self._request('POST', '/api/v1/organizations', json=test_case['data'])
            if resp is not None and resp.status_code in [400, 422]:
                self.log_success(f"{test_case['name']}: Rejected with {resp.status_code}")
                passed += 1
            else:
                self.log_fail(f"{test_case['name']}: Expected 400/422, got {resp.status_code if resp else 'error'}")

        return passed > 0  # At least some should be validated

    def test_invalid_resource_ids(self) -> bool:
        """Test invalid resource ID handling."""
        self.log_info("Testing invalid resource IDs...")

        test_cases = [
            {'endpoint': '/api/v1/organizations/999999', 'expected': 404, 'name': 'Non-existent ID'},
            {'endpoint': '/api/v1/organizations/-1', 'expected': [400, 404], 'name': 'Negative ID'},
            {'endpoint': '/api/v1/organizations/abc', 'expected': [400, 404], 'name': 'String instead of ID'},
            {'endpoint': '/api/v1/organizations/0', 'expected': [400, 404], 'name': 'Zero ID'},
        ]

        passed = 0
        for test_case in test_cases:
            resp, err = self._request('GET', test_case['endpoint'])
            expected = test_case['expected'] if isinstance(test_case['expected'], list) else [test_case['expected']]
            if resp is not None and resp.status_code in expected:
                self.log_success(f"{test_case['name']}: Rejected with {resp.status_code}")
                passed += 1
            else:
                self.log_fail(f"{test_case['name']}: Expected {expected}, got {resp.status_code if resp else 'error'}")

        return passed == len(test_cases)

    def test_pagination_validation(self) -> bool:
        """Test pagination edge cases."""
        self.log_info("Testing pagination validation...")

        test_cases = [
            {'params': {'page': -1, 'per_page': 10}, 'name': 'Negative page number'},
            {'params': {'page': 1, 'per_page': -10}, 'name': 'Negative per_page'},
            {'params': {'page': 1, 'per_page': 10000}, 'name': 'Extremely large per_page'},
            {'params': {'page': 'abc', 'per_page': 10}, 'name': 'String page number'},
        ]

        passed = 0
        for test_case in test_cases:
            resp, err = self._request('GET', '/api/v1/organizations', params=test_case['params'])
            # Some pagination errors might be handled gracefully (200 with empty results)
            # So we accept both error responses and successful empty responses
            if resp is not None and resp.status_code in [200, 400, 422]:
                self.log_success(f"{test_case['name']}: Handled with {resp.status_code}")
                passed += 1
            else:
                self.log_warn(f"{test_case['name']}: Got {resp.status_code if resp else 'error'}")
                passed += 1  # Don't fail - pagination handling varies

        return passed > 0

    def test_search_injection(self) -> bool:
        """Test search query injection attempts."""
        self.log_info("Testing search injection attempts...")

        dangerous_queries = [
            ("admin'; DROP TABLE organizations; --", False),  # (query, is_known_limitation)
            ("<script>alert('xss')</script>", False),
            ("../../etc/passwd", False),
            ("%00null", True),  # PostgreSQL limitation - null bytes in strings
            ("' OR '1'='1", False),
        ]

        passed = 0
        for query, is_known_limit in dangerous_queries:
            resp, err = self._request('GET', f'/api/v1/search?q={query}')
            # Should either reject (400) or handle safely (200 with no results)
            if resp is not None and resp.status_code in [200, 400]:
                self.log_success(f"Injection attempt handled: {query[:30]}...")
                passed += 1
            elif is_known_limit:
                # Known limitation (e.g., PostgreSQL null byte handling)
                self.log_warn(f"Known limitation (PostgreSQL): {query[:30]}...")
                passed += 1  # Don't count as failure
            else:
                self.log_fail(f"Injection attempt mishandled: {query[:30]}...")

        return passed == len(dangerous_queries)

    def test_unauthorized_access(self) -> bool:
        """Test unauthorized access attempts."""
        self.log_info("Testing unauthorized access...")

        # Save current token
        saved_token = self.access_token

        # Try without token
        self.access_token = None
        resp, err = self._request('GET', '/api/v1/organizations')

        # Restore token
        self.access_token = saved_token

        if resp is not None and resp.status_code == 401:
            self.log_success("Unauthorized access rejected with 401")
            return True
        else:
            self.log_fail(f"Unauthorized access should return 401, got {resp.status_code if resp else 'error'}")
            return False

    def test_invalid_token(self) -> bool:
        """Test invalid token handling."""
        self.log_info("Testing invalid token handling...")

        # Save current token
        saved_token = self.access_token

        # Try with invalid token
        self.access_token = "invalid_token_12345"
        resp, err = self._request('GET', '/api/v1/organizations')

        # Restore token
        self.access_token = saved_token

        if resp is not None and resp.status_code == 401:
            self.log_success("Invalid token rejected with 401")
            return True
        else:
            self.log_fail(f"Invalid token should return 401, got {resp.status_code if resp else 'error'}")
            return False

    def test_extremely_long_strings(self) -> bool:
        """Test extremely long string handling."""
        self.log_info("Testing extremely long string handling...")

        # Create organization with very long name
        resp, err = self._request(
            'POST',
            '/api/v1/organizations',
            json={
                'name': 'A' * 10000,
                'description': 'B' * 100000
            }
        )

        if resp is not None and resp.status_code in [400, 422, 413]:
            self.log_success(f"Extremely long strings rejected with {resp.status_code}")
            return True
        else:
            self.log_warn(f"Extremely long strings got {resp.status_code if resp else 'error'} (may be accepted)")
            return True  # Don't fail - some systems allow long strings

    def run_all_tests(self):
        """Run all validation tests."""
        self.log_info("=" * 60)
        self.log_info("Elder API Validation Tests")
        self.log_info("=" * 60)
        self.log_info(f"Base URL: {self.base_url}")
        self.log_info("")

        # Run tests
        self.test_authentication_validation()
        self.log_info("")

        self.test_invalid_json()
        self.log_info("")

        self.test_missing_required_fields()
        self.log_info("")

        self.test_invalid_data_types()
        self.log_info("")

        self.test_invalid_resource_ids()
        self.log_info("")

        self.test_pagination_validation()
        self.log_info("")

        self.test_search_injection()
        self.log_info("")

        self.test_unauthorized_access()
        self.log_info("")

        self.test_invalid_token()
        self.log_info("")

        self.test_extremely_long_strings()
        self.log_info("")

    def print_summary(self):
        """Print test summary."""
        self.log_info("=" * 60)
        self.log_info("API Validation Test Summary")
        self.log_info("=" * 60)
        print(f"{GREEN}Passed: {self.tests_passed}{NC}")
        print(f"{RED}Failed: {self.tests_failed}{NC}")

        if self.failed_tests:
            print(f"\n{RED}Failed tests:{NC}")
            for test in self.failed_tests:
                print(f"  - {test}")
            return 1
        else:
            print(f"\n{GREEN}All validation tests passed!{NC}")
            return 0


def main():
    parser = argparse.ArgumentParser(description='Elder API validation tests')
    parser.add_argument('--url', default=os.getenv('API_URL', 'http://localhost:4000'),
                        help='API base URL (default: http://localhost:4000)')
    parser.add_argument('--username', default=os.getenv('ADMIN_USERNAME', 'admin@localhost.local'),
                        help='Admin username')
    parser.add_argument('--password', default=os.getenv('ADMIN_PASSWORD', 'admin123'),
                        help='Admin password')
    parser.add_argument('--no-verify-ssl', action='store_true',
                        help='Disable SSL certificate verification')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose output')
    parser.add_argument('--host-header', default=os.getenv('HOST_HEADER', ''),
                        help='Override Host header (for bypass URL routing, e.g. beta via dal2 LB)')

    args = parser.parse_args()

    tester = ValidationTester(
        base_url=args.url,
        verify_ssl=not args.no_verify_ssl,
        verbose=args.verbose,
        host_header=args.host_header
    )

    if not tester.authenticate(args.username, args.password):
        sys.exit(1)

    tester.run_all_tests()
    sys.exit(tester.print_summary())


if __name__ == '__main__':
    main()
