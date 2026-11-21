"""Flows module models — CI/CD pipelines, stages, approvals, executions.

Ported from IceFlows v1.1.x (CI/CD pipeline orchestration).
- iceflows: main pipeline/flow definitions
- iceflows_stages: ordered pipeline stages (dev, staging, prod)
- iceflows_stage_approvers: approval authority per stage
- iceflows_stage_tests: test execution configuration per stage
- iceflows_stage_calls: external service calls (IceStreams/IceRuns) per stage
- iceflows_stage_reviews: Darwin AI code review configuration per stage
- iceflows_credentials: Git provider credentials (GitHub/GitLab tokens)
- iceflows_promotions: promotion requests between stages
- iceflows_approvals: approval decisions for promotions
- iceflows_webhooks: GitHub/GitLab webhook triggers
- iceflows_executions: pipeline run records (reserved for future implementation)

All tables prefixed with iceflows_ per module manifest table_prefix.
Tenant-scoped: all have tenant_id FK → tenants.id.
Actor refs: *_identity_id (Integer FK → identities.id).
Top-level referenceable: iceflows (pipelines) has VillageIDMixin.
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
)

from apps.api.models.base import (
    Base,
    IDMixin,
    TenantScopedMixin,
    TimestampMixin,
    VillageIDMixin,
)


class IceFlows(Base, IDMixin, TenantScopedMixin, VillageIDMixin, TimestampMixin):
    """Pipeline/flow definition (root entity for CI/CD orchestration)."""

    __tablename__ = "iceflows"

    flow_id = Column(String(36), nullable=False, comment="UUID flow identifier")
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    repository_url = Column(String(500), nullable=False, comment="Git repository URL")
    repository_provider = Column(
        String(50), nullable=False, comment="github, gitlab, bitbucket"
    )
    repository_name = Column(
        String(255), nullable=True, comment="Extracted from repository_url"
    )
    default_branch = Column(String(255), nullable=True, server_default="main")
    credential_id = Column(
        Integer,
        ForeignKey("iceflows_credentials.id", ondelete="SET NULL"),
        nullable=True,
        comment="Git provider credential reference",
    )
    gitops_enabled = Column(Boolean, nullable=False, server_default="0")
    gitops_repo_url = Column(String(500), nullable=True)
    gitops_branch = Column(String(255), nullable=True, server_default="main")
    gitops_path = Column(String(500), nullable=True, comment="Path within GitOps repo")
    webhook_secret = Column(String(64), nullable=True, comment="Webhook signing secret")
    status = Column(
        String(50),
        nullable=False,
        server_default="draft",
        comment="draft, active, paused, archived",
    )
    is_enabled = Column(Boolean, nullable=False, server_default="1")
    created_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="RESTRICT"), nullable=False
    )
    tags = Column(JSON, nullable=True, comment="Array of tag strings")

    __table_args__ = (Index("ix_iceflows_tenant_id", "tenant_id"),)


class IceFlowsStage(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Pipeline stage (e.g., dev, staging, production)."""

    __tablename__ = "iceflows_stages"

    stage_id = Column(String(36), nullable=False, comment="UUID stage identifier")
    flow_id = Column(
        Integer, ForeignKey("iceflows.id", ondelete="CASCADE"), nullable=False
    )
    stage_order = Column(Integer, nullable=False, comment="Execution order within flow")
    branch_name = Column(
        String(255), nullable=False, comment="Git branch for this stage"
    )
    display_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    is_production = Column(Boolean, nullable=False, server_default="0")
    auto_promote = Column(Boolean, nullable=False, server_default="0")
    require_approval = Column(Boolean, nullable=False, server_default="1")
    min_approvers = Column(Integer, nullable=False, server_default="1")
    override_min_approvers = Column(Integer, nullable=False, server_default="2")
    day_restrictions = Column(
        JSON,
        nullable=True,
        comment="Blocked days: {blocked_days: [0,5,6]} for Sun/Fri/Sat",
    )
    time_restrictions = Column(
        JSON,
        nullable=True,
        comment="Time window: {start_hour, end_hour, timezone}",
    )
    notification_config = Column(JSON, nullable=True, comment="Notification settings")
    is_enabled = Column(Boolean, nullable=False, server_default="1")

    __table_args__ = (Index("ix_iceflows_stages_flow_id", "flow_id"),)


