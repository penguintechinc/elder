# Cloud-Scan Linkage — PR1: Core Linker Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the provider-agnostic two-pass relationship linker in the shared discovery service — so that once providers declare `relationships`, edges are resolved by native cloud id and written where the graph UI reads them.

**Architecture:** `_store_discovered_resources` gains a second pass. Pass 1 persists resources (as today) and, as it goes, populates their `external_id` column and registers each in an in-memory `scan_index` keyed `(provider, external_id) → (type_string, row_id)`. Pass 2 walks the resources again, and for every `relationships` entry resolves the target by native id (scan_index first, DB fallback on `external_id`) and writes a `dependencies` edge — dual-writing `network_entity_mappings` for network-membership edges. Unresolved targets are logged and counted, never silently dropped. A bounded read-side change widens the graph node allowlist so cross-table edges render. **No provider capture changes in this PR** — providers still emit no `relationships`, so the engine is a tested no-op on real scans until PR2+.

**Tech Stack:** Python 3.13, Quart, penguin-dal (runtime, auto-reflected DAL), SQLAlchemy + Alembic (schema only), pytest (SQLite test DB via `app`/`db` fixtures).

## Global Constraints

- **Runtime DB access is penguin-dal only** — `self.db(<query>).select()`, `self.db.<table>.insert(...)`, `self.db.commit()`. Never `self.db.<table>.select()` (resolves `select` as a column → AttributeError). Never SQLAlchemy at runtime.
- **Schema changes: SQLAlchemy model + Alembic migration only.** penguin-dal auto-reflects columns from the live DB (`shared/database/connection.py:44`, `DAL(..., migrate=False)`), so no PyDAL field list exists to edit. Unit tests build schema via `create_all()` inside `create_app("testing")`, so a new column must be on the **model** for tests to see it.
- **Alembic migration is additive and idempotent** — nullable column, `sa.inspect` guard before `op.add_column`, no backfill, no startup auto-migrate.
- **Edge direction: source = dependent/child, target = depended-on/parent** (matches existing `routes_to`, `bound_to`).
- **`dependency_type` vocabulary:** `in_network`, `in_subnet`, `uses_security_group`, `attached_to`, `routes_to`, `assumes_role`, `in_resource_group`, `bound_to`, `runs_on`, `runs_in`, `manages`, `discovered_from`.
- **Edge helpers stay idempotent** (SELECT-first) — re-scan must not duplicate edges.
- **90%+ coverage**; black / isort / flake8 clean.
- **No PII / secrets in logs** — an unresolved-edge warning logs the dangling native id + edge type only.

---

## File map

| File | Change |
|---|---|
| `apps/api/models/infrastructure.py` | Add `external_id` to `NetworkingResource`, `Service`, `Software`, `DataStore` |
| `apps/api/models/security.py` | Add `external_id` to `Certificate` |
| `alembic/versions/015_add_external_id_to_domain_tables.py` | Create — additive migration |
| `apps/worker/discovery/service.py` | Populate `external_id`; fix `network_entity_mappings` field bug; add scan_index + `_resolve_target` + `_link_resources`; two-pass `_store_discovered_resources`; return counts |
| `apps/api/api/v1/graph.py` | Widen node allowlist + add node materialization for domain tables |
| `tests/unit/test_discovery_linker.py` | Create — engine + bug-fix + external_id tests |
| `tests/unit/test_graph_domain_nodes.py` | Create — read-side widening test |

---

### Task 1: Add `external_id` to domain-table models + migration

**Files:**
- Modify: `apps/api/models/infrastructure.py` (`NetworkingResource` :20-37, `Service` :68-93, `Software` :96-118, `DataStore` :121-152)
- Modify: `apps/api/models/security.py` (`Certificate` :133)
- Create: `alembic/versions/015_add_external_id_to_domain_tables.py`
- Test: `tests/unit/test_discovery_linker.py`

