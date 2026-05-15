import sqlalchemy as sa
from alembic import op

revision = "001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("tags", sa.Text(), server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="created"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("stopped_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), server_default="0"),
        sa.Column("operation_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "operations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("seq_num", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_operations_session", "operations", ["session_id", "seq_num"])

    op.create_table(
        "automations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("source_session_id", sa.String(), nullable=True),
        sa.Column("source_pattern", sa.Text(), nullable=True),
        sa.Column("flow_definition", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), server_default="0.0"),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("execution_count", sa.Integer(), server_default="0"),
        sa.Column("success_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["source_session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "executions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("automation_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("total_steps", sa.Integer(), server_default="0"),
        sa.Column("completed_steps", sa.Integer(), server_default="0"),
        sa.Column("failed_steps", sa.Integer(), server_default="0"),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("variables", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["automation_id"], ["automations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "execution_steps",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("execution_id", sa.String(), nullable=False),
        sa.Column("step_id", sa.String(), nullable=False),
        sa.Column("step_type", sa.String(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("screenshot_before", sa.Text(), nullable=True),
        sa.Column("screenshot_after", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_exec_steps", "execution_steps", ["execution_id", "step_index"])

    op.create_table(
        "desktop_snapshots",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("timestamp", sa.Integer(), nullable=False),
        sa.Column("active_window_title", sa.String(), nullable=True),
        sa.Column("active_window_app", sa.String(), nullable=True),
        sa.Column("window_count", sa.Integer(), server_default="0"),
        sa.Column("screenshot_path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("desktop_snapshots")
    op.drop_table("execution_steps")
    op.drop_table("executions")
    op.drop_table("automations")
    op.drop_table("operations")
    op.drop_table("sessions")
