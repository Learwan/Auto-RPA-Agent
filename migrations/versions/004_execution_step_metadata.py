"""add execution step metadata column

Revision ID: 004
Revises: 003
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("execution_steps", sa.Column("metadata_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("execution_steps", "metadata_json")