"""Add recording_sessions.nas_folder — remembers the chosen NAS folder

Revision ID: 012
Revises: 011
"""
import sqlalchemy as sa
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable without default: sessions from before the NAS upload simply have no folder,
    # and the next upload decides one for them.
    with op.batch_alter_table("recording_sessions") as batch:
        batch.add_column(sa.Column("nas_folder", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recording_sessions") as batch:
        batch.drop_column("nas_folder")
