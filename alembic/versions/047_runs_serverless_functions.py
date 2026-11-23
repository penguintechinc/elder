"""Elder Runs serverless functions, versions, and executions tables.

Revision ID: 047
Revises: 046
Create Date: 2026-11-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create run_functions, run_function_versions, and run_executions tables."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    if "run_functions" not in existing_tables:
        op.create_table(
            "run_functions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id"),
                nullable=False,
            ),
            sa.Column("village_id", sa.String(32), nullable=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "runtime",
                sa.String(32),
                nullable=False,
                comment="nodejs | rust | python | golang | ruby",
            ),
            sa.Column("code", sa.Text(), nullable=False),
            sa.Column("entrypoint", sa.String(255), nullable=True),
            sa.Column(
                "timeout_seconds",
                sa.Integer(),
                nullable=False,
                server_default="60",
            ),
            sa.Column(
                "memory_mb",
                sa.Integer(),
                nullable=False,
                server_default="256",
            ),
            sa.Column("env_vars_json", sa.JSON(), nullable=True),
            sa.Column("schedule_cron", sa.String(128), nullable=True),
            sa.Column(
                "execution_namespace",
                sa.String(128),
                nullable=False,
                server_default="elder-runs",
            ),
            sa.Column("worker_cluster", sa.String(128), nullable=True),
            sa.Column(
                "is_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            sa.Column(
                "created_by_identity_id",
                sa.Integer(),
                sa.ForeignKey("identities.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=True,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=True,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_run_functions_tenant_id", "run_functions", ["tenant_id"])
        op.create_index(
            "ix_run_functions_village_id",
            "run_functions",
            ["village_id"],
            unique=True,
        )
        op.create_index("ix_run_functions_runtime", "run_functions", ["runtime"])
        op.create_index(
            "ix_run_functions_worker_cluster", "run_functions", ["worker_cluster"]
        )

    if "run_function_versions" not in existing_tables:
        op.create_table(
            "run_function_versions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id"),
                nullable=False,
            ),
            sa.Column(
                "function_id",
                sa.Integer(),
                sa.ForeignKey("run_functions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("code", sa.Text(), nullable=False),
            sa.Column("runtime", sa.String(32), nullable=False),
            sa.Column("entrypoint", sa.String(255), nullable=True),
            sa.Column(
                "created_by_identity_id",
                sa.Integer(),
                sa.ForeignKey("identities.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=True,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "function_id",
                "version_number",
                name="uq_run_function_versions_func_ver",
            ),
        )
        op.create_index(
            "ix_run_function_versions_tenant_id",
            "run_function_versions",
            ["tenant_id"],
        )
        op.create_index(
            "ix_run_function_versions_function_id",
            "run_function_versions",
            ["function_id"],
        )

    if "run_executions" not in existing_tables:
        op.create_table(
            "run_executions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id"),
                nullable=False,
            ),
            sa.Column("execution_id", sa.String(64), nullable=False),
            sa.Column(
                "function_id",
                sa.Integer(),
                sa.ForeignKey("run_functions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="pending",
                comment="pending | running | completed | failed | timed_out",
            ),
            sa.Column(
                "trigger_type",
                sa.String(32),
                nullable=False,
                server_default="manual",
                comment="manual | schedule | stream",
            ),
            sa.Column(
                "triggered_by_identity_id",
                sa.Integer(),
                sa.ForeignKey("identities.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("worker_id", sa.String(128), nullable=True),
            sa.Column("input_json", sa.JSON(), nullable=True),
            sa.Column("output_json", sa.JSON(), nullable=True),
            sa.Column("stdout_log", sa.Text(), nullable=True),
            sa.Column("stderr_log", sa.Text(), nullable=True),
            sa.Column("exit_code", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=True,
                server_default=sa.func.now(),
            ),
        )
        op.create_index("ix_run_executions_tenant_id", "run_executions", ["tenant_id"])
        op.create_index(
            "ix_run_executions_execution_id",
            "run_executions",
            ["execution_id"],
            unique=True,
        )
        op.create_index(
            "ix_run_executions_function_id", "run_executions", ["function_id"]
        )
        op.create_index("ix_run_executions_status", "run_executions", ["status"])

    if conn.dialect.name == "postgresql":
        fn_exists = conn.execute(
            sa.text("SELECT 1 FROM pg_proc WHERE proname = 'sync_village_ids_registry'")
        ).first()
        if fn_exists is not None:
            conn.execute(
                sa.text(
                    "DROP TRIGGER IF EXISTS trg_run_functions_sync_village_ids "
                    "ON run_functions"
                )
            )
            conn.execute(
                sa.text(
                    "CREATE TRIGGER trg_run_functions_sync_village_ids "
                    "AFTER INSERT OR UPDATE OF village_id OR DELETE ON run_functions "
                    "FOR EACH ROW EXECUTE FUNCTION sync_village_ids_registry()"
                )
            )


def downgrade() -> None:
    """Drop run_executions, run_function_versions, and run_functions tables."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = set(inspector.get_table_names())

    if conn.dialect.name == "postgresql" and "run_functions" in existing_tables:
        conn.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS trg_run_functions_sync_village_ids "
                "ON run_functions"
            )
        )

    if "run_executions" in existing_tables:
        op.drop_table("run_executions")
    if "run_function_versions" in existing_tables:
        op.drop_table("run_function_versions")
    if "run_functions" in existing_tables:
        op.drop_table("run_functions")
