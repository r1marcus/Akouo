"""Chorbildschirm: gespeicherte Programme der Dirigenten und der Verlauf der Anzeige.

Ersetzt die Liedanzeige auf dem Mini-Rechner im Chor (Programme-Ordner und eine
Textdatei je Modus und Jahr).

Die Tabellen legt auch die API beim Start an (create_all) — deshalb nur anlegen,
wenn sie noch fehlen.

Revision ID: 020
Revises: 019
"""
import sqlalchemy as sa
from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    vorhanden = sa.inspect(op.get_bind()).get_table_names()
    if "chor_programme" not in vorhanden:
        op.create_table(
            "chor_programme",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("punkte", sa.String(), nullable=False, server_default="[]"),
            sa.Column("klammern", sa.String(), nullable=False, server_default="( )"),
            sa.Column("am", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
    if "anzeige_verlauf" not in vorhanden:
        op.create_table(
            "anzeige_verlauf",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("modus", sa.String(), nullable=False),
            sa.Column("text", sa.String(), nullable=False),
            sa.Column("am", sa.DateTime(), server_default=sa.func.now(), nullable=False, index=True),
        )


def downgrade() -> None:
    op.drop_table("anzeige_verlauf")
    op.drop_table("chor_programme")
