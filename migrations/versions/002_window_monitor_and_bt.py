import sqlalchemy as sa
from alembic import op

revision = "002_window_monitor_and_bt"
down_revision = "001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("desktop_snapshots", sa.Column("active_window_class", sa.String(), nullable=True))
    op.add_column("desktop_snapshots", sa.Column("active_window_process", sa.String(), nullable=True))
    op.add_column("desktop_snapshots", sa.Column("active_window_bounds", sa.Text(), nullable=True))
    op.add_column("desktop_snapshots", sa.Column("window_list", sa.Text(), nullable=True))

    op.add_column("execution_steps", sa.Column("visual_comparison", sa.Text(), nullable=True))
    op.add_column("execution_steps", sa.Column("verification_result", sa.Text(), nullable=True))

    op.create_table(
        "users",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="viewer"),
        sa.Column("active", sa.Integer(), server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("username"),
    )

    op.create_table(
        "audit_entries",
        sa.Column("entry_id", sa.String(), nullable=False),
        sa.Column("timestamp", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("success", sa.Integer(), server_default="1"),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("entry_id"),
    )

    op.create_table(
        "credentials",
        sa.Column("credential_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("credential_type", sa.String(), server_default="password"),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("credential_id"),
    )

    op.create_table(
        "schedules",
        sa.Column("schedule_id", sa.String(), nullable=False),
        sa.Column("automation_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("trigger_type", sa.String(), nullable=False, server_default="manual"),
        sa.Column("cron_expression", sa.String(), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), server_default="0"),
        sa.Column("event_pattern", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("last_run", sa.String(), nullable=True),
        sa.Column("next_run", sa.String(), nullable=True),
        sa.Column("run_count", sa.Integer(), server_default="0"),
        sa.Column("failure_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.String(), nullable=True),
        sa.Column("variables_json", sa.Text(), nullable=True),
        sa.Column("notification_webhook", sa.String(), nullable=True),
        sa.Column("notification_email", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("schedule_id"),
    )

    op.create_table(
        "workflow_templates",
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(), server_default="general"),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("author_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("flow_definition", sa.Text(), nullable=False),
        sa.Column("version", sa.String(), server_default="1.0.0"),
        sa.Column("downloads", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("template_id"),
    )


def downgrade() -> None:
    op.drop_table("workflow_templates")
    op.drop_table("schedules")
    op.drop_table("credentials")
    op.drop_table("audit_entries")
    op.drop_table("users")

    op.drop_column("execution_steps", "verification_result")
    op.drop_column("execution_steps", "visual_comparison")

    op.drop_column("desktop_snapshots", "window_list")
    op.drop_column("desktop_snapshots", "active_window_bounds")
    op.drop_column("desktop_snapshots", "active_window_process")
    op.drop_column("desktop_snapshots", "active_window_class")
