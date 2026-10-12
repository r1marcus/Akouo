"""Add service_type_templates + couple_name/wedding_part to recording_sessions (Phase 6)

Revision ID: 006
Revises: 005
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa
import json

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 1: Create service_type_templates table
    op.create_table(
        "service_type_templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("service_type", sa.String(), nullable=False),
        sa.Column("track_sequence", sa.String(), nullable=True),  # JSON-encoded list
        sa.Column("checklist_template_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(),
                  server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["checklist_template_id"], ["checklist_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_type"),
    )

    # Step 2: Add couple_name and wedding_part to recording_sessions
    with op.batch_alter_table("recording_sessions") as batch_op:
        batch_op.add_column(sa.Column("couple_name", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("wedding_part", sa.Integer(), nullable=True))

    # Step 3: Seed 16 service type templates
    service_type_templates_table = sa.table(
        "service_type_templates",
        sa.column("service_type", sa.String),
        sa.column("track_sequence", sa.String),
        sa.column("checklist_template_id", sa.Integer),
    )
    seed_data = [
        {"service_type": "Bibelstunde",     "track_sequence": json.dumps(["Chorlied", "Predigt"]),                                                            "checklist_template_id": None},
        {"service_type": "Gebetsstunde",    "track_sequence": json.dumps(["Chorlied", "Predigt"]),                                                            "checklist_template_id": None},
        {"service_type": "Sonntagmorgen",   "track_sequence": json.dumps(["Chorlied", "Ansage", "Chorlied", "Predigt", "Chorlied", "Vermeldungen"]),          "checklist_template_id": None},
        {"service_type": "Sonntagabend",    "track_sequence": json.dumps(["Chorlied", "Ansage", "Predigt", "Chorlied"]),                                      "checklist_template_id": None},
        {"service_type": "Hochzeit",        "track_sequence": json.dumps(["Orchester", "Chorlied", "Anspiel", "Predigt", "Chorlied"]),                        "checklist_template_id": None},
        {"service_type": "Heiligabend",     "track_sequence": json.dumps(["Chorlied", "Ansage", "Predigt", "Chorlied"]),                                      "checklist_template_id": None},
        {"service_type": "Weihnachten",     "track_sequence": json.dumps(["Chorlied", "Ansage", "Predigt", "Chorlied"]),                                      "checklist_template_id": None},
        {"service_type": "Silvester",       "track_sequence": json.dumps(["Chorlied", "Ansage", "Predigt", "Chorlied"]),                                      "checklist_template_id": None},
        {"service_type": "Neujahr",         "track_sequence": json.dumps(["Chorlied", "Ansage", "Predigt", "Chorlied"]),                                      "checklist_template_id": None},
        {"service_type": "Karfreitag",      "track_sequence": json.dumps(["Chorlied", "Ansage", "Predigt", "Chorlied"]),                                      "checklist_template_id": None},
        {"service_type": "Ostern",          "track_sequence": json.dumps(["Chorlied", "Ansage", "Predigt", "Chorlied"]),                                      "checklist_template_id": None},
        {"service_type": "Himmelfahrt",     "track_sequence": json.dumps(["Chorlied", "Ansage", "Predigt", "Chorlied"]),                                      "checklist_template_id": None},
        {"service_type": "Pfingsten",       "track_sequence": json.dumps(["Chorlied", "Ansage", "Predigt", "Chorlied"]),                                      "checklist_template_id": None},
        {"service_type": "Erntedankfest",   "track_sequence": json.dumps(["Chorlied", "Ansage", "Predigt", "Chorlied"]),                                      "checklist_template_id": None},
        {"service_type": "Besuch",          "track_sequence": json.dumps([]),                                                                                 "checklist_template_id": None},
        {"service_type": "Sonstiges",       "track_sequence": json.dumps([]),                                                                                 "checklist_template_id": None},
    ]
    op.bulk_insert(service_type_templates_table, seed_data)


def downgrade() -> None:
    op.drop_table("service_type_templates")
    with op.batch_alter_table("recording_sessions") as batch_op:
        batch_op.drop_column("wedding_part")
        batch_op.drop_column("couple_name")
