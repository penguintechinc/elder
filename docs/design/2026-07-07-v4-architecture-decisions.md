# Elder v4.0 — Architecture Decisions (Approved Exceptions & Conventions)

Decisions taken during the v4.0 modular-platform merge that intentionally deviate
from, or refine, the company-wide house standards. Recorded here per the house rule
requiring documented justification for exceptions.

---

## AD-1: Generic `shared/` infra stays local (penguin-libs migration waived)

**Decision:** Elder keeps its generic infrastructure utilities **local** in `shared/`
rather than migrating them to the published `penguin-libs` packages, as an approved
one-time exception for this project.

**Affected files:** `shared/logging/logger.py` (structured logging + Kafka/CloudWatch/
GCP handlers), `shared/observability.py` (OpenTelemetry SDK init/export), `shared/api_utils.py`,
`shared/database/connection.py` + `manager.py` (pooling/retry/replica routing over penguin-dal).

**Why:** The house rule ("use published penguin-libs packages; no local copies of generic
utils") normally applies. For the v4 merge, migrating these would require cross-repo changes
to `penguin-libs` (publish new versions) plus re-integration in Elder — coordination overhead
that isn't justified while the v4 platform is still consolidating. Keeping them local keeps
the merge self-contained. Revisit post-v4 if these utilities are needed by other products.

**Scope:** This waiver covers ONLY the four areas above. It does NOT waive the mandate to use
`penguin-aaa`, `penguin-dal`, `penguin-licensing`, etc. as runtime dependencies.

---

## AD-2: Multi-layer data validation (three intentional object layers)

**Decision:** Elder deliberately maintains **three** representations of domain data, each
for a distinct purpose. This is defense-in-depth, NOT accidental duplication — do not collapse
them.

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **API boundary** | **Pydantic** | Validate/serialize untrusted request & response payloads |
| **Internal transfer** | **`@dataclass(slots=True)`** | Fast, low-memory objects passed between services/functions |
| **Persistence** | **SQLAlchemy** | Database schema + ORM (Alembic is schema authority; penguin-dal at runtime) |

**Tiebreaker:** If a given object genuinely does not need all three layers (the extra layers
aren't earning their keep), prefer **Pydantic** as the single representation.

**Implication for the DRY consolidation audit:** the audit's "reconcile dataclasses vs pydantic
DTOs" recommendation is **overridden** — the parallel definitions are intentional. Consolidation
effort instead ensures each layer is applied consistently for its purpose (Pydantic at API edges,
dataclass-slots for internal passing, SQLAlchemy for DB), not the elimination of a layer.

---

## AD-3: Canonical `village_id` format — `{tenant:8hex}-{object:16hex}`

**Decision:** Every referenceable object (entities, resources, workflows, documents, pages,
diagrams, tickets, …) carries a unique `village_id` in this format:

```
TTTTTTTT-OOOOOOOOOOOOOOOO      e.g. 0000002a-000000000000f3c1
|        |
|        +- object: 64-bit, unique WITHIN the tenant (16 hex)
+- tenant: 32-bit = hex(tenant_id) (8 hex)
```

- **Length:** 24 hex + 1 dash = **25 chars → fits `String(32)`** (the existing `VillageIDMixin`
  column; no widening / no migration).
- **tenant** (32-bit, `hex(tenant_id)`): 4.29B customers. Self-describing — enables cheap
  tenant-scope validation straight from the id.
- **object** (64-bit, unique within tenant): **sequentially allocated** via a per-tenant counter
  (`INCR elder:vid:{tenant}` in Redis; DB sequence acceptable for durability). 1.8×10¹⁹ objects
  per tenant — inexhaustible. Sequential (not random) keeps the id short *and* collision-free
  (random would need 128-bit to avoid birthday collisions at volume).
- **(tenant, object) is globally unique**, so the full id is globally unique.

**No `collection` segment.** Objects can belong to more than one tenant, so a tenant-scoped
grouping baked into the id doesn't fit; grouping is handled separately (membership tables).

**Multi-tenant objects:** the tenant segment = the object's **home/owning tenant**. A shared
object keeps its original id; cross-tenant access is via the Phase 2 cross-reference registry +
permissions, never by re-minting the id.

**Enumerability:** sequential ids are guessable (`…-0001`, `…-0002`). Acceptable — every request
is authorized by tenant + scope, so enumeration is not a security boundary. If unguessability is
ever required for a specific object type, that object's segment reverts to random (and reclaims
its bit budget).

**Supersedes** the legacy `TTTT-OOOO-IIIIIIII` (tenant-org-instance) format. Generator rewrite
(`shared/utils/village_id.py`) + the resolver land in **Phase 2** (cross-reference core).
