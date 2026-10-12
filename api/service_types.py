"""Gottesdiensttypen and the checklist that belongs to each of them."""
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import ChecklistInstance, ServiceTypeTemplate

SERVICE_TYPES = [
    "Bibelstunde", "Gebetsstunde", "Sonntagmorgen", "Sonntagabend",
    "Hochzeit", "Heiligabend", "Weihnachten", "Silvester", "Neujahr",
    "Karfreitag", "Ostern", "Himmelfahrt", "Pfingsten",
    "Erntedankfest", "Besuch", "Sonstiges",
]

# Regular services — shown as large tiles in the header picker; all others sit behind "Weitere"
PRIMARY_SERVICE_TYPES = ["Sonntagmorgen", "Sonntagabend", "Bibelstunde", "Gebetsstunde"]

# Track types an operator can mark during a service (mirrors audio-service/recorder.py)
MARKER_TYPES = [
    # Beiträge, die ins Archiv gehen
    "Chorlied", "Orchester", "Jugendchor", "Kinderchor", "Gruppenlied",
    "Gedicht", "Anspiel", "Predigt", "Kindergeschichte", "Prosa",
    # Nur wenn ausdrücklich angesagt — nie geraten. Musikstück ist Musik auf der
    # Bühne und etwas anderes als Orchester; akustisch sind beide nicht zu trennen.
    "Gebetslied", "Musikstück",
    # Gehört zum Gottesdienst und wird markiert, aber nicht exportiert
    "Gemeindegesang", "Kinderlied", "Gebet", "Ansage", "Vermeldungen",
]

# Wird markiert, damit der Ablauf vollständig dokumentiert ist, aber beim
# Sammel-Export übersprungen. Einzeln exportieren bleibt möglich.
NICHT_EXPORTIEREN = ("Gemeindegesang", "Kinderlied", "Gebet", "Ansage", "Vermeldungen")

# settings row holding the header selection ("in welchem Gottesdienst sind wir gerade")
CURRENT_SERVICE_KEY = "current_service"


async def ensure_checklist_for_service(db: AsyncSession, service_type: str) -> Optional[ChecklistInstance]:
    """Activate the checklist mapped to service_type (SVC-04).

    Skips when today's active checklist already uses that template — a new instance
    would wipe the items ticked off before the service.
    """
    result = await db.execute(
        select(ServiceTypeTemplate).where(ServiceTypeTemplate.service_type == service_type)
    )
    svc_tmpl = result.scalar_one_or_none()
    if svc_tmpl is None or svc_tmpl.checklist_template_id is None:
        return None

    today = date.today().isoformat()
    result = await db.execute(
        select(ChecklistInstance).order_by(ChecklistInstance.created_at.desc()).limit(1)
    )
    active = result.scalar_one_or_none()
    if active is not None and active.template_id == svc_tmpl.checklist_template_id and active.service_date == today:
        return active

    instance = ChecklistInstance(template_id=svc_tmpl.checklist_template_id, service_date=today)
    db.add(instance)
    await db.commit()
    return instance
