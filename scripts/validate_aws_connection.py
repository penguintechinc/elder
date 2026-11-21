#!/usr/bin/env python3
"""
AWS Connection Validation Script for Elder.

This script validates AWS connectivity and permissions for the Elder
AWS Discovery service. It tests authentication and optionally performs
a minimal discovery to verify required IAM permissions.

Usage:
    python scripts/validate_aws_connection.py [options]

Options:
    -v, --verbose       Show detailed output
    -d, --discover      Run a minimal discovery test after connection validation
    -r, --region REGION AWS region to test (default: us-east-1)
    --services SERVICES Comma-separated list of services to test discovery
                        (default: ec2,s3,rds,lambda,vpc)

Environment Variables:
    AWS_ACCESS_KEY_ID       AWS access key ID
    AWS_SECRET_ACCESS_KEY   AWS secret access key
    AWS_DEFAULT_REGION      Default AWS region

Examples:
    # Basic connection test
    python scripts/validate_aws_connection.py

    # Verbose connection test with discovery
    python scripts/validate_aws_connection.py -v -d

    # Test specific region and services
    python scripts/validate_aws_connection.py -r us-west-2 --services ec2,s3 -d
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# Color codes for terminal output
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    END = "\033[0m"


def print_header(msg: str) -> None:
    """Print a styled header."""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{msg}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'=' * 60}{Colors.END}")


def print_success(msg: str) -> None:
    """Print a success message."""
    print(f"{Colors.GREEN}✓ {msg}{Colors.END}")


def print_error(msg: str) -> None:
    """Print an error message."""
    print(f"{Colors.RED}✗ {msg}{Colors.END}")


def print_warning(msg: str) -> None:
    """Print a warning message."""
    print(f"{Colors.YELLOW}⚠ {msg}{Colors.END}")


def print_info(msg: str) -> None:
    """Print an info message."""
    print(f"{Colors.BLUE}ℹ {msg}{Colors.END}")


def check_boto3_import() -> tuple[bool, str | None]:
    """Check if boto3 is installed and importable."""
    try:
        import boto3
        import botocore

        return True, boto3.__version__
    except ImportError as e:
        return False, str(e)


def check_credentials_configured() -> dict[str, bool]:
    """Check if AWS credentials are configured via environment variables."""
    return {
        "AWS_ACCESS_KEY_ID": bool(os.environ.get("AWS_ACCESS_KEY_ID")),
        "AWS_SECRET_ACCESS_KEY": bool(os.environ.get("AWS_SECRET_ACCESS_KEY")),
        "AWS_DEFAULT_REGION": bool(os.environ.get("AWS_DEFAULT_REGION")),
        "AWS_SESSION_TOKEN": bool(os.environ.get("AWS_SESSION_TOKEN")),
        "AWS_ROLE_ARN": bool(os.environ.get("AWS_ROLE_ARN")),
        "AWS_WEB_IDENTITY_TOKEN_FILE": bool(
            os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE")
        ),
    }


def detect_auth_method() -> str:
    """Detect which AWS authentication method will be used."""
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return "static_credentials"
    elif os.environ.get("AWS_ROLE_ARN") and os.environ.get(
        "AWS_WEB_IDENTITY_TOKEN_FILE"
    ):
        return "web_identity_oidc"
    elif os.environ.get("AWS_PROFILE"):
        return "aws_profile"
    else:
        return "environment_or_iam_role"


def test_sts_connection(region: str, verbose: bool = False) -> tuple[bool, dict]:
    """
    Test AWS connection using STS get_caller_identity.

    Returns:
        Tuple of (success, identity_info or error_info)
    """
    import boto3
    from botocore.exceptions import (
        BotoCoreError,
        ClientError,
        NoCredentialsError,
        PartialCredentialsError,
    )

    try:
        session = boto3.Session(region_name=region)
        sts = session.client("sts")
        identity = sts.get_caller_identity()

        return True, {
            "account": identity.get("Account"),
            "arn": identity.get("Arn"),
            "user_id": identity.get("UserId"),
        }

    except NoCredentialsError:
        return False, {
            "error": "NoCredentialsError",
            "message": "No AWS credentials found. Set AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY environment variables, or configure "
            "AWS credentials file (~/.aws/credentials).",
        }
    except PartialCredentialsError as e:
        return False, {
            "error": "PartialCredentialsError",
            "message": f"Incomplete credentials: {str(e)}. Ensure both "
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are set.",
        }
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_msg = e.response.get("Error", {}).get("Message", str(e))
        return False, {
            "error": error_code,
            "message": error_msg,
            "hint": get_error_hint(error_code),
        }
    except BotoCoreError as e:
        return False, {
            "error": "BotoCoreError",
            "message": str(e),
        }
    except Exception as e:
        return False, {
            "error": type(e).__name__,
            "message": str(e),
        }


def get_error_hint(error_code: str) -> str:
    """Get helpful hints for common AWS error codes."""
    hints = {
        "InvalidClientTokenId": (
            "The access key ID is invalid. Verify AWS_ACCESS_KEY_ID is correct "
            "and the key is active in IAM."
        ),
        "SignatureDoesNotMatch": (
            "The secret access key is incorrect. Verify AWS_SECRET_ACCESS_KEY "
            "matches the access key ID."
        ),
        "ExpiredToken": (
            "The security token has expired. If using temporary credentials, "
            "refresh them."
        ),
        "AccessDenied": (
            "The credentials are valid but lack permission for sts:GetCallerIdentity. "
            "This is unusual - check IAM policies."
        ),
        "UnrecognizedClientException": (
            "AWS doesn't recognize the credentials. They may have been deactivated "
            "or deleted."
        ),
    }
    return hints.get(error_code, "Check AWS documentation for this error code.")


def test_service_access(
    region: str, services: list[str], verbose: bool = False
) -> dict[str, dict]:
    """
    Test access to specific AWS services.

    Returns:
        Dict mapping service name to result dict with 'success' and 'details' keys.
    """
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    session = boto3.Session(region_name=region)
    results = {}

    service_tests = {
        "ec2": ("ec2", "describe_instances", {}, "EC2 (Compute)"),
        "s3": ("s3", "list_buckets", {}, "S3 (Storage)"),
        "rds": ("rds", "describe_db_instances", {}, "RDS (Database)"),
        "lambda": ("lambda", "list_functions", {}, "Lambda (Serverless)"),
        "vpc": ("ec2", "describe_vpcs", {}, "VPC (Network)"),
        "elb": ("elbv2", "describe_load_balancers", {}, "ELB (Load Balancers)"),
        "ebs": ("ec2", "describe_volumes", {}, "EBS (Block Storage)"),
        "iam": ("iam", "list_users", {}, "IAM (Identity)"),
    }

    for service_key in services:
        if service_key not in service_tests:
            results[service_key] = {
                "success": False,
                "display_name": service_key.upper(),
                "error": f"Unknown service: {service_key}",
            }
            continue

        client_name, method_name, params, display_name = service_tests[service_key]

        try:
            client = session.client(client_name)
            method = getattr(client, method_name)
            response = method(**params)

            # Count resources found
            resource_count = 0
            if service_key == "ec2":
                for res in response.get("Reservations", []):
                    resource_count += len(res.get("Instances", []))
            elif service_key == "s3":
                resource_count = len(response.get("Buckets", []))
            elif service_key == "rds":
                resource_count = len(response.get("DBInstances", []))
            elif service_key == "lambda":
                resource_count = len(response.get("Functions", []))
            elif service_key == "vpc":
                resource_count = len(response.get("Vpcs", []))
            elif service_key == "elb":
                resource_count = len(response.get("LoadBalancers", []))
            elif service_key == "ebs":
                resource_count = len(response.get("Volumes", []))
            elif service_key == "iam":
                resource_count = len(response.get("Users", []))

            results[service_key] = {
                "success": True,
                "display_name": display_name,
                "resource_count": resource_count,
            }

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            results[service_key] = {
                "success": False,
                "display_name": display_name,
                "error": error_code,
                "message": error_msg,
            }
        except BotoCoreError as e:
            results[service_key] = {
                "success": False,
                "display_name": display_name,
                "error": "BotoCoreError",
                "message": str(e),
            }
        except Exception as e:
            results[service_key] = {
                "success": False,
                "display_name": display_name,
                "error": type(e).__name__,
                "message": str(e),
            }

    return results


def test_elder_discovery_client(
    region: str, verbose: bool = False
) -> tuple[bool, str, str]:
    """
    Test the actual Elder AWSDiscoveryClient.

    Returns:
        Tuple of (success, message, auth_method)
    """
    try:
        # Add project root to path
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from apps.api.services.discovery.aws_discovery import AWSDiscoveryClient

        # Build config based on available credentials
        # The client will auto-detect the best authentication method
        config = {
            "provider_type": "aws",
            "region": region,
        }

        # Add static credentials if available
        if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get(
            "AWS_SECRET_ACCESS_KEY"
        ):
            config["access_key_id"] = os.environ.get("AWS_ACCESS_KEY_ID")
            config["secret_access_key"] = os.environ.get("AWS_SECRET_ACCESS_KEY")

        # Add OIDC config if available
        if os.environ.get("AWS_ROLE_ARN"):
            config["role_arn"] = os.environ.get("AWS_ROLE_ARN")
        if os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE"):
            config["web_identity_token_file"] = os.environ.get(
                "AWS_WEB_IDENTITY_TOKEN_FILE"
            )

        client = AWSDiscoveryClient(config)
        auth_method = client.get_auth_method()

        if client.test_connection():
            return True, "Elder AWSDiscoveryClient connected successfully", auth_method
        else:
            return (
                False,
                "Elder AWSDiscoveryClient.test_connection() returned False",
                auth_method,
            )

    except ImportError as e:
        return False, f"Failed to import AWSDiscoveryClient: {e}", "unknown"
    except Exception as e:
        return False, f"Error initializing AWSDiscoveryClient: {e}", "unknown"


def main():
    parser = argparse.ArgumentParser(
        description="Validate AWS connection for Elder Discovery service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed output",
    )
    parser.add_argument(
        "-d",
        "--discover",
        action="store_true",
        help="Run discovery test after connection validation",
    )
    parser.add_argument(
        "-r",
        "--region",
        default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        help="AWS region to test (default: us-east-1 or AWS_DEFAULT_REGION)",
    )
    parser.add_argument(
        "--services",
        default="ec2,s3,rds,lambda,vpc",
        help="Comma-separated services to test (default: ec2,s3,rds,lambda,vpc)",
    )

    args = parser.parse_args()
    services = [s.strip().lower() for s in args.services.split(",")]

    print_header("Elder AWS Connection Validation")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Region: {args.region}")

    all_passed = True

    # Step 1: Check boto3 installation
    print("\n" + Colors.BOLD + "Step 1: Checking boto3 installation" + Colors.END)
    boto3_ok, boto3_version = check_boto3_import()
    if boto3_ok:
        print_success(f"boto3 v{boto3_version} installed")
    else:
        print_error(f"boto3 not installed: {boto3_version}")
        print_info("Install with: pip install boto3")
        sys.exit(1)

    # Step 2: Check credentials configuration
    print(
        "\n" + Colors.BOLD + "Step 2: Checking credentials configuration" + Colors.END
    )
    creds = check_credentials_configured()
    auth_method = detect_auth_method()

    auth_method_display = {
        "static_credentials": "Static Credentials (Access Key + Secret Key)",
        "web_identity_oidc": "Web Identity / OIDC (Recommended)",
        "aws_profile": "AWS Profile",
        "environment_or_iam_role": "Environment / IAM Role",
    }
    print_info(
        f"Authentication method: {auth_method_display.get(auth_method, auth_method)}"
    )

    if creds["AWS_ACCESS_KEY_ID"] and creds["AWS_SECRET_ACCESS_KEY"]:
        print_success("AWS static credentials configured")
        if args.verbose:
            # Show masked key ID
            key_id = os.environ.get("AWS_ACCESS_KEY_ID", "")
            masked = (
                key_id[:4] + "*" * (len(key_id) - 8) + key_id[-4:]
                if len(key_id) > 8
                else "****"
            )
            print_info(f"Access Key ID: {masked}")
    elif creds["AWS_ROLE_ARN"] and creds["AWS_WEB_IDENTITY_TOKEN_FILE"]:
        print_success("AWS OIDC/Web Identity configured")
        if args.verbose:
            print_info(f"Role ARN: {os.environ.get('AWS_ROLE_ARN', '')}")
            print_info(
                f"Token File: {os.environ.get('AWS_WEB_IDENTITY_TOKEN_FILE', '')}"
            )
    else:
        print_info("No explicit credentials found, will use IAM role or AWS config")

    if creds["AWS_SESSION_TOKEN"]:
        print_info("Session token detected (using temporary credentials)")

    # Step 3: Test STS connection
    print("\n" + Colors.BOLD + "Step 3: Testing AWS authentication (STS)" + Colors.END)
    sts_ok, sts_result = test_sts_connection(args.region, args.verbose)

    if sts_ok:
        print_success("AWS authentication successful")
        print_info(f"Account: {sts_result['account']}")
        print_info(f"ARN: {sts_result['arn']}")
        if args.verbose:
            print_info(f"User ID: {sts_result['user_id']}")
    else:
        print_error(f"AWS authentication failed: {sts_result['error']}")
        print_error(f"  {sts_result['message']}")
        if "hint" in sts_result:
            print_info(f"Hint: {sts_result['hint']}")
        all_passed = False

    # Step 4: Test Elder's AWSDiscoveryClient
    print("\n" + Colors.BOLD + "Step 4: Testing Elder AWSDiscoveryClient" + Colors.END)
    elder_ok, elder_msg, elder_auth = test_elder_discovery_client(
        args.region, args.verbose
    )

    if elder_ok:
        print_success(elder_msg)
        print_info(f"Client auth method: {elder_auth}")
    else:
        print_error(elder_msg)
        all_passed = False

    # Step 5: Optional discovery test
    if args.discover and sts_ok:
        print(
            "\n"
            + Colors.BOLD
            + f"Step 5: Testing service discovery ({', '.join(services)})"
            + Colors.END
        )
        service_results = test_service_access(args.region, services, args.verbose)

        for service, result in service_results.items():
            if result["success"]:
                count = result.get("resource_count", 0)
                print_success(
                    f"{result['display_name']}: Access OK ({count} resources found)"
                )
            else:
                print_error(f"{result['display_name']}: {result['error']}")
                if args.verbose and "message" in result:
                    print_error(f"  {result['message']}")
                all_passed = False

        # Summary of required IAM permissions
        failed_services = [s for s, r in service_results.items() if not r["success"]]
        if failed_services:
            print("\n" + Colors.BOLD + "Required IAM Permissions:" + Colors.END)
            permission_map = {
                "ec2": "ec2:DescribeInstances, ec2:DescribeVolumes, ec2:DescribeVpcs, ec2:DescribeSubnets",
                "s3": "s3:ListAllMyBuckets, s3:GetBucketLocation, s3:GetBucketTagging",
                "rds": "rds:DescribeDBInstances, rds:ListTagsForResource",
                "lambda": "lambda:ListFunctions, lambda:ListTags",
                "vpc": "ec2:DescribeVpcs, ec2:DescribeSubnets",
                "elb": "elasticloadbalancing:DescribeLoadBalancers, elasticloadbalancing:DescribeTags",
                "ebs": "ec2:DescribeVolumes",
                "iam": "iam:ListUsers, iam:ListRoles",
            }
            for service in failed_services:
                if service in permission_map:
                    print_info(f"  {service}: {permission_map[service]}")

    # Final summary
    print_header("Validation Summary")
    if all_passed:
        print_success("All validation checks passed!")
        print_info("Elder AWS Discovery is ready to use.")
        sys.exit(0)
    else:
        print_error("Some validation checks failed.")
        print_info("Review the errors above and check your AWS configuration.")
        sys.exit(1)


if __name__ == "__main__":
    main()
