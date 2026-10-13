# flake8: noqa: E501
#!/usr/bin/env python3
"""Test connectivity to external services before running the connector."""

import asyncio
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from apps.worker.config.settings import settings  # noqa: E402
from apps.worker.utils.logger import configure_logging, get_logger  # noqa: E402

# Configure logging
configure_logging()
logger = get_logger(__name__)


async def test_aws():
    """Test AWS connectivity."""
    if not settings.aws_enabled:
        logger.info("AWS connector disabled, skipping")
        return True

    try:
        import boto3

        logger.info("Testing AWS connectivity...")
        sts = boto3.client(
            "sts",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_default_region,
        )
        identity = sts.get_caller_identity()
        logger.info(
            "✓ AWS connection successful",
            account_id=identity["Account"],
            arn=identity["Arn"],
        )
        return True
    except Exception as e:
        logger.error(f"✗ AWS connection failed: {e}")
        return False


async def test_gcp():
    """Test GCP connectivity."""
    if not settings.gcp_enabled:
        logger.info("GCP connector disabled, skipping")
        return True

    try:
        from google.auth import load_credentials_from_file
        from google.cloud import compute_v1

        logger.info("Testing GCP connectivity...")

        if settings.gcp_credentials_path:
            credentials, _ = load_credentials_from_file(settings.gcp_credentials_path)
        else:
            from google.auth import default

            credentials, _ = default()

        zones_client = compute_v1.ZonesClient(credentials=credentials)
        list(zones_client.list(project=settings.gcp_project_id, max_results=1))

        logger.info("✓ GCP connection successful", project=settings.gcp_project_id)
        return True
    except Exception as e:
        logger.error(f"✗ GCP connection failed: {e}")
        return False


async def test_google_workspace():
    """Test Google Workspace connectivity."""
    if not settings.google_workspace_enabled:
        logger.info("Google Workspace connector disabled, skipping")
        return True

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        logger.info("Testing Google Workspace connectivity...")

        credentials = service_account.Credentials.from_service_account_file(
            settings.google_workspace_credentials_path,
            scopes=["https://www.googleapis.com/auth/admin.directory.user.readonly"],
        )

        delegated_credentials = credentials.with_subject(
            settings.google_workspace_admin_email
        )

        admin_service = build(
            "admin", "directory_v1", credentials=delegated_credentials
        )
        admin_service.users().list(
            customer=settings.google_workspace_customer_id,
            maxResults=1,
        ).execute()

        logger.info(
            "✓ Google Workspace connection successful",
            admin_email=settings.google_workspace_admin_email,
        )
        return True
    except Exception as e:
        logger.error(f"✗ Google Workspace connection failed: {e}")
        return False


async def test_ldap():
    """Test LDAP connectivity."""
    if not settings.ldap_enabled:
        logger.info("LDAP connector disabled, skipping")
        return True

    try:
        import ssl

        import ldap3
        from ldap3 import ALL, Connection, Server

        logger.info("Testing LDAP connectivity...")

        tls = None
        if settings.ldap_use_ssl:
            tls_config = ldap3.Tls(
                validate=(
                    ssl.CERT_REQUIRED if settings.ldap_verify_cert else ssl.CERT_NONE
                ),
            )
            tls = tls_config

        server = Server(
            settings.ldap_server,
            port=settings.ldap_port,
            use_ssl=settings.ldap_use_ssl,
            tls=tls,
            get_info=ALL,
        )

        conn = Connection(
            server,
            user=settings.ldap_bind_dn,
            password=settings.ldap_bind_password,
            auto_bind=True,
        )

        # Test simple search
        conn.search(
            search_base=settings.ldap_base_dn,
            search_filter="(objectClass=*)",
            search_scope=ldap3.BASE,
            attributes=["objectClass"],
        )

        conn.unbind()

        logger.info(
            "✓ LDAP connection successful",
            server=settings.ldap_server,
            use_ssl=settings.ldap_use_ssl,
        )
        return True
    except Exception as e:
        logger.error(f"✗ LDAP connection failed: {e}")
        return False


async def test_okta():
    """Test Okta connectivity."""
    if not settings.okta_enabled:
        logger.info("Okta connector disabled, skipping")
        return True

    try:
        import httpx

        logger.info("Testing Okta connectivity...")

        base_url = f"https://{settings.okta_domain}"
        headers = {
            "Authorization": f"SSWS {settings.okta_api_token}",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
            resp = await client.get(f"{base_url}/api/v1/users?limit=1")
            resp.raise_for_status()

        logger.info("✓ Okta connection successful", domain=settings.okta_domain)
        return True
    except Exception as e:
        logger.error(f"✗ Okta connection failed: {e}")
        return False


async def test_authentik():
    """Test Authentik connectivity."""
    if not settings.authentik_enabled:
        logger.info("Authentik connector disabled, skipping")
        return True

    try:
        import httpx

        logger.info("Testing Authentik connectivity...")

        base_url = f"https://{settings.authentik_domain}/api/v3"
        headers = {
            "Authorization": f"Bearer {settings.authentik_api_token}",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(
            headers=headers, timeout=30.0, verify=settings.authentik_verify_ssl
        ) as client:
            resp = await client.get(f"{base_url}/core/users/?page_size=1")
            resp.raise_for_status()

        logger.info(
            "✓ Authentik connection successful", domain=settings.authentik_domain
        )
        return True
    except Exception as e:
        logger.error(f"✗ Authentik connection failed: {e}")
        return False


async def test_elder_api():
    """Test Elder API connectivity."""
    try:
        from apps.worker.utils.elder_client import ElderAPIClient

        logger.info("Testing Elder API connectivity...")

        async with ElderAPIClient() as client:
            healthy = await client.health_check()

            if healthy:
                logger.info(
                    "✓ Elder API connection successful", url=settings.elder_api_url
                )
                return True
            else:
                logger.error("✗ Elder API health check failed")
                return False
    except Exception as e:
        logger.error(f"✗ Elder API connection failed: {e}")
        return False


async def main():
    """Run all connectivity tests."""
    logger.info("=" * 60)
    logger.info("Elder Connector Service - Connectivity Test")
    logger.info("=" * 60)

    results = {
        "Elder API": await test_elder_api(),
        "AWS": await test_aws(),
        "GCP": await test_gcp(),
        "Google Workspace": await test_google_workspace(),
        "LDAP": await test_ldap(),
        "Okta": await test_okta(),
        "Authentik": await test_authentik(),
    }

    logger.info("=" * 60)
    logger.info("Test Results:")
    logger.info("=" * 60)

    all_passed = True
    for service, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        logger.info(f"{service}: {status}")
        if not passed:
            all_passed = False

    logger.info("=" * 60)

    if all_passed:
        logger.info("All tests passed! Connector is ready to run.")
        return 0
    else:
        logger.error("Some tests failed. Please check your configuration.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
