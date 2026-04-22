"""
Stub optional heavy worker dependencies for unit tests.

Worker connectors depend on optional packages (google-api-core, pyVmomi, etc.)
that are not installed in the unit test environment.  This conftest stubs them
out so tests can import individual connectors without installing the full stack.

Import this file at the top of any unit test that imports worker connectors:
    import tests.unit.conftest_worker_stubs  # noqa: F401
"""

import sys
from unittest.mock import MagicMock

_STUBS = [
    # GCP / Google auth
    "google",
    "google.api_core",
    "google.api_core.exceptions",
    "google.auth",
    "google.cloud",
    "google.cloud.compute_v1",
    "google.cloud.storage",
    "google.oauth2",
    "google.oauth2.service_account",
    # Google API client library
    "googleapiclient",
    "googleapiclient.discovery",
    "googleapiclient.errors",
    # AWS
    "boto3",
    "botocore",
    "botocore.exceptions",
    # vCenter
    "pyVmomi",
    "pyVim",
    "pyVim.connect",
    # LXD
    "pylxd",
    # LDAP
    "ldap3",
    "ldap3.core",
    "ldap3.core.exceptions",
    # Kubernetes
    "kubernetes",
    "kubernetes.client",
    "kubernetes.client.rest",
    "kubernetes.config",
    # FleetDM / iboss / Authentik (httpx may be installed, but guard anyway)
    "httpx",
    # Okta
    "okta",
    "okta.client",
    # Authentik
    "authentik",
]

for _name in _STUBS:
    # Force-set: some (e.g. google.*) may be partially present as namespace
    # packages that don't expose the classes connectors need.
    _m = MagicMock()
    _m.__name__ = _name
    _m.__path__ = []  # marks it as a package so sub-imports resolve
    _m.__spec__ = None
    sys.modules[_name] = _m
