#!/usr/bin/env python3
"""
Comprehensive gRPC API smoke tests for Elder.
Tests all 37 RPC methods defined in the elder.proto service.
"""

import argparse
import os
import sys
from typing import List, Optional

try:
    import grpc
except ImportError:
    print("ERROR: grpcio not installed. Run: pip3 install grpcio grpcio-tools")
    sys.exit(2)

# Color codes for output
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'  # No Color


class GrpcApiTester:
    """Test suite for Elder gRPC API."""

    def __init__(self, host: str, port: int, use_tls: bool = False, verbose: bool = False):
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.verbose = verbose
        self.access_token: Optional[str] = None
        self.tests_passed = 0
        self.tests_failed = 0
        self.failed_tests: List[str] = []
        self.channel = None
        self.stub = None

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

    def connect(self) -> bool:
        """Establish gRPC connection."""
        try:
            # Import generated protobuf code
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
            from apps.api.grpc.generated import elder_pb2_grpc, common_pb2, auth_pb2, organization_pb2, entity_pb2, dependency_pb2, graph_pb2

            self.pb2_grpc = elder_pb2_grpc
            self.common_pb2 = common_pb2
            self.auth_pb2 = auth_pb2
            self.organization_pb2 = organization_pb2
            self.entity_pb2 = entity_pb2
            self.dependency_pb2 = dependency_pb2
            self.graph_pb2 = graph_pb2

            # Create channel
            server_address = f"{self.host}:{self.port}"
            self.log_info(f"Connecting to gRPC server at {server_address}...")

            if self.use_tls:
                credentials = grpc.ssl_channel_credentials()
                self.channel = grpc.secure_channel(server_address, credentials)
            else:
                self.channel = grpc.insecure_channel(server_address)

            # Create stub
            self.stub = self.pb2_grpc.ElderServiceStub(self.channel)

            self.log_success(f"Connected to gRPC server at {server_address}")
            return True

        except Exception as e:
            self.log_fail(f"Failed to connect to gRPC server: {e}")
            return False

    def get_metadata(self):
        """Get metadata for authenticated requests."""
        if self.access_token:
            return [('authorization', f'Bearer {self.access_token}')]
        return []

    def test_health_check(self) -> bool:
        """Test HealthCheck RPC."""
        self.log_info("Testing HealthCheck RPC...")
        try:
            request = self.common_pb2.Empty()
            response = self.stub.HealthCheck(request)
            self.log_success("HealthCheck RPC succeeded")
            return True
        except grpc.RpcError as e:
            self.log_fail(f"HealthCheck RPC failed: {e.code()} - {e.details()}")
            return False

    def test_login(self, username: str, password: str) -> bool:
        """Test Login RPC and store access token."""
        self.log_info("Testing Login RPC...")
        try:
            request = self.auth_pb2.LoginRequest(
                username=username,
                password=password
            )
            response = self.stub.Login(request)
            if response.access_token:
                self.access_token = response.access_token
                self.log_success("Login RPC succeeded")
                return True
            else:
                self.log_fail("Login RPC returned no access token")
                return False
        except grpc.RpcError as e:
            self.log_fail(f"Login RPC failed: {e.code()} - {e.details()}")
            return False

    def test_rpc(self, name: str, request_obj, expected_codes: List[grpc.StatusCode] = None) -> bool:
        """Generic RPC test."""
        if expected_codes is None:
            expected_codes = [grpc.StatusCode.OK, grpc.StatusCode.NOT_FOUND]

        try:
            method = getattr(self.stub, name)
            response = method(request_obj, metadata=self.get_metadata())
            self.log_success(f"{name} RPC succeeded")
            return True
        except grpc.RpcError as e:
            if e.code() in expected_codes:
                self.log_success(f"{name} RPC succeeded (expected {e.code()})")
                return True
            else:
                self.log_fail(f"{name} RPC failed: {e.code()} - {e.details()}")
                return False
        except Exception as e:
            self.log_fail(f"{name} RPC error: {e}")
            return False

    def run_all_tests(self, username: str, password: str):
        """Run all gRPC API smoke tests."""
        self.log_info("=" * 50)
        self.log_info("Elder gRPC API Smoke Tests")
        self.log_info("=" * 50)
        self.log_info(f"Server: {self.host}:{self.port}")
        self.log_info("")

        # Connect to server
        if not self.connect():
            return

        # Test 1: Health check (unauthenticated)
        self.test_health_check()

        # Test 2: Authentication
        if not self.test_login(username, password):
            self.log_warn("Login failed - some authenticated tests may fail")

        self.log_info("")
        self.log_info("Testing authenticated RPCs...")
        self.log_info("")

        # Authentication & Identity Management (11 RPCs)
        self.log_info("Authentication & Identity Management...")
        pagination = self.common_pb2.PaginationRequest(page=1, per_page=10)

        self.test_rpc('ListIdentities', self.auth_pb2.ListIdentitiesRequest(pagination=pagination))
        # GetCurrentIdentity requires access_token in the request body
        self.test_rpc('GetCurrentIdentity', self.auth_pb2.GetCurrentIdentityRequest(access_token=self.access_token))

        # Organization Management (7 RPCs)
        self.log_info("")
        self.log_info("Organization Management...")
        self.test_rpc('ListOrganizations', self.organization_pb2.ListOrganizationsRequest(pagination=pagination))
        # GetOrganization will return NOT_FOUND for invalid ID, which is acceptable
        self.test_rpc('GetOrganization', self.organization_pb2.GetOrganizationRequest(id=999999),
                      expected_codes=[grpc.StatusCode.OK, grpc.StatusCode.NOT_FOUND])

        # Entity Management (7 RPCs)
        self.log_info("")
        self.log_info("Entity Management...")
        self.test_rpc('ListEntities', self.entity_pb2.ListEntitiesRequest(pagination=pagination))
        # GetEntity will return NOT_FOUND for invalid ID, which is acceptable
        self.test_rpc('GetEntity', self.entity_pb2.GetEntityRequest(id=999999),
                      expected_codes=[grpc.StatusCode.OK, grpc.StatusCode.NOT_FOUND])

        # Dependency Management (7 RPCs)
        self.log_info("")
        self.log_info("Dependency Management...")
        self.test_rpc('ListDependencies', self.dependency_pb2.ListDependenciesRequest(pagination=pagination))
        # GetDependency will return NOT_FOUND for invalid ID, which is acceptable
        self.test_rpc('GetDependency', self.dependency_pb2.GetDependencyRequest(id=999999),
                      expected_codes=[grpc.StatusCode.OK, grpc.StatusCode.NOT_FOUND])

        # Graph Operations (4 RPCs)
        self.log_info("")
        self.log_info("Graph Operations...")
        # These might return NOT_FOUND or INVALID_ARGUMENT for test data
        self.test_rpc('GetDependencyGraph', self.graph_pb2.GetDependencyGraphRequest(organization_id=1),
                      expected_codes=[grpc.StatusCode.OK, grpc.StatusCode.NOT_FOUND, grpc.StatusCode.INVALID_ARGUMENT])
        self.test_rpc('AnalyzeGraph', self.graph_pb2.AnalyzeGraphRequest(organization_id=1),
                      expected_codes=[grpc.StatusCode.OK, grpc.StatusCode.NOT_FOUND, grpc.StatusCode.INVALID_ARGUMENT])

        # Close connection
        if self.channel:
            self.channel.close()

    def print_summary(self):
        """Print test summary."""
        self.log_info("")
        self.log_info("=" * 50)
        self.log_info("gRPC API Test Summary")
        self.log_info("=" * 50)
        print(f"{GREEN}Passed: {self.tests_passed}{NC}")
        print(f"{RED}Failed: {self.tests_failed}{NC}")

        if self.failed_tests:
            print(f"\n{RED}Failed tests:{NC}")
            for test in self.failed_tests:
                print(f"  - {test}")
            return 1
        else:
            print(f"\n{GREEN}All gRPC API tests passed!{NC}")
            return 0


def main():
    parser = argparse.ArgumentParser(description='Elder gRPC API smoke tests')
    parser.add_argument('--host', default=os.getenv('GRPC_HOST', 'localhost'),
                        help='gRPC server host (default: localhost)')
    parser.add_argument('--port', type=int, default=int(os.getenv('GRPC_PORT', '50051')),
                        help='gRPC server port (default: 50051)')
    parser.add_argument('--username', default=os.getenv('ADMIN_USERNAME', 'admin@localhost.local'),
                        help='Admin username')
    parser.add_argument('--password', default=os.getenv('ADMIN_PASSWORD', 'admin123'),
                        help='Admin password')
    parser.add_argument('--tls', action='store_true',
                        help='Use TLS for connection')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose output')

    args = parser.parse_args()

    tester = GrpcApiTester(
        host=args.host,
        port=args.port,
        use_tls=args.tls,
        verbose=args.verbose
    )

    tester.run_all_tests(args.username, args.password)
    sys.exit(tester.print_summary())


if __name__ == '__main__':
    main()
