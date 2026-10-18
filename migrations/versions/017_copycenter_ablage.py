"""CopyCenter-Ordner je Aufnahme festhalten.

Der Ordner wird beim ersten Track entschieden (_a, _b, belegt → _c …) und muss für
alle weiteren Tracks derselbe bleiben.

Die Tabelle legt auch die API beim Start an (create_all) — deshalb nur anlegen,
wenn sie noch fehlt.

Revision ID: 017
Revises: 016
"""
import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "copycenter_ablage" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "copycenter_ablage",
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("recording_sessions.id"),
                  primary_key=True),
        sa.Column("ordner", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("copycenter_ablage")
