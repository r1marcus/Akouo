"""Titel und Sprecher von Hand: eine spätere KI-Auswertung überschreibt sie nicht.

Bis 08.11. ließ sich ein Titel nur ändern, wenn die KI-Auswertung schon gelaufen war —
und die nächste Auswertung hätte ihn wieder ersetzt.

Revision ID: 018
Revises: 017
"""
import sqlalchemy as sa
from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    spalten = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("segment_insights")}
    with op.batch_alter_table("segment_insights") as t:
        if "titel_von_hand" not in spalten:
            t.add_column(sa.Column("titel_von_hand", sa.Boolean(), nullable=False, server_default=sa.false()))
        if "sprecher_von_hand" not in spalten:
            t.add_column(sa.Column("sprecher_von_hand", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("segment_insights") as t:
        t.drop_column("sprecher_von_hand")
        t.drop_column("titel_von_hand")
