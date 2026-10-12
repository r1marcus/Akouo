"""Add markers.ended_at_ts — explicit track end (REC-08)

Revision ID: 009
Revises: 008
Create Date: 2026-09-18

Markers now span a range: the operator starts a track (type) and ends it. Everything between
tracks (setup, talking) belongs to no segment and is left out when cutting.
"""
from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("markers") as batch_op:
        batch_op.add_column(sa.Column("ended_at_ts", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("markers") as batch_op:
        batch_op.drop_column("ended_at_ts")
