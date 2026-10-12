"""Add segment_exports table (Phase 7 — EXP-01/EXP-02)

Revision ID: 007
Revises: 006
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "segment_exports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("marker_id", sa.Integer(), nullable=False),
        sa.Column("marker_type", sa.String(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("mp3_path", sa.String(), nullable=False),
        sa.Column("mp3_filename", sa.String(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("nas_path", sa.String(), nullable=True),
        sa.Column("nas_status", sa.String(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("nas_error", sa.String(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["recording_sessions.id"]),
        sa.ForeignKeyConstraint(["marker_id"], ["markers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("segment_exports")