**Interfaces:**
- Produces: an `external_id` column (`String(255)`, nullable, indexed) on tables `networking_resources`, `services`, `software`, `data_stores`, `certificates`. `entities.external_id` already exists (`entity.py:44`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_discovery_linker.py`:

```python
"""Tests for the cloud-discovery relationship linker (PR1 core engine)."""

from apps.api.models.infrastructure import (
    DataStore,
    NetworkingResource,
    Service,
    Software,
)
from apps.api.models.security import Certificate


def test_domain_tables_have_external_id_column():
    # regression: edge resolution keys on external_id; these tables lacked it.
    for model in (NetworkingResource, Service, Software, DataStore, Certificate):
        assert "external_id" in model.__table__.columns, model.__tablename__
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_discovery_linker.py::test_domain_tables_have_external_id_column -v`
Expected: FAIL — `assert 'external_id' in ...` KeyError/AssertionError for `networking_resources`.

- [ ] **Step 3: Add the column to each model**

In `apps/api/models/infrastructure.py`, add this line inside each of `NetworkingResource`, `Service`, `Software`, `DataStore` (place it near `name`):

```python
    external_id = Column(String(255), nullable=True, index=True)
```

In `apps/api/models/security.py`, add the same line inside `Certificate`.

- [ ] **Step 4: Create the Alembic migration**

Create `alembic/versions/015_add_external_id_to_domain_tables.py`:

```python
"""Add external_id to domain tables for discovery edge resolution

Revision ID: 015
Revises: 014
Create Date: 2026-07-25
"""

import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

_TABLES = ["networking_resources", "services", "software", "data_stores", "certificates"]


def upgrade():
    """Add nullable external_id + index to each domain table (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table in _TABLES:
        columns = [c["name"] for c in inspector.get_columns(table)]
        if "external_id" not in columns:
            op.add_column(table, sa.Column("external_id", sa.String(255), nullable=True))
            op.create_index(f"ix_{table}_external_id", table, ["external_id"])


def downgrade():
    for table in _TABLES:
        op.drop_index(f"ix_{table}_external_id", table_name=table)
        op.drop_column(table, "external_id")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_discovery_linker.py::test_domain_tables_have_external_id_column -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/models/infrastructure.py apps/api/models/security.py \
  alembic/versions/015_add_external_id_to_domain_tables.py tests/unit/test_discovery_linker.py
git commit -m "feat(discovery): add external_id to domain tables for edge resolution"
```

---

### Task 2: Populate `external_id` on persisted resources

**Files:**
- Modify: `apps/worker/discovery/service.py` — `_store_as_entity` (:1399-1469), `_upsert_networking_resource` (:514-558), and the `.insert(...)` calls in `_store_as_service`, `_store_as_data_store`, `_store_container_image_as_software`
- Test: `tests/unit/test_discovery_linker.py`

**Interfaces:**
- Consumes: a resource dict's native id via `resource.get("external_id") or resource.get("resource_id")`.
- Produces: every discovered row carries `external_id`. `_upsert_networking_resource` gains an `external_id: Optional[str] = None` parameter.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_discovery_linker.py`:

```python
import pytest

from apps.worker.discovery.service import DiscoveryService


@pytest.fixture
def seeded(app):
    """A DiscoveryService on the test DAL with one tenant + organization."""
    with app.app_context():
        db = app.db
        db.tenants.insert(name="T", slug="linker-test", is_active=True)
        org_id = db.organizations.insert(name="Org", tenant_id=1)
        db.commit()
        yield DiscoveryService(db), db, org_id


def test_entity_insert_sets_external_id(seeded, app):
    service, db, org_id = seeded
    with app.app_context():
        eid = service._store_as_entity(
            org_id,
            {"name": "web-1", "resource_type": "ec2_instance", "resource_id": "i-abc"},
            "compute",
        )
        db.commit()
        row = db(db.entities.id == eid).select().first()
        assert row.external_id == "i-abc"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_discovery_linker.py::test_entity_insert_sets_external_id -v`
Expected: FAIL — `row.external_id` is `None`.

- [ ] **Step 3: Populate external_id in `_store_as_entity`**

In `apps/worker/discovery/service.py`, in `_store_as_entity`, add `external_id` to both the update dict (after line 1449) and the insert dict (after line 1462):

```python
        native_id = resource.get("external_id") or resource.get("resource_id")
```
Add this right after `resource_type = resource.get("resource_type", "")` (line 1421). Then in the `update_data` dict add `"external_id": native_id,` and in `insert_data` add `"external_id": native_id,`.

- [ ] **Step 4: Populate external_id in `_upsert_networking_resource`**

Add a parameter `external_id: Optional[str] = None` to the signature (after `tags`, line 522), add `external_id=external_id` to the `.insert(...)` (after line 551), and to the update branch add `external_id=external_id,` (after line 539). Then update its two call sites in `_ensure_intermediate_networking` to pass the native id:
- AWS VPC (near line 447): add `external_id=vpc_id_str,`
- AWS subnet (near line 472): add `external_id=subnet_id_str,` (compute `subnet_id_str` before the call — it is currently derived at line 485; move that derivation above the `_upsert_networking_resource` call)
- GCP VPC (near line 494): add `external_id=resource.get("resource_id"),`

- [ ] **Step 5: Populate external_id in service/data_store/software inserts**

In `_store_as_service`, `_store_as_data_store`, and `_store_container_image_as_software`, add `external_id=resource.get("external_id") or resource.get("resource_id")` to each `.insert(...)` call (and the corresponding update dict where the function upserts). For `_store_container_image_as_software` the native id is the image string — use `external_id=image`.

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/unit/test_discovery_linker.py::test_entity_insert_sets_external_id -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/worker/discovery/service.py tests/unit/test_discovery_linker.py
git commit -m "feat(discovery): populate external_id on all persisted resources"
```

---

### Task 3: Fix the `network_entity_mappings` field-name bug

**Files:**
- Modify: `apps/worker/discovery/service.py:568, 579` (`_upsert_network_entity_mapping`)
- Test: `tests/unit/test_discovery_linker.py`

**Interfaces:**
- Produces: `_upsert_network_entity_mapping(network_id, entity_id, relationship_type)` actually inserts a row (was raising AttributeError on the non-existent `networking_resource_id` field and swallowing it).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_discovery_linker.py`:

```python
def test_network_entity_mapping_actually_inserts(seeded, app):
    service, db, org_id = seeded
    with app.app_context():
        net_id = service._upsert_networking_resource(
            org_id, name="vpc-1", network_type="other", external_id="vpc-1"
        )
        eid = service._store_as_entity(
            org_id,
            {"name": "web", "resource_type": "ec2_instance", "resource_id": "i-1"},
            "compute",
        )
        db.commit()
        service._upsert_network_entity_mapping(net_id, eid, "in_network")
        db.commit()
        # regression: worker referenced networking_resource_id (no such column),
        # so this row was never written.
        row = (
            db(
                (db.network_entity_mappings.network_id == net_id)
                & (db.network_entity_mappings.entity_id == eid)
            )
            .select()
            .first()
        )
        assert row is not None
        assert row.relationship_type == "in_network"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_discovery_linker.py::test_network_entity_mapping_actually_inserts -v`
Expected: FAIL — no row (the helper caught an AttributeError internally and logged a warning).

- [ ] **Step 3: Fix the field name**

In `apps/worker/discovery/service.py`, change the two references from `networking_resource_id` to `network_id`:
- Line 568: `self.db.network_entity_mappings.network_id == network_id`
- Line 579: `networking_resource_id=network_id,` → `network_id=network_id,`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_discovery_linker.py::test_network_entity_mapping_actually_inserts -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/discovery/service.py tests/unit/test_discovery_linker.py
git commit -m "fix(discovery): correct network_entity_mappings field name (network_id)"
```

---

### Task 4: Two-pass linker — scan_index, resolver, `_link_resources`

**Files:**
- Modify: `apps/worker/discovery/service.py` — add `_resolve_target`, `_resolve_target_in_db`, `_link_resources`; rewrite `_store_discovered_resources` (:1141-1325) as two passes returning counts
- Test: `tests/unit/test_discovery_linker.py`

**Interfaces:**
- Consumes: resource dicts that may carry `relationships: List[{"target_external_id": str, "edge_type": str, "target_kind": str}]`.
- Produces:
  - `_resolve_target(scan_index, provider, target_external_id, target_kind, organization_id) -> Optional[Tuple[str, int]]`
  - `_link_resources(source, target, edge_type)` where `source`/`target` are `(type_string, row_id)`
  - `_store_discovered_resources(...) -> Dict[str, int]` returning `{"edges_created": int, "unresolved_edges": int}`

- [ ] **Step 1: Write the failing test (pure resolver)**

Append to `tests/unit/test_discovery_linker.py`:

```python
from unittest.mock import MagicMock


def test_resolve_target_prefers_scan_index():
    service = DiscoveryService(MagicMock())
    scan_index = {("aws", "vpc-1"): ("networking_resource", 42)}
    assert service._resolve_target(scan_index, "aws", "vpc-1", "networking_resource", 1) == (
        "networking_resource",
        42,
    )


def test_resolve_target_unknown_returns_none_without_db_hit():
    db = MagicMock()
    db.return_value.select.return_value.first.return_value = None
    service = DiscoveryService(db)
    assert service._resolve_target({}, "aws", "vpc-nope", "networking_resource", 1) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_discovery_linker.py -k resolve_target -v`
Expected: FAIL — `AttributeError: '_resolve_target'`.

- [ ] **Step 3: Implement the resolver + linker helpers**

Add to `DiscoveryService` (place after `_create_dependency_link`):

```python
    # Native-id → domain table for DB-fallback resolution.
    _RESOLVE_TABLES = {
        "entity": "entities",
        "networking_resource": "networking_resources",
        "data_store": "data_stores",
        "service": "services",
        "software": "software",
    }

    def _resolve_target(
        self, scan_index, provider, target_external_id, target_kind, organization_id
    ):
        """Resolve a native cloud id to (type_string, row_id).

        Scan-local index first (same job), then a DB lookup on external_id so
        targets discovered by an earlier scan still resolve.
        """
        hit = scan_index.get((provider, target_external_id))
        if hit:
            return hit
        return self._resolve_target_in_db(target_external_id, target_kind, organization_id)

    def _resolve_target_in_db(self, external_id, target_kind, organization_id):
        """Look up a persisted row by external_id, hinted table first."""
        order = []
        if target_kind in self._RESOLVE_TABLES:
            order.append(target_kind)
        order += [k for k in self._RESOLVE_TABLES if k not in order]
        for type_string in order:
            table = getattr(self.db, self._RESOLVE_TABLES[type_string])
            row = (
                self.db(
                    (table.external_id == external_id)
                    & (table.organization_id == organization_id)
                )
                .select()
                .first()
            )
            if row:
                return (type_string, row.id)
        return None

    def _link_resources(self, source, target, edge_type):
        """Write a dependencies edge; dual-write network membership."""
        src_type, src_id = source
        tgt_type, tgt_id = target
        self._create_dependency_link(
            src_type, src_id, tgt_type, tgt_id, edge_type, {"linked_by": "discovery"}
        )
        if (
            edge_type in ("in_network", "in_subnet")
            and src_type == "entity"
            and tgt_type == "networking_resource"
        ):
            self._upsert_network_entity_mapping(tgt_id, src_id, edge_type)
```

- [ ] **Step 4: Rewrite `_store_discovered_resources` as two passes**

Modify `_store_discovered_resources` (`service.py:1141-1325`):
1. After `networking_lookup = self._ensure_intermediate_networking(...)` (line 1162), seed the scan index and edge accumulators:

```python
        # (provider, external_id) -> (type_string, row_id)
        scan_index = {}
        for key, net_id in networking_lookup.items():
            _, _, ext = key.partition(":")
            if ext and net_id:
                scan_index[(provider, ext)] = ("networking_resource", net_id)
        edges_created = 0
        unresolved_edges = 0
```

2. In each persistence branch of the main loop, register the persisted row. After the identity/service/data_store/entity/software ids are obtained, register them, e.g. immediately after `entity_id = self._store_as_entity(...)` add:

```python
                    self._register(scan_index, provider, resource, "entity", entity_id)
```
and equivalently `("service", svc_id)`, `("data_store", ds_id)`, `("identity", sa_id)`, `("software", sw_id)`. Add the helper:

```python
    def _register(self, scan_index, provider, resource, type_string, row_id):
        """Index a just-persisted resource by its native id for edge resolution."""
        if not row_id:
            return
        ext = resource.get("external_id") or resource.get("resource_id")
        if ext:
            scan_index[(provider, ext)] = (type_string, row_id)
```

3. Immediately before the final `self.db.commit()` (line 1325), add pass 2:

```python
        # Pass 2: resolve declared relationships into edges.
        for category, resources in discovery_results.items():
            if category in ["resources_count", "discovery_time", "duration_seconds"]:
                continue
            for resource in resources or []:
                rels = resource.get("relationships") or []
                if not rels:
                    continue
                ext = resource.get("external_id") or resource.get("resource_id")
                source = scan_index.get((provider, ext))
                if not source:
                    continue
                for rel in rels:
                    target = self._resolve_target(
                        scan_index,
                        provider,
                        rel.get("target_external_id"),
                        rel.get("target_kind"),
                        organization_id,
                    )
                    if not target:
                        unresolved_edges += 1
                        logger.warning(
                            "Unresolved discovery edge: %s -[%s]-> %s (provider=%s)",
                            ext,
                            rel.get("edge_type"),
                            rel.get("target_external_id"),
                            provider,
                        )
                        continue
                    self._link_resources(source, target, rel.get("edge_type"))
                    edges_created += 1

        self.db.commit()
        return {"edges_created": edges_created, "unresolved_edges": unresolved_edges}
```
Change the method's return annotation from `-> None` to `-> Dict[str, int]`.

- [ ] **Step 5: Write the integration test for the full linker path**

Append to `tests/unit/test_discovery_linker.py`:

```python
def test_two_pass_linker_creates_edges(seeded, app):
    service, db, org_id = seeded
    with app.app_context():
        results = {
            "network": [
                {"name": "vpc-1", "resource_type": "vpc", "resource_id": "vpc-1",
                 "metadata": {"vpc_id": "vpc-1"}},
            ],
            "compute": [
                {"name": "web", "resource_type": "ec2_instance", "resource_id": "i-1",
                 "metadata": {},
                 "relationships": [
                     {"target_external_id": "vpc-1", "edge_type": "in_network",
                      "target_kind": "networking_resource"}
                 ]},
            ],
        }
        counts = service._store_discovered_resources(org_id, results)
        assert counts == {"edges_created": 1, "unresolved_edges": 0}

        inst = db(db.entities.external_id == "i-1").select().first()
        vpc = db(db.networking_resources.external_id == "vpc-1").select().first()
        dep = (
            db(
                (db.dependencies.source_type == "entity")
                & (db.dependencies.source_id == inst.id)
                & (db.dependencies.target_type == "networking_resource")
                & (db.dependencies.target_id == vpc.id)
            )
            .select()
            .first()
        )
        assert dep is not None and dep.dependency_type == "in_network"
        # dual-write to the topology-tab projection
        nem = (
            db(
                (db.network_entity_mappings.network_id == vpc.id)
                & (db.network_entity_mappings.entity_id == inst.id)
            )
            .select()
            .first()
        )
        assert nem is not None


def test_linker_is_idempotent(seeded, app):
    service, db, org_id = seeded
    with app.app_context():
        results = {
            "network": [{"name": "vpc-1", "resource_type": "vpc", "resource_id": "vpc-1",
                         "metadata": {"vpc_id": "vpc-1"}}],
            "compute": [{"name": "web", "resource_type": "ec2_instance", "resource_id": "i-1",
                         "metadata": {}, "relationships": [
                             {"target_external_id": "vpc-1", "edge_type": "in_network",
                              "target_kind": "networking_resource"}]}],
        }
        service._store_discovered_resources(org_id, results)
        service._store_discovered_resources(org_id, results)
        rows = db(db.dependencies.dependency_type == "in_network").select()
        assert len(rows) == 1


def test_linker_counts_unresolved(seeded, app):
    service, db, org_id = seeded
    with app.app_context():
        results = {
            "compute": [{"name": "web", "resource_type": "ec2_instance", "resource_id": "i-9",
                         "metadata": {}, "relationships": [
                             {"target_external_id": "vpc-missing", "edge_type": "in_network",
                              "target_kind": "networking_resource"}]}],
        }
        counts = service._store_discovered_resources(org_id, results)
        assert counts["unresolved_edges"] == 1
        assert counts["edges_created"] == 0
```

- [ ] **Step 6: Run all linker tests**

Run: `pytest tests/unit/test_discovery_linker.py -v`
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add apps/worker/discovery/service.py tests/unit/test_discovery_linker.py
git commit -m "feat(discovery): two-pass relationship linker with native-id resolution"
```

---

### Task 5: Surface edge counts in the job result

**Files:**
- Modify: `apps/worker/discovery/executor.py` (the caller of `_store_discovered_resources`) and/or `apps/worker/discovery/service.py:1612-1620` (`complete_job`)
- Test: `tests/unit/test_discovery_linker.py`

**Interfaces:**
- Consumes: the `{"edges_created", "unresolved_edges"}` dict returned by `_store_discovered_resources`.
- Produces: those two counts present in the discovery job's `results_json`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_discovery_linker.py`:

```python
def test_store_returns_edge_counts(seeded, app):
    service, db, org_id = seeded
    with app.app_context():
        counts = service._store_discovered_resources(org_id, {"compute": []})
        assert set(counts) == {"edges_created", "unresolved_edges"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_discovery_linker.py::test_store_returns_edge_counts -v`
Expected: FAIL if Task 4's return wasn't applied; otherwise PASS — in which case extend the assertion to check the counts are merged into the results dict at the executor call site (read `apps/worker/discovery/executor.py` to find where `_store_discovered_resources` is invoked and where `results` is built for `complete_job`).

- [ ] **Step 3: Merge counts into results at the call site**

In `apps/worker/discovery/executor.py`, where `_store_discovered_resources(...)` is called, capture its return value and merge into the `results` dict passed to `complete_job` (so it lands in `results_json`):

```python
        edge_counts = self.discovery_service._store_discovered_resources(
            organization_id, discovery_results
        )
        results.update(edge_counts)
```
(Adjust variable names to the actual executor code.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_discovery_linker.py::test_store_returns_edge_counts -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/discovery/executor.py tests/unit/test_discovery_linker.py
git commit -m "feat(discovery): report edges_created/unresolved_edges in job result"
```

---

### Task 6: Widen the graph read-side node allowlist

**Files:**
- Modify: `apps/api/api/v1/graph.py` (`VALID_RESOURCE_TYPES` :16-24; node materialization :501-615)
- Test: `tests/unit/test_graph_domain_nodes.py`

**Interfaces:**
- Consumes: `dependencies` rows whose `source_type`/`target_type` are `networking_resource`/`data_store`/`service`/`software`.
- Produces: `/graph/map` materializes nodes for those types so their edges render instead of being dropped by the `add_edge` guard (`graph.py:461-463`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_graph_domain_nodes.py`:

```python
"""The global graph/map must render domain-table nodes so cloud edges show."""


def test_networking_resource_node_and_edge_render(app):
    with app.app_context():
        db = app.db
        db.tenants.insert(name="T", slug="graph-test", is_active=True)
        org_id = db.organizations.insert(name="Org", tenant_id=1)
        eid = db.entities.insert(
            name="web", type="compute", organization_id=org_id, external_id="i-1"
        )
        nid = db.networking_resources.insert(
            name="vpc-1", network_type="other", organization_id=org_id, external_id="vpc-1"
        )
        db.dependencies.insert(
            source_type="entity", source_id=eid,
            target_type="networking_resource", target_id=nid,
            dependency_type="in_network",
        )
        db.commit()

    client = app.test_client()
    resp = client.get("/api/v1/graph/map")
    body = resp.get_json()
    node_types = {n["resource_type"] for n in body["data"]["nodes"]}
    assert "networking_resource" in node_types
    edge_types = {e["type"] for e in body["data"]["edges"]}
    assert "in_network" in edge_types
```

Note: if `/graph/map` requires auth, follow the pattern in `tests/api/test_route_existence.py` for an authenticated test client; adjust the request accordingly. Confirm the JSON envelope shape (`data.nodes`/`data.edges`) against `graph.py` before finalizing assertions.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_graph_domain_nodes.py -v`
Expected: FAIL — `networking_resource` not in node types (allowlist excludes it; edge dropped).

- [ ] **Step 3: Widen the allowlist**

In `apps/api/api/v1/graph.py`, extend `VALID_RESOURCE_TYPES` (:16-24):

```python
VALID_RESOURCE_TYPES = [
    "organization",
    "entity",
    "identity",
    "project",
    "milestone",
    "issue",
    "networking_resource",
    "data_store",
    "service",
    "software",
]
```

- [ ] **Step 4: Add node materialization for the domain tables**

In the node-building section (after the `issue` block, ~line 615), add a block per new type following the `entity` block pattern (:502-526). Example for networking_resource:

```python
        if "networking_resource" in resource_types:
            nr_query = db.networking_resources.id > 0
            if org_ids_to_include:
                nr_query &= db.networking_resources.organization_id.belongs(
                    list(org_ids_to_include)
                )
            elif org_id:
                nr_query &= db.networking_resources.organization_id == org_id
            for nr in db(nr_query).select(limitby=(0, limit)):
                add_node("networking_resource", nr.id, nr.name, nr.network_type,
                         {"organization_id": nr.organization_id})
```
Repeat for `data_store` (`db.data_stores`, label `name`, subtype `storage_type`), `service` (`db.services`, subtype `deployment_method`), and `software` (`db.software`, subtype `software_type`). If `_get_node_style_by_resource` (`graph.py:443`) has no branch for these types, add a sensible default shape/color so they render.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_graph_domain_nodes.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/api/v1/graph.py tests/unit/test_graph_domain_nodes.py
git commit -m "feat(graph): render networking/data_store/service/software nodes so cloud edges show"
```

---

### Task 7: Backfill K8s edge-helper regression tests

**Files:**
- Test: `tests/unit/test_discovery_linker.py` (append)

**Interfaces:**
- Consumes: existing `_create_dependency_link`, `_upsert_network_entity_mapping` (now fixed), `_store_k8s_ingress`, `_store_k8s_pvc_as_data_store`.
- Produces: coverage guarding these previously-untested helpers.

- [ ] **Step 1: Write the tests**

Append to `tests/unit/test_discovery_linker.py`:

```python
def test_create_dependency_link_is_idempotent(seeded, app):
    service, db, org_id = seeded
    with app.app_context():
        service._create_dependency_link("entity", 1, "entity", 2, "routes_to")
        service._create_dependency_link("entity", 1, "entity", 2, "routes_to")
        db.commit()
        rows = db(
            (db.dependencies.source_id == 1) & (db.dependencies.target_id == 2)
        ).select()
        assert len(rows) == 1


def test_dependency_link_records_type(seeded, app):
    service, db, org_id = seeded
    with app.app_context():
        service._create_dependency_link("data_store", 5, "data_store", 6, "bound_to")
        db.commit()
        row = db(db.dependencies.dependency_type == "bound_to").select().first()
        assert row.source_type == "data_store" and row.target_type == "data_store"
```

- [ ] **Step 2: Run the tests**

Run: `pytest tests/unit/test_discovery_linker.py -k "dependency_link" -v`
Expected: PASS.

- [ ] **Step 3: Run the full linker + graph suites and check coverage**

Run: `pytest tests/unit/test_discovery_linker.py tests/unit/test_graph_domain_nodes.py -v`
Expected: PASS. Then run `pytest tests/unit -q` to confirm no regression in the wider suite.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_discovery_linker.py
git commit -m "test(discovery): cover dependency-link edge helpers"
```

---

## Self-review

- **Spec coverage:** two-pass engine (Task 4) ✓; native-id resolution + migration (Tasks 1–2, 4) ✓; dual-write rule §3.5 (Task 4 `_link_resources`) ✓; unresolved counting + job-result reporting §3.4 (Tasks 4–5) ✓; read-side node allowlist §3.5.3 (Task 6) ✓; K8s edge-test backfill §8 (Task 7) ✓; `network_entity_mappings` bug (surfaced during planning, Task 3) ✓. **Provider capture (spec §6) is intentionally deferred to PRs 2–5** — not in this plan.
- **Placeholder scan:** none — every step has real code or an exact anchored instruction. Two steps (Task 5 executor merge, Task 6 auth/envelope) say "adjust to actual code" because they touch a caller whose body wasn't quoted; the implementer must read that file, which is expected, not a placeholder.
- **Type consistency:** `scan_index` keys `(provider, external_id)` → `(type_string, row_id)` used identically in `_register`, `_resolve_target`, and pass 2; `_link_resources` takes the same `(type_string, row_id)` tuples; `_store_discovered_resources` returns `{"edges_created", "unresolved_edges"}` consumed by Task 5.

## Known limitations (deferred, flagged not hidden)

- `dependencies.tenant_id` is left unset by `_create_dependency_link` (pre-existing; column nullable). Tenant-scoping edges is a separate follow-up, not smuggled into this PR.
- Load balancers stored via `_store_as_networking_resource` are not yet registered in `scan_index` (no captured id in the current loop) — they become resolvable targets when provider capture lands in PR2, where their registration is added alongside the capture code.
- On real scans this PR creates **no new edges** (providers emit no `relationships` yet) — verified by the no-op path; visible edges begin in PR2.
