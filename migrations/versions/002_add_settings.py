"""Add settings table

Revision ID: 002
Revises: 7df4dac945c0
Create Date: 2026-03-29

"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "7df4dac945c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("settings")
