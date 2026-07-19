"""Guard the discovery services against schema drift.

Discovery writes to domain tables through penguin-dal `insert()` calls whose
kwargs are plain Python identifiers, so a renamed or missing column is invisible
until a scan runs and blows up at runtime. Every enumerator swallows its own
exceptions, so the failure surfaces as "0 resources discovered" rather than an
error. These tests compare each insert against the SQLAlchemy models statically.
"""

import ast
import importlib
from pathlib import Path

import pytest

from apps.api.models.base import Base
from apps.api.modules import CORE_MODELS, MODULES
from apps.worker.discovery.aws_discovery import AWSDiscoveryClient

REPO_ROOT = Path(__file__).resolve().parents[2]

SERVICE_FILES = [
    REPO_ROOT / "apps" / "worker" / "discovery" / "service.py",
    REPO_ROOT / "apps" / "api" / "services" / "discovery" / "service.py",
]

# Insert sites permitted to omit a required column. Empty by design — every
# known gap has been fixed. Shrink this set; never grow it.
KNOWN_GAPS: set = set()


@pytest.fixture(scope="module")
def tables():
    """Load every registered model and return Base.metadata.tables."""
    to_import = set(CORE_MODELS)
    for manifest in MODULES:
        to_import.update(manifest.models_import)
    for path in sorted(to_import):
        try:
            importlib.import_module(path)
        except ImportError:
            pass
    return Base.metadata.tables


def _required_columns(table):
    """Columns an insert must supply: NOT NULL, no default, not autoincrement."""
    required = set()
    for col in table.columns:
        if col.nullable or col.default is not None or col.server_default is not None:
            continue
        if col.primary_key and col.autoincrement:
            continue
        required.add(col.name)
    return required


def _insert_sites(path):
    """Yield (table_name, kwarg_names, lineno) for each `self.db.X.insert(...)`."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # self.db.<table>.insert(...)
        if not (isinstance(func, ast.Attribute) and func.attr == "insert"):
            continue
        owner = func.value
        if not isinstance(owner, ast.Attribute):
            continue
        table_name = owner.attr
        # insert(**kwargs) can't be resolved statically — skip it.
        if any(kw.arg is None for kw in node.keywords):
            continue
        names = {kw.arg for kw in node.keywords}
        yield table_name, names, node.lineno


@pytest.mark.parametrize("path", SERVICE_FILES, ids=lambda p: p.parent.parent.name)
def test_insert_kwargs_are_real_columns(path, tables):
    """No insert may reference a column the model doesn't define."""
    problems = []
    for table_name, names, lineno in _insert_sites(path):
        table = tables.get(table_name)
        if table is None:
            problems.append(f"{path.name}:{lineno} unknown table '{table_name}'")
            continue
        unknown = sorted(n for n in names if n not in table.columns)
        if unknown:
            problems.append(f"{path.name}:{lineno} {table_name} -> unknown {unknown}")
    assert (
        not problems
    ), "discovery insert references non-existent columns:\n" + "\n".join(problems)


@pytest.mark.parametrize("path", SERVICE_FILES, ids=lambda p: p.parent.parent.name)
def test_insert_supplies_required_columns(path, tables):
    """Every NOT NULL column without a default must be supplied."""
    problems = []
    for table_name, names, lineno in _insert_sites(path):
        table = tables.get(table_name)
        if table is None:
            continue
        missing = sorted(
            c
            for c in _required_columns(table) - names
            if (table_name, c) not in KNOWN_GAPS
        )
        if missing:
            problems.append(f"{path.name}:{lineno} {table_name} -> missing {missing}")
    assert not problems, "discovery insert omits required columns:\n" + "\n".join(
        problems
    )


class TestAwsResourceNaming:
    """Untagged AWS resources must not collapse onto a shared placeholder name."""

    def test_name_tag_is_used_when_present(self):
        tags = [{"Key": "Name", "Value": "web-01"}]
        assert AWSDiscoveryClient._get_name_from_tags(None, tags, "i-123") == "web-01"

    def test_falls_back_to_resource_id_when_untagged(self):
        # regression: three untagged subnets all landed on "Unnamed" and the
        # networking upsert (keyed on name) collapsed them into a single row.
        assert (
            AWSDiscoveryClient._get_name_from_tags(None, [], "subnet-abc")
            == "subnet-abc"
        )
        assert (
            AWSDiscoveryClient._get_name_from_tags(None, None, "vpc-xyz") == "vpc-xyz"
        )

    def test_falls_back_when_name_tag_is_empty(self):
        tags = [{"Key": "Name", "Value": ""}]
        assert AWSDiscoveryClient._get_name_from_tags(None, tags, "vol-9") == "vol-9"

    def test_distinct_ids_yield_distinct_names(self):
        ids = ["subnet-1", "subnet-2", "subnet-3"]
        names = {AWSDiscoveryClient._get_name_from_tags(None, [], i) for i in ids}
        assert len(names) == 3
