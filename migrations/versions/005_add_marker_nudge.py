"""Add marker nudge offsets + audio_service_index (Phase 5)

Revision ID: 005
Revises: 004
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("markers") as batch_op:
        batch_op.add_column(
            sa.Column("start_nudge", sa.Float(), server_default=sa.text("0.0"), nullable=False)
        )
        batch_op.add_column(
            sa.Column("end_nudge", sa.Float(), server_default=sa.text("0.0"), nullable=False)
        )
        batch_op.add_column(
            sa.Column("audio_service_index", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("markers") as batch_op:
        batch_op.drop_column("audio_service_index")
        batch_op.drop_column("end_nudge")
        batch_op.drop_column("start_nudge")
