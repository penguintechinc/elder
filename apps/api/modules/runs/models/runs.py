"""SQLAlchemy / penguin-dal models for Elder Runs serverless functions.

Tables:
- run_functions: serverless function definitions (VillageIDMixin, tenant-scoped)
- run_function_versions: immutable code/runtime version history per function
- run_executions: invocation records, logs, outputs, and worker status
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from apps.api.models.base import (
    Base,
    IDMixin,
    TenantScopedMixin,
    TimestampMixin,
    VillageIDMixin,
)

SUPPORTED_RUNTIMES: frozenset[str] = frozenset(
    {"nodejs", "rust", "python", "golang", "ruby"}
)
EXECUTION_STATUSES: frozenset[str] = frozenset(
    {"pending", "running", "completed", "failed", "timed_out"}
)
TERMINAL_EXECUTION_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "timed_out"}
)
TRIGGER_TYPES: frozenset[str] = frozenset({"manual", "schedule", "stream"})


class RunFunction(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """Serverless function definition executed by svc-runworker."""

    __tablename__ = "run_functions"

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    runtime = Column(
        String(32),
        nullable=False,
        index=True,
        comment="nodejs | rust | python | golang | ruby",
    )
    code = Column(Text, nullable=False)
    storage_backend = Column(
        String(32),
        nullable=False,
        default="database",
        server_default="database",
        comment="database | s3",
    )
    storage_key = Column(String(512), nullable=True)
    code_sha256 = Column(String(64), nullable=True)
    entrypoint = Column(String(255), nullable=True)
    timeout_seconds = Column(Integer, nullable=False, default=60, server_default="60")
    memory_mb = Column(Integer, nullable=False, default=256, server_default="256")
    env_vars_json = Column(JSON, nullable=True)
    granted_scopes_json = Column(JSON, nullable=True)
    allowed_egress_domains_json = Column(JSON, nullable=True)
    schedule_cron = Column(String(128), nullable=True)
    execution_namespace = Column(
        String(128),
        nullable=False,
        default="elder-runs",
        server_default="elder-runs",
    )
    worker_cluster = Column(String(128), nullable=True, index=True)
    is_enabled = Column(Boolean, nullable=False, default=True, server_default="1")
    created_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        Index("ix_run_functions_tenant_id", "tenant_id"),
        Index("ix_run_functions_village_id", "village_id", unique=True),
    )


class RunFunctionVersion(Base, IDMixin, TenantScopedMixin):
    """Immutable version snapshot created on function creation and code updates."""

    __tablename__ = "run_function_versions"

    function_id = Column(
        Integer, ForeignKey("run_functions.id", ondelete="CASCADE"), nullable=False
    )
    version_number = Column(Integer, nullable=False)
    code = Column(Text, nullable=False)
    storage_backend = Column(
        String(32),
        nullable=False,
        default="database",
        server_default="database",
        comment="database | s3",
    )
    storage_key = Column(String(512), nullable=True)
    code_sha256 = Column(String(64), nullable=True)
    runtime = Column(String(32), nullable=False)
    entrypoint = Column(String(255), nullable=True)
    granted_scopes_json = Column(JSON, nullable=True)
    created_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "function_id",
            "version_number",
            name="uq_run_function_versions_func_ver",
        ),
        Index("ix_run_function_versions_tenant_id", "tenant_id"),
        Index("ix_run_function_versions_function_id", "function_id"),
    )


class RunExecution(Base, IDMixin, TenantScopedMixin):
    """Single invocation record of a RunFunction."""

    __tablename__ = "run_executions"

    execution_id = Column(String(64), nullable=False, unique=True)
    function_id = Column(
        Integer, ForeignKey("run_functions.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(
        String(32),
        nullable=False,
        default="pending",
        server_default="pending",
        index=True,
        comment="pending | running | completed | failed | timed_out",
    )
    trigger_type = Column(
        String(32),
        nullable=False,
        default="manual",
        server_default="manual",
        comment="manual | schedule | stream",
    )
    triggered_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    worker_id = Column(String(128), nullable=True)
    input_json = Column(JSON, nullable=True)
    output_json = Column(JSON, nullable=True)
    stdout_log = Column(Text, nullable=True)
    stderr_log = Column(Text, nullable=True)
    exit_code = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_run_executions_tenant_id", "tenant_id"),
        Index("ix_run_executions_execution_id", "execution_id", unique=True),
        Index("ix_run_executions_function_id", "function_id"),
    )
