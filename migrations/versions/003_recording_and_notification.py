"""recording_groups + notification_configs + semantic_annotations

Revision ID: 003
Revises: 002_window_monitor_and_bt
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002_window_monitor_and_bt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recording_groups",
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_application", sa.String(), nullable=True),
        sa.Column("target_workflow", sa.String(), nullable=True),
        sa.Column("min_recordings", sa.Integer(), server_default="2"),
        sa.Column("status", sa.String(), server_default="collecting"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("group_id"),
    )

    op.create_table(
        "recording_sessions",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("status", sa.String(), server_default="pending"),
        sa.Column("operation_count", sa.Integer(), server_default="0"),
        sa.Column("duration_ms", sa.Integer(), server_default="0"),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("session_id"),
        sa.ForeignKeyConstraint(["group_id"], ["recording_groups.group_id"]),
    )

    op.create_table(
        "fusion_results",
        sa.Column("result_id", sa.String(), nullable=False),
        sa.Column("group_id", sa.String(), nullable=False),
        sa.Column("common_steps", sa.Text(), nullable=True),
        sa.Column("branches", sa.Text(), nullable=True),
        sa.Column("loop_patterns", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), server_default="0.0"),
        sa.Column("flow_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("result_id"),
        sa.ForeignKeyConstraint(["group_id"], ["recording_groups.group_id"]),
    )

    op.create_table(
        "notification_configs",
        sa.Column("config_id", sa.String(), nullable=False),
        sa.Column("config_type", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("secret", sa.String(), nullable=True),
        sa.Column("smtp_host", sa.String(), nullable=True),
        sa.Column("smtp_port", sa.Integer(), server_default="587"),
        sa.Column("smtp_user", sa.String(), nullable=True),
        sa.Column("smtp_password_encrypted", sa.String(), nullable=True),
        sa.Column("from_address", sa.String(), nullable=True),
        sa.Column("to_addresses", sa.Text(), nullable=True),
        sa.Column("use_tls", sa.Integer(), server_default="1"),
        sa.Column("enabled", sa.Integer(), server_default="0"),
        sa.Column("events", sa.Text(), nullable=True),
        sa.Column("headers", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("config_id"),
    )

    op.create_table(
        "semantic_annotations",
        sa.Column("annotation_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("operation_index", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("intent", sa.String(), nullable=True),
        sa.Column("semantic_tags", sa.Text(), nullable=True),
        sa.Column("ui_elements", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), server_default="0.0"),
        sa.Column("status", sa.String(), server_default="pending"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("annotation_id"),
    )


def downgrade() -> None:
    op.drop_table("semantic_annotations")
    op.drop_table("notification_configs")
    op.drop_table("fusion_results")
    op.drop_table("recording_sessions")
    op.drop_table("recording_groups")
