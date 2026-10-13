"""
Stub optional heavy worker dependencies for unit tests.

Worker connectors depend on optional packages (google-api-core, pyVmomi, etc.)
that are not installed in the unit test environment.  This conftest stubs them
out so tests can import individual connectors without installing the full stack.

Import this file at the top of any unit test that imports worker connectors:
    import tests.unit.conftest_worker_stubs  # noqa: F401
"""

import importlib
import importlib.util
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


def _find_spec(name):
    """find_spec that never raises (stubbed parents break the machinery)."""
    try:
        return importlib.util.find_spec(name)
    except (ImportError, AttributeError, ValueError):
        return None


for _name in _STUBS:
    _spec = _find_spec(_name)
    if _spec is not None and _spec.loader is not None:
        # The real package is installed (e.g. httpx) — never shadow it with
        # a mock; doing so poisons sys.modules for the whole test session.
        continue
    if _spec is not None and _spec.loader is None and "." not in _name:
        # Real top-level NAMESPACE package (e.g. `google`, provided by
        # protobuf). Replacing it breaks genuine children like
        # google.protobuf (needed by the OTel OTLP exporter) — keep the real
        # namespace and stub only its missing children below.
        importlib.import_module(_name)
        continue
    _m = MagicMock()
    _m.__name__ = _name
    _m.__path__ = []  # marks it as a package so sub-imports resolve
    _m.__spec__ = None
    sys.modules[_name] = _m
    # Bind the stub as an attribute of its parent — pre-seeding sys.modules
    # skips the import machinery step that normally does this.
    _parent, _, _child = _name.rpartition(".")
    if _parent and _parent in sys.modules:
        setattr(sys.modules[_parent], _child, _m)
