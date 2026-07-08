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
