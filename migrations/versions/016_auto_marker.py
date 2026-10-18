"""Merken, welche Marker die Automatik gesetzt hat.

Grundlage für „Neu erkennen" nach dem Gottesdienst: ersetzt wird nur, was die
Automatik gesetzt und seither niemand angefasst hat. Bisher stand das nur im
Speicher der API und war nach jedem Neustart weg.

Die Tabelle legt auch die API beim Start an (create_all) — deshalb nur anlegen,
wenn sie noch fehlt.

Revision ID: 016
Revises: 015
"""
import sqlalchemy as sa
from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "auto_markers" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "auto_markers",
        sa.Column("marker_id", sa.Integer(), sa.ForeignKey("markers.id"), primary_key=True),
        sa.Column("typ", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("auto_markers")
