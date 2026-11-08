"""Lücken innerhalb eines Tracks: die Aufnahme läuft weiter, der Track lässt sie aus.

Bisher gab es nur „Pause“, und die hält die ganze Aufnahme an — wer zu spät fortsetzt,
verliert Ton. Am 08.11. (Kindersegnung) sollte nur ein Lied mitten in einem Beitrag
herausfallen.

Die Tabelle legt auch die API beim Start an (create_all) — deshalb nur anlegen,
wenn sie noch fehlt.

Revision ID: 019
Revises: 018
"""
import sqlalchemy as sa
from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "marker_luecken" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "marker_luecken",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("marker_id", sa.Integer(), sa.ForeignKey("markers.id"), nullable=False, index=True),
        sa.Column("von_s", sa.Float(), nullable=False),
        sa.Column("bis_s", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("marker_luecken")
