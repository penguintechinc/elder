"""Streams module models — playbooks, nodes, edges, executions, approvals.

Ported from IceCharts v1.1.x IceStreams (playbook automation).
- stream_playbooks: main workflow definitions
- stream_nodes: individual steps in a workflow
- stream_edges: connections between nodes
- stream_versions: versioned snapshots for rollback
- stream_webhooks: webhook triggers
- stream_executions: individual workflow run instances
- stream_node_executions: per-node execution details
- stream_schedules: cron-like scheduling
- stream_shares: sharing permissions
- stream_editor_locks: single-editor enforcement
- stream_templates: reusable workflow templates (VillageIDMixin)
- stream_forms: dynamic forms for playbooks
- stream_form_submissions: form submission history
- stream_node_metadata: node annotations and metadata
- stream_custom_modules: uploadable trigger/action modules (VillageIDMixin)
- stream_approval_gates: approval gate node configurations
- stream_execution_approvals: approval decisions for paused executions

All tables prefixed with stream_ per module manifest table_prefix.
Tenant-scoped: all have tenant_id FK → tenants.id.
Actor refs: *_identity_id (Integer FK → identities.id).
Top-level referenceable: stream_playbooks, stream_templates have VillageIDMixin.
"""

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


class StreamPlaybook(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """Playbook/workflow definition (root entity)."""

    __tablename__ = "stream_playbooks"

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    owner_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    status = Column(
        String(20),
        nullable=False,
        server_default="draft",
        comment="draft, active, paused, archived",
    )
    is_enabled = Column(Boolean, nullable=False, server_default="0")
    is_public = Column(Boolean, nullable=False, server_default="0")
    is_template = Column(Boolean, nullable=False, server_default="0")
    trigger_type = Column(
        String(50), nullable=True, comment="webhook, schedule, grpc, manual"
    )
    trigger_config = Column(JSON, nullable=True, comment="Trigger-specific config")
    error_handling = Column(JSON, nullable=True, comment="Retry, failure notifications")
    tags = Column(JSON, nullable=True, comment="Array of tag strings")
    canvas_data = Column(JSON, nullable=True, comment="ReactFlow viewport, zoom")
    execution_count = Column(Integer, nullable=False, server_default="0")
    success_count = Column(Integer, nullable=False, server_default="0")
    failure_count = Column(Integer, nullable=False, server_default="0")
    last_execution_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(
        DateTime(timezone=True), nullable=True, comment="For scheduled"
    )

    __table_args__ = (Index("ix_stream_playbooks_tenant_id", "tenant_id"),)


class StreamNode(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Playbook node/step in workflow."""

    __tablename__ = "stream_nodes"

    playbook_id = Column(
        Integer, ForeignKey("stream_playbooks.id", ondelete="CASCADE"), nullable=False
    )
    node_id = Column(String(100), nullable=False, comment="ReactFlow node ID (UUID)")
    node_type = Column(
        String(50), nullable=False, comment="trigger_*, transform_*, action_*"
    )
    node_category = Column(
        String(50),
        nullable=False,
        server_default="transform",
        comment="trigger, transform, action",
    )
    label = Column(String(255), nullable=True)
    position_x = Column(Integer, nullable=False)
    position_y = Column(Integer, nullable=False)
    config = Column(JSON, nullable=False, comment="Node-specific configuration")
    data_schema = Column(JSON, nullable=True, comment="Expected input/output schema")
    is_enabled = Column(Boolean, nullable=False, server_default="1")
    execution_order = Column(Integer, nullable=False, server_default="0")
    comments = Column(Text, nullable=True, comment="User comments on node")
    metadata_json = Column(JSON, nullable=True, comment="Key/value metadata")

    __table_args__ = (Index("ix_stream_nodes_playbook_id", "playbook_id"),)


class StreamEdge(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Connection between playbook nodes."""

    __tablename__ = "stream_edges"

    playbook_id = Column(
        Integer, ForeignKey("stream_playbooks.id", ondelete="CASCADE"), nullable=False
    )
    edge_id = Column(String(100), nullable=False, comment="ReactFlow edge ID")
    source_node_id = Column(String(100), nullable=False)
    target_node_id = Column(String(100), nullable=False)
    source_handle = Column(String(50), nullable=True, comment="Multiple outputs")
    target_handle = Column(String(50), nullable=True, comment="Multiple inputs")
    condition = Column(JSON, nullable=True, comment="Conditional edge")
    label = Column(String(255), nullable=True)

    __table_args__ = (Index("ix_stream_edges_playbook_id", "playbook_id"),)


class StreamVersion(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Versioned snapshot of playbook content."""

    __tablename__ = "stream_versions"

    playbook_id = Column(
        Integer, ForeignKey("stream_playbooks.id", ondelete="CASCADE"), nullable=False
    )
    version_number = Column(Integer, nullable=False)
    created_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    nodes_json = Column(JSON, nullable=False)
    edges_json = Column(JSON, nullable=False)
    canvas_json = Column(JSON, nullable=True)
    change_summary = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_stream_versions_playbook_id", "playbook_id"),
        UniqueConstraint("playbook_id", "version_number", name="uq_stream_version"),
    )


class StreamWebhook(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Webhook token for playbook trigger."""

    __tablename__ = "stream_webhooks"

    playbook_id = Column(
        Integer, ForeignKey("stream_playbooks.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(255), nullable=True)
    token = Column(String(255), nullable=False, unique=True)
    signature_secret = Column(String(255), nullable=True, comment="HMAC signature")
    validate_signature = Column(Boolean, nullable=False, server_default="0")
    allowed_methods = Column(JSON, nullable=True, comment="POST, GET, etc.")
    ip_whitelist = Column(JSON, nullable=True, comment="IP restriction")
    is_active = Column(Boolean, nullable=False, server_default="1")
    is_enabled = Column(Boolean, nullable=False, server_default="1")
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)
    trigger_count = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (Index("ix_stream_webhooks_playbook_id", "playbook_id"),)


class StreamExecution(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Individual playbook execution/run."""

    __tablename__ = "stream_executions"

    playbook_id = Column(
        Integer, ForeignKey("stream_playbooks.id", ondelete="CASCADE"), nullable=False
    )
    execution_id = Column(String(100), nullable=False, unique=True, comment="UUID")
    status = Column(
        String(50),
        nullable=False,
        server_default="pending",
        comment="pending, running, paused_for_approval, completed, failed, cancelled",
    )
    trigger_type = Column(
        String(50), nullable=True, comment="webhook, schedule, manual"
    )
    triggered_by = Column(
        String(50), nullable=True, comment="webhook, schedule, manual"
    )
    triggered_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    input_json = Column(JSON, nullable=True, comment="Initial trigger payload")
    output_json = Column(JSON, nullable=True, comment="Final output after completion")
    error_message = Column(Text, nullable=True)
    error_details = Column(JSON, nullable=True, comment="Stack trace, failed node")
    retry_count = Column(Integer, nullable=False, server_default="0")
    parent_execution_id = Column(String(100), nullable=True, comment="For retry chains")
    worker_id = Column(String(100), nullable=True, comment="Which worker processed")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True, comment="Execution time (ms)")

    __table_args__ = (
        Index("ix_stream_executions_playbook_id", "playbook_id"),
        Index("ix_stream_executions_execution_id", "execution_id"),
    )


class StreamNodeExecution(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Per-node execution details within a playbook execution."""

    __tablename__ = "stream_node_executions"

    execution_id = Column(
        String(100), nullable=False, comment="FK to stream_executions (string)"
    )
    node_id = Column(String(100), nullable=False)
    node_type = Column(String(50), nullable=True)
    playbook_id = Column(
        Integer, ForeignKey("stream_playbooks.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(
        String(50),
        nullable=False,
        server_default="pending",
        comment="pending, running, completed, failed, skipped",
    )
    input_json = Column(JSON, nullable=True)
    output_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    error_details = Column(JSON, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    retry_count = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        Index("ix_stream_node_executions_execution_id", "execution_id"),
        Index("ix_stream_node_executions_playbook_id", "playbook_id"),
    )


class StreamSchedule(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Cron-like scheduling for playbook."""

    __tablename__ = "stream_schedules"

    playbook_id = Column(
        Integer, ForeignKey("stream_playbooks.id", ondelete="CASCADE"), nullable=False
    )
    cron_expression = Column(String(100), nullable=False, comment="e.g., '0 9 * * 1-5'")
    timezone = Column(String(100), nullable=False, server_default="UTC")
    is_active = Column(Boolean, nullable=False, server_default="1")
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    run_count = Column(Integer, nullable=False, server_default="0")
    static_input = Column(JSON, nullable=True, comment="Static payload for scheduled")

    __table_args__ = (Index("ix_stream_schedules_playbook_id", "playbook_id"),)


class StreamShare(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Playbook-level sharing permission."""

    __tablename__ = "stream_shares"

    playbook_id = Column(
        Integer, ForeignKey("stream_playbooks.id", ondelete="CASCADE"), nullable=False
    )
    shared_with_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="CASCADE"), nullable=True
    )
    shared_with_group_id = Column(
        Integer, ForeignKey("identity_groups.id", ondelete="CASCADE"), nullable=True
    )
    shared_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    permission = Column(
        String(50),
        nullable=False,
        server_default="viewer",
        comment="viewer, editor",
    )
    expires_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_stream_shares_playbook_id", "playbook_id"),)


class StreamEditorLock(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Single-editor enforcement lock."""

    __tablename__ = "stream_editor_locks"

    playbook_id = Column(
        Integer,
        ForeignKey("stream_playbooks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    locked_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="CASCADE"), nullable=False
    )
    locked_by_name = Column(String(255), nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, comment="Auto-release")
    socket_id = Column(String(255), nullable=True, comment="WebSocket session")

    __table_args__ = (Index("ix_stream_editor_locks_playbook_id", "playbook_id"),)


class StreamTemplate(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """Reusable playbook template."""

    __tablename__ = "stream_templates"

    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(
        String(100), nullable=False, server_default="custom", comment="custom, builtin"
    )
    created_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    nodes_json = Column(JSON, nullable=False)
    edges_json = Column(JSON, nullable=False)
    canvas_data = Column(JSON, nullable=True)
    is_public = Column(Boolean, nullable=False, server_default="0")
    usage_count = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (Index("ix_stream_templates_tenant_id", "tenant_id"),)


class StreamForm(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Dynamic form for playbook."""

    __tablename__ = "stream_forms"

    playbook_id = Column(
        Integer, ForeignKey("stream_playbooks.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    fields_json = Column(JSON, nullable=False, comment="Form field definitions")
    form_token = Column(
        String(255), nullable=True, unique=True, comment="Public access"
    )
    access_type = Column(
        String(50),
        nullable=False,
        server_default="registered",
        comment="public, registered, specific",
    )
    allowed_users = Column(JSON, nullable=True, comment="For 'specific' access")
    is_active = Column(Boolean, nullable=False, server_default="1")
    submission_count = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (Index("ix_stream_forms_playbook_id", "playbook_id"),)


class StreamFormSubmission(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Form submission record."""

    __tablename__ = "stream_form_submissions"

    form_id = Column(
        Integer, ForeignKey("stream_forms.id", ondelete="CASCADE"), nullable=False
    )
    playbook_id = Column(
        Integer, ForeignKey("stream_playbooks.id", ondelete="CASCADE"), nullable=False
    )
    submitted_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    submission_data = Column(JSON, nullable=False)
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(500), nullable=True)
    execution_id = Column(String(100), nullable=True, comment="If triggered execution")

    __table_args__ = (
        Index("ix_stream_form_submissions_form_id", "form_id"),
        Index("ix_stream_form_submissions_playbook_id", "playbook_id"),
    )


class StreamNodeMetadata(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Node annotation and metadata."""

    __tablename__ = "stream_node_metadata"

    playbook_id = Column(
        Integer, ForeignKey("stream_playbooks.id", ondelete="CASCADE"), nullable=False
    )
    node_id = Column(String(100), nullable=False)
    comments = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True, comment="Key/value pairs")
    updated_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (Index("ix_stream_node_metadata_playbook_id", "playbook_id"),)


class StreamCustomModule(
    Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin
):
    """Uploadable trigger/action module."""

    __tablename__ = "stream_custom_modules"

    name = Column(String(255), nullable=False, unique=True)
    display_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    module_type = Column(String(50), nullable=False, comment="trigger, action")
    version = Column(String(50), nullable=False, server_default="1.0.0")
    code_blob = Column(Text, nullable=True)
    config_schema = Column(JSON, nullable=True, comment="JSON Schema for config")
    input_schema = Column(JSON, nullable=True, comment="Expected input schema")
    output_schema = Column(JSON, nullable=True, comment="Expected output schema")
    is_validated = Column(Boolean, nullable=False, server_default="0")
    validation_errors = Column(JSON, nullable=True, comment="Validation errors")
    is_enabled = Column(Boolean, nullable=False, server_default="1")
    uploaded_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    usage_count = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (Index("ix_stream_custom_modules_tenant_id", "tenant_id"),)


class StreamApprovalGate(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Approval gate node configuration."""

    __tablename__ = "stream_approval_gates"

    playbook_id = Column(
        Integer, ForeignKey("stream_playbooks.id", ondelete="CASCADE"), nullable=False
    )
    gate_id = Column(String(100), nullable=False, unique=True)
    node_id = Column(String(100), nullable=False, comment="Associated node ID")
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    require_approval = Column(Boolean, nullable=False, server_default="1")
    min_approvers = Column(Integer, nullable=False, server_default="1")
    approvers = Column(JSON, nullable=True, comment="List of identity IDs")
    approver_groups = Column(JSON, nullable=True, comment="List of group IDs")
    timeout_minutes = Column(Integer, nullable=True, comment="Approval timeout")
    is_enabled = Column(Boolean, nullable=False, server_default="1")

    __table_args__ = (
        Index("ix_stream_approval_gates_playbook_id", "playbook_id"),
        Index("ix_stream_approval_gates_gate_id", "gate_id"),
    )


class StreamExecutionApproval(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Approval decision for paused execution."""

    __tablename__ = "stream_execution_approvals"

    execution_id = Column(
        String(100), nullable=False, comment="FK to stream_executions"
    )
    gate_id = Column(
        Integer,
        ForeignKey("stream_approval_gates.id", ondelete="CASCADE"),
        nullable=True,
    )
    approver_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    approval_id = Column(String(100), nullable=False, unique=True, comment="UUID")
    decision = Column(String(20), nullable=False, comment="approve, reject")
    comment = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_stream_execution_approvals_execution_id", "execution_id"),
        Index("ix_stream_execution_approvals_gate_id", "gate_id"),
    )
