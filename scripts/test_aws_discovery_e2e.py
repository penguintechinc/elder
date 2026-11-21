#!/usr/bin/env python3
"""
End-to-end test for Elder AWS Discovery API.

This script tests both static credentials and OIDC authentication methods
through the Elder Discovery API.

Usage:
    python scripts/test_aws_discovery_e2e.py
"""

import json
import os
import sys
import time

import requests

# Configuration
API_BASE_URL = os.environ.get("ELDER_API_URL", "http://localhost:4000")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin@localhost.local")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# AWS credentials from environment — REQUIRED, never hardcoded.
# (A real key pair was previously committed here; it must be considered
# compromised and rotated in AWS IAM — removing it from the file does not
# remove it from git history.)
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
    sys.exit(
        "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set in the "
        "environment to run this e2e script."
    )
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-2")
AWS_ROLE_ARN = os.environ.get("AWS_ROLE_ARN", "")
AWS_WEB_IDENTITY_TOKEN_FILE = os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE", "")


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    END = "\033[0m"


def print_header(msg):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{msg}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.END}")


def print_success(msg):
    print(f"{Colors.GREEN}✓ {msg}{Colors.END}")


def print_error(msg):
    print(f"{Colors.RED}✗ {msg}{Colors.END}")


def print_info(msg):
    print(f"{Colors.BLUE}ℹ {msg}{Colors.END}")


def login():
    """Login to Elder API and get access token."""
    print_info(f"Logging in as {ADMIN_USERNAME}...")

    response = requests.post(
        f"{API_BASE_URL}/api/v1/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        headers={"Content-Type": "application/json"},
    )

    if response.status_code != 200:
        print_error(f"Login failed: {response.status_code}")
        print_error(response.text)
        sys.exit(1)

    data = response.json()
    print_success(f"Logged in as {data['user']['username']}")
    return data["access_token"]


