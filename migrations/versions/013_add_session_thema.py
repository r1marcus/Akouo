"""Freies Thema je Gottesdienst

Bisher gab es nur den Typ (Sonntagmorgen, Bibelstunde …) und für Hochzeiten das
Brautpaar. Ein Erntedankfest, eine Taufe oder eine Jubiläumsfeier ließ sich nicht
benennen — im Archiv hießen alle Dateien gleich.

Revision ID: 013
Revises: 012
"""
import sqlalchemy as sa
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("recording_sessions") as batch:
        batch.add_column(sa.Column("thema", sa.String(), nullable=True))


def downgrade():
    with op.batch_alter_table("recording_sessions") as batch:
        batch.drop_column("thema")
