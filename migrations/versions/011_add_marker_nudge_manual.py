"""Add markers.nudge_manual — hand-set boundaries beat the automation

Revision ID: 011
Revises: 010
Create Date: 2026-09-20

The boundary correction resets start_nudge/end_nudge before writing its own values, which
silently threw away offsets the operator had dialled in during the service. Markers flagged
here are skipped by that correction entirely.
"""
from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("markers") as batch_op:
        batch_op.add_column(
            sa.Column("nudge_manual", sa.Boolean(), server_default=sa.text("0"), nullable=False)
        )
    # No backfill: existing offsets could equally come from an earlier automatic run, and
    # flagging those would freeze them forever. Old rows start unflagged.


def downgrade() -> None:
    with op.batch_alter_table("markers") as batch_op:
        batch_op.drop_column("nudge_manual")