def create_discovery_job(token, name, config, description):
    """Create a discovery job."""
    print_info(f"Creating discovery job: {name}")

    payload = {
        "name": name,
        "provider": "aws",
        "config": config,
        "organization_id": 1,
        "schedule_interval": 0,  # One-time job
        "description": description,
    }

    response = requests.post(
        f"{API_BASE_URL}/api/v1/discovery/jobs",
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    if response.status_code not in [200, 201]:
        print_error(f"Failed to create job: {response.status_code}")
        print_error(response.text)
        return None

    job = response.json()
    print_success(f"Job created with ID: {job.get('id')}")
    return job


def test_job_connectivity(token, job_id):
    """Test job connectivity."""
    print_info(f"Testing job {job_id} connectivity...")

    response = requests.post(
        f"{API_BASE_URL}/api/v1/discovery/jobs/{job_id}/test",
        headers={"Authorization": f"Bearer {token}"},
    )

    result = response.json()

    if response.status_code == 200 and result.get("success"):
        print_success("Connectivity test passed")
        if "identity" in result:
            print_info(f"  Account: {result['identity'].get('account')}")
            print_info(f"  ARN: {result['identity'].get('arn')}")
        if "auth_method" in result:
            print_info(f"  Auth method: {result['auth_method']}")
        return True
    else:
        print_error(f"Connectivity test failed: {result.get('error', 'Unknown error')}")
        return False


def run_discovery(token, job_id):
    """Run the discovery job."""
    print_info(f"Running discovery job {job_id}...")

    response = requests.post(
        f"{API_BASE_URL}/api/v1/discovery/jobs/{job_id}/run",
        headers={"Authorization": f"Bearer {token}"},
    )

    result = response.json()

    if response.status_code in [200, 202] and result.get("success"):
        print_success(
            f"Discovery completed: {result.get('resources_discovered', 0)} resources"
        )
        if result.get("discovery_time"):
            print_info(f"Discovery time: {result.get('discovery_time')}")
        return result
    elif response.status_code in [200, 202, 500]:
        # Discovery may have run but encountered an error
        if result.get("error"):
            print_error(f"Discovery error: {result.get('error')}")
        return result
    else:
        print_error(f"Failed to run job: {response.status_code}")
        print_error(response.text)
        return None


def get_job_history(token, job_id):
    """Get job execution history."""
    response = requests.get(
        f"{API_BASE_URL}/api/v1/discovery/jobs/{job_id}/history",
        headers={"Authorization": f"Bearer {token}"},
    )

    if response.status_code == 200:
        return response.json().get("history", [])
    return []


def wait_for_job_completion(token, job_id, timeout=120):
    """Wait for job to complete."""
    print_info(f"Waiting for job {job_id} to complete (timeout: {timeout}s)...")

    start_time = time.time()
    while time.time() - start_time < timeout:
        # Check job status
        response = requests.get(
            f"{API_BASE_URL}/api/v1/discovery/jobs/{job_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        if response.status_code == 200:
            job = response.json()
            status = job.get("status", "unknown")

            if status == "completed":
                print_success("Job completed successfully")
                return job
            elif status == "failed":
                print_error(f"Job failed: {job.get('last_error', 'Unknown error')}")
                return job
            elif status == "running":
                print(f"  Status: {status}...", end="\r")

        time.sleep(2)

    print_error("Job timed out")
    return None


def delete_job(token, job_id):
    """Delete a discovery job."""
    response = requests.delete(
        f"{API_BASE_URL}/api/v1/discovery/jobs/{job_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    if response.status_code == 200:
        print_success(f"Job {job_id} deleted")
        return True
    return False


def test_static_credentials(token):
    """Test AWS discovery with static credentials."""
    print_header("Test 1: Static Credentials Authentication")

    config = {
        "region": AWS_REGION,
        "access_key_id": AWS_ACCESS_KEY_ID,
        "secret_access_key": AWS_SECRET_ACCESS_KEY,
        "services": ["ec2", "s3", "vpc", "iam"],  # Include IAM to test Identity mapping
    }

    # Create job
    job = create_discovery_job(
        token,
        name="E2E Test - Static Credentials",
        config=config,
        description="End-to-end test with static AWS credentials",
    )

    if not job:
        return False

    job_id = job.get("id")

    try:
        # Test connectivity
        if not test_job_connectivity(token, job_id):
            return False

        # Run discovery (synchronous - completes immediately)
        result = run_discovery(token, job_id)
        if not result:
            return False

        # Discovery is synchronous, check result directly
        if result.get("success"):
            print_success("Discovery completed successfully!")
            print_info(f"Resources discovered: {result.get('resources_discovered', 0)}")
            return True
        else:
            print_error(f"Discovery failed: {result.get('error', 'Unknown error')}")
            return False

    finally:
        # Cleanup
        print_info("Cleaning up...")
        delete_job(token, job_id)


def test_oidc_credentials(token):
    """Test AWS discovery with OIDC credentials."""
    print_header("Test 2: OIDC / Web Identity Authentication")

    if not AWS_ROLE_ARN:
        print_info("AWS_ROLE_ARN not set, skipping OIDC test")
        print_info("To test OIDC, set AWS_ROLE_ARN and AWS_WEB_IDENTITY_TOKEN_FILE")
        return None  # Skip, not fail

    config = {
        "region": AWS_REGION,
        "role_arn": AWS_ROLE_ARN,
        "services": ["ec2", "s3", "vpc", "iam"],
    }

    if AWS_WEB_IDENTITY_TOKEN_FILE:
        config["web_identity_token_file"] = AWS_WEB_IDENTITY_TOKEN_FILE

    # Create job
    job = create_discovery_job(
        token,
        name="E2E Test - OIDC Credentials",
        config=config,
        description="End-to-end test with OIDC/Web Identity",
    )

    if not job:
        return False

    job_id = job.get("id")

    try:
        # Test connectivity
        if not test_job_connectivity(token, job_id):
            return False

        # Run discovery
        result = run_discovery(token, job_id)
        if not result:
            return False

        # Wait for completion
        final_job = wait_for_job_completion(token, job_id)

        if final_job and final_job.get("status") == "completed":
            if "last_results" in final_job:
                results = final_job["last_results"]
                print_info(f"Resources discovered: {results.get('resources_count', 0)}")
            return True

        return False

    finally:
        # Cleanup
        print_info("Cleaning up...")
        delete_job(token, job_id)


def test_environment_credentials(token):
    """Test AWS discovery using environment credentials (no explicit creds)."""
    print_header("Test 3: Environment / IAM Role Authentication")

    config = {
        "region": AWS_REGION,
        "services": ["ec2", "s3", "vpc"],
        # No explicit credentials - will use environment
    }

    # Create job
    job = create_discovery_job(
        token,
        name="E2E Test - Environment Credentials",
        config=config,
        description="End-to-end test using environment credentials",
    )

    if not job:
        return False

    job_id = job.get("id")

    try:
        # Test connectivity - this will use whatever creds are in the container
        connectivity_result = test_job_connectivity(token, job_id)

        if connectivity_result:
            print_success("Environment credentials detected and working")
        else:
            print_info(
                "No environment credentials available (expected in isolated containers)"
            )

        return True  # This test is informational

    finally:
        # Cleanup
        print_info("Cleaning up...")
        delete_job(token, job_id)


def main():
    print_header("Elder AWS Discovery End-to-End Test")
    print(f"API URL: {API_BASE_URL}")
    print(f"AWS Region: {AWS_REGION}")

    # Login
    token = login()

    results = {}

    # Test 1: Static credentials
    results["static_credentials"] = test_static_credentials(token)

    # Test 2: OIDC credentials
    results["oidc"] = test_oidc_credentials(token)

    # Test 3: Environment credentials (informational)
    results["environment"] = test_environment_credentials(token)

    # Summary
    print_header("Test Summary")

    for test_name, result in results.items():
        if result is True:
            print_success(f"{test_name}: PASSED")
        elif result is False:
            print_error(f"{test_name}: FAILED")
        else:
            print_info(f"{test_name}: SKIPPED")

    # Exit with appropriate code
    failed = [k for k, v in results.items() if v is False]
    if failed:
        print_error(f"\n{len(failed)} test(s) failed")
        sys.exit(1)
    else:
        print_success("\nAll tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