class IceFlowsStageApprover(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Approval authority for a pipeline stage."""

    __tablename__ = "iceflows_stage_approvers"

    approver_id = Column(String(36), nullable=False, comment="UUID approver identifier")
    stage_id = Column(
        Integer, ForeignKey("iceflows_stages.id", ondelete="CASCADE"), nullable=False
    )
    identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="CASCADE"), nullable=True
    )
    group_id = Column(
        Integer,
        nullable=True,
        comment="Soft ref to a group/team (no groups table in Elder yet)",
    )
    role = Column(
        String(50),
        nullable=False,
        server_default="approver",
        comment="approver, admin, reviewer",
    )
    can_override = Column(
        Boolean,
        nullable=False,
        server_default="0",
        comment="Can override min_approvers",
    )

    __table_args__ = (Index("ix_iceflows_stage_approvers_stage_id", "stage_id"),)


class IceFlowsStageTest(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Test execution configuration for a pipeline stage."""

    __tablename__ = "iceflows_stage_tests"

    test_id = Column(String(36), nullable=False, comment="UUID test identifier")
    stage_id = Column(
        Integer, ForeignKey("iceflows_stages.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(255), nullable=False)
    test_type = Column(
        String(50),
        nullable=False,
        comment="unit, integration, e2e, custom",
    )
    path_mode = Column(
        String(50),
        nullable=False,
        server_default="repo_relative",
        comment="centralized or repo_relative",
    )
    centralized_path = Column(
        String(500), nullable=True, comment="Path when path_mode=centralized"
    )
    repo_relative_path = Column(
        String(500), nullable=True, comment="Path when path_mode=repo_relative"
    )
    command = Column(Text, nullable=True, comment="Custom test command")
    timeout_seconds = Column(Integer, nullable=False, server_default="600")
    is_blocking = Column(Boolean, nullable=False, server_default="1")
    is_required = Column(Boolean, nullable=False, server_default="1")
    execution_order = Column(Integer, nullable=False, server_default="0")
    env_vars = Column(JSON, nullable=True, comment="Environment variables")

    __table_args__ = (Index("ix_iceflows_stage_tests_stage_id", "stage_id"),)


class IceFlowsStageCall(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """External service call configuration (IceStreams playbook or IceRuns function)."""

    __tablename__ = "iceflows_stage_calls"

    call_id = Column(String(36), nullable=False, comment="UUID call identifier")
    stage_id = Column(
        Integer, ForeignKey("iceflows_stages.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String(255), nullable=False)
    call_type = Column(
        String(50),
        nullable=False,
        comment="icestreams (playbook) or iceruns (function)",
    )
    target_id = Column(
        String(50),
        nullable=False,
        comment="Playbook or function ID (Integer as string)",
    )
    trigger_on = Column(
        String(50),
        nullable=False,
        server_default="on_promotion",
        comment="pre_merge, post_merge, on_approval, on_promotion",
    )
    input_template = Column(JSON, nullable=True, comment="Input template JSON")
    timeout_seconds = Column(Integer, nullable=False, server_default="300")
    is_blocking = Column(Boolean, nullable=False, server_default="1")
    retry_count = Column(Integer, nullable=False, server_default="0")
    execution_order = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (Index("ix_iceflows_stage_calls_stage_id", "stage_id"),)


class IceFlowsStageReview(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Darwin AI code review configuration for a pipeline stage."""

    __tablename__ = "iceflows_stage_reviews"

    review_id = Column(String(36), nullable=False, comment="UUID review identifier")
    stage_id = Column(
        Integer,
        ForeignKey("iceflows_stages.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        comment="One review config per stage",
    )
    is_required = Column(Boolean, nullable=False, server_default="1")
    review_type = Column(
        String(50),
        nullable=False,
        server_default="inherit",
        comment="inherit, standard, security, performance, full",
    )
    min_score = Column(Integer, nullable=False, server_default="70")
    block_on_critical = Column(Boolean, nullable=False, server_default="1")
    allowed_issue_types = Column(
        JSON, nullable=True, comment="Array of allowed issue types"
    )
    reviewers_notified = Column(Boolean, nullable=False, server_default="1")

    __table_args__ = (Index("ix_iceflows_stage_reviews_stage_id", "stage_id"),)


class IceFlowsCredential(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Git provider credential (token) for repository access."""

    __tablename__ = "iceflows_credentials"

    credential_id = Column(
        String(36), nullable=False, comment="UUID credential identifier"
    )
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    provider = Column(String(50), nullable=False, comment="github or gitlab")
    access_token = Column(
        Text, nullable=False, comment="SENSITIVE: Git provider access token/PAT"
    )
    token_type = Column(
        String(50),
        nullable=False,
        server_default="personal",
        comment="personal, oauth, app",
    )
    scopes = Column(JSON, nullable=True, comment="Array of token scopes/permissions")
    expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="1")
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (Index("ix_iceflows_credentials_tenant_id", "tenant_id"),)


class IceFlowsPromotion(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Promotion request between stages (e.g., dev→staging, staging→prod)."""

    __tablename__ = "iceflows_promotions"

    promotion_id = Column(
        String(36), nullable=False, comment="UUID promotion identifier"
    )
    flow_id = Column(
        Integer, ForeignKey("iceflows.id", ondelete="CASCADE"), nullable=False
    )
    source_stage_id = Column(
        Integer, ForeignKey("iceflows_stages.id", ondelete="RESTRICT"), nullable=False
    )
    target_stage_id = Column(
        Integer, ForeignKey("iceflows_stages.id", ondelete="RESTRICT"), nullable=False
    )
    source_commit = Column(String(100), nullable=True, comment="Git commit SHA")
    status = Column(
        String(50),
        nullable=False,
        server_default="pending",
        comment="pending, approved, rejected, merged, cancelled",
    )
    requested_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    merged_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    merged_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_iceflows_promotions_flow_id", "flow_id"),
        Index("ix_iceflows_promotions_status", "status"),
    )


class IceFlowsApproval(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Approval decision record for a promotion."""

    __tablename__ = "iceflows_approvals"

    approval_id = Column(String(36), nullable=False, comment="UUID approval identifier")
    promotion_id = Column(
        Integer,
        ForeignKey("iceflows_promotions.id", ondelete="CASCADE"),
        nullable=False,
    )
    approver_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="RESTRICT"), nullable=False
    )
    decision = Column(String(50), nullable=False, comment="approve or reject")
    comment = Column(Text, nullable=True, comment="Approval comment/reason")
    can_override = Column(Boolean, nullable=False, server_default="0")
    approved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_iceflows_approvals_promotion_id", "promotion_id"),)


class IceFlowsWebhook(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """GitHub/GitLab webhook trigger for automatic promotions."""

    __tablename__ = "iceflows_webhooks"

    webhook_id = Column(String(36), nullable=False, comment="UUID webhook identifier")
    flow_id = Column(
        Integer, ForeignKey("iceflows.id", ondelete="CASCADE"), nullable=False
    )
    provider = Column(String(50), nullable=False, comment="github or gitlab")
    webhook_secret = Column(
        String(64), nullable=False, comment="Signature verification"
    )
    is_active = Column(Boolean, nullable=False, server_default="1")
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_iceflows_webhooks_flow_id", "flow_id"),)


class IceFlowsExecution(Base, IDMixin, TenantScopedMixin, TimestampMixin):
    """Pipeline execution record (run instance — reserved for future implementation)."""

    __tablename__ = "iceflows_executions"

    execution_id = Column(
        String(36), nullable=False, comment="UUID execution identifier"
    )
    promotion_id = Column(
        Integer,
        ForeignKey("iceflows_promotions.id", ondelete="SET NULL"),
        nullable=True,
    )
    flow_id = Column(
        Integer, ForeignKey("iceflows.id", ondelete="CASCADE"), nullable=False
    )
    status = Column(
        String(50),
        nullable=False,
        server_default="pending",
        comment="pending, in_progress, success, failed, cancelled",
    )
    started_by_identity_id = Column(
        Integer, ForeignKey("identities.id", ondelete="SET NULL"), nullable=True
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    execution_log = Column(JSON, nullable=True, comment="Structured execution log")

    __table_args__ = (
        Index("ix_iceflows_executions_flow_id", "flow_id"),
        Index("ix_iceflows_executions_promotion_id", "promotion_id"),
    )
