"""Add segment_insights — AI metadata per segment (AI-02)

Revision ID: 010
Revises: 009
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "segment_insights",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("marker_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("bible_refs", sa.String(), nullable=True),
        sa.Column("keywords", sa.String(), nullable=True),
        sa.Column("speaker", sa.String(), nullable=True),
        sa.Column("transcript", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["recording_sessions.id"]),
        sa.ForeignKeyConstraint(["marker_id"], ["markers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("marker_id"),
    )


def downgrade() -> None:
    op.drop_table("segment_insights")
