"""Map service types to their checklist templates (SVC-04)

Revision ID: 008
Revises: 007
Create Date: 2026-09-15

Matched by checklist template *name*, so the mapping is reproducible on any installation.
A missing template leaves the service type unmapped; existing mappings are not overwritten.
"""
from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

GOTTESDIENST = [
    "Sonntagmorgen", "Sonntagabend",
    "Heiligabend", "Weihnachten", "Silvester", "Neujahr", "Karfreitag",
    "Ostern", "Himmelfahrt", "Pfingsten", "Erntedankfest",
]

# service type -> checklist template name
MAPPING = {
    **{service_type: "Gottesdienst" for service_type in GOTTESDIENST},
    "Bibelstunde": "Bibelstunde",
    "Gebetsstunde": "Gebetstunde",  # template is spelled without the second "s"
}


def upgrade() -> None:
    conn = op.get_bind()
    for service_type, template_name in MAPPING.items():
        conn.execute(
            sa.text(
                "UPDATE service_type_templates SET checklist_template_id = "
                "(SELECT id FROM checklist_templates WHERE name = :template_name ORDER BY id LIMIT 1) "
                "WHERE service_type = :service_type AND checklist_template_id IS NULL"
            ),
            {"template_name": template_name, "service_type": service_type},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for service_type in MAPPING:
        conn.execute(
            sa.text("UPDATE service_type_templates SET checklist_template_id = NULL WHERE service_type = :service_type"),
            {"service_type": service_type},
        )
