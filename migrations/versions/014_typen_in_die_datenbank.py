"""Gottesdienstarten, Beitragsarten und Ansage-Regeln in die Datenbank

Vorher standen diese Listen im Code — die Beitragsarten sogar zweimal, in
api/service_types.py und audio-service/recorder.py, und die beiden wichen bereits
voneinander ab (der Audio-Dienst kannte "Unbenannt", die API nicht).

Mitgewandert ist, was die Erkennung ueber einen Typ weiss: Klangklasse, typische
Dauer, Ankerfunktion. Damit funktioniert die Automatik auch fuer einen selbst
angelegten Typ, statt ihn zu ignorieren.

Die Startwerte sind aus dem bisherigen Code uebernommen, das Verhalten bleibt also
zunaechst unveraendert.

Revision ID: 014
Revises: 013
"""
from alembic import op
import sqlalchemy as sa

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


MARKER_TYPES = [
    {"name": 'Chorlied', "sort_order": 0, "active": True, "exportieren": True, "klang_klasse": 'musik', "anker": False, "auto_marker": True, "dauer_min": 120, "dauer_max": 340, "geschuetzt": True},
    {"name": 'Orchester', "sort_order": 10, "active": True, "exportieren": True, "klang_klasse": 'musik', "anker": True, "auto_marker": True, "dauer_min": 120, "dauer_max": 200, "geschuetzt": True},
    {"name": 'Jugendchor', "sort_order": 20, "active": True, "exportieren": True, "klang_klasse": 'musik', "anker": False, "auto_marker": True, "dauer_min": None, "dauer_max": None, "geschuetzt": True},
    {"name": 'Kinderchor', "sort_order": 30, "active": True, "exportieren": True, "klang_klasse": 'musik', "anker": False, "auto_marker": True, "dauer_min": None, "dauer_max": None, "geschuetzt": False},
    {"name": 'Gruppenlied', "sort_order": 40, "active": True, "exportieren": True, "klang_klasse": 'musik', "anker": False, "auto_marker": True, "dauer_min": 150, "dauer_max": 260, "geschuetzt": False},
    {"name": 'Gedicht', "sort_order": 50, "active": True, "exportieren": True, "klang_klasse": 'sprache', "anker": False, "auto_marker": True, "dauer_min": 60, "dauer_max": 160, "geschuetzt": True},
    {"name": 'Anspiel', "sort_order": 60, "active": True, "exportieren": True, "klang_klasse": 'sprache', "anker": False, "auto_marker": True, "dauer_min": None, "dauer_max": None, "geschuetzt": False},
    {"name": 'Predigt', "sort_order": 70, "active": True, "exportieren": True, "klang_klasse": 'sprache', "anker": True, "auto_marker": True, "dauer_min": 260, "dauer_max": 2000, "geschuetzt": True},
    {"name": 'Kindergeschichte', "sort_order": 80, "active": True, "exportieren": True, "klang_klasse": 'sprache', "anker": False, "auto_marker": True, "dauer_min": 300, "dauer_max": 900, "geschuetzt": True},
    {"name": 'Prosa', "sort_order": 90, "active": True, "exportieren": True, "klang_klasse": 'sprache', "anker": False, "auto_marker": True, "dauer_min": 300, "dauer_max": 900, "geschuetzt": False},
    {"name": 'Gebetslied', "sort_order": 100, "active": True, "exportieren": True, "klang_klasse": None, "anker": False, "auto_marker": True, "dauer_min": None, "dauer_max": None, "geschuetzt": False},
    {"name": 'Musikstück', "sort_order": 110, "active": True, "exportieren": True, "klang_klasse": None, "anker": False, "auto_marker": True, "dauer_min": None, "dauer_max": None, "geschuetzt": False},
    {"name": 'Gemeindegesang', "sort_order": 120, "active": True, "exportieren": False, "klang_klasse": 'musik', "anker": False, "auto_marker": False, "dauer_min": 120, "dauer_max": 340, "geschuetzt": True},
    {"name": 'Kinderlied', "sort_order": 130, "active": True, "exportieren": False, "klang_klasse": None, "anker": False, "auto_marker": False, "dauer_min": None, "dauer_max": None, "geschuetzt": False},
    {"name": 'Gebet', "sort_order": 140, "active": True, "exportieren": False, "klang_klasse": 'sprache', "anker": False, "auto_marker": True, "dauer_min": 45, "dauer_max": 400, "geschuetzt": False},
    {"name": 'Ansage', "sort_order": 150, "active": True, "exportieren": False, "klang_klasse": 'sprache', "anker": False, "auto_marker": True, "dauer_min": 30, "dauer_max": 200, "geschuetzt": False},
    {"name": 'Vermeldungen', "sort_order": 160, "active": True, "exportieren": False, "klang_klasse": 'sprache', "anker": False, "auto_marker": True, "dauer_min": None, "dauer_max": None, "geschuetzt": False},
    {"name": 'Unbenannt', "sort_order": 999, "active": False, "exportieren": False, "klang_klasse": None, "anker": False, "auto_marker": True, "dauer_min": None, "dauer_max": None, "geschuetzt": True},
]

SERVICE_TYPES = [
    {"name": 'Bibelstunde', "sort_order": 0, "is_primary": True, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Gebetsstunde', "sort_order": 10, "is_primary": True, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Sonntagmorgen', "sort_order": 20, "is_primary": True, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Sonntagabend', "sort_order": 30, "is_primary": True, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Hochzeit', "sort_order": 40, "is_primary": False, "active": True, "geschuetzt": True, "nas_vorlage": None},
    {"name": 'Heiligabend', "sort_order": 50, "is_primary": False, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Weihnachten', "sort_order": 60, "is_primary": False, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Silvester', "sort_order": 70, "is_primary": False, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Neujahr', "sort_order": 80, "is_primary": False, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Karfreitag', "sort_order": 90, "is_primary": False, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Ostern', "sort_order": 100, "is_primary": False, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Himmelfahrt', "sort_order": 110, "is_primary": False, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Pfingsten', "sort_order": 120, "is_primary": False, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Erntedankfest', "sort_order": 130, "is_primary": False, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Besuch', "sort_order": 140, "is_primary": False, "active": True, "geschuetzt": False, "nas_vorlage": None},
    {"name": 'Sonstiges', "sort_order": 150, "is_primary": False, "active": True, "geschuetzt": True, "nas_vorlage": None},
]

ANSAGE_REGELN = [
    {"muster": '\\b(unser |der )?chor\\s+singt\\b', "marker_type": 'Chorlied', "sort_order": 0},
    {"muster": '\\bchorlied\\b', "marker_type": 'Chorlied', "sort_order": 10},
    {"muster": '\\bder (jugendchor|kinderchor)\\b', "marker_type": 'Jugendchor', "sort_order": 20},
    {"muster": '\\bwir singen (gemeinsam|zusammen|alle)\\b', "marker_type": 'Gemeindegesang', "sort_order": 30},
    {"muster": '\\b(gemeinsam|zusammen) singen wir\\b', "marker_type": 'Gemeindegesang', "sort_order": 40},
    {"muster": '\\bdas orchester\\b', "marker_type": 'Orchester', "sort_order": 50},
    {"muster": '\\bkindergeschichte\\b', "marker_type": 'Kindergeschichte', "sort_order": 60},
    {"muster": '\\b(ein |das )?gedicht\\b.{0,30}\\b(vortragen|hören|folgt)\\b', "marker_type": 'Gedicht', "sort_order": 70},
    {"muster": '\\bwir hören (nun |jetzt )?(ein|das) wort\\b', "marker_type": 'Predigt', "sort_order": 80},
]


def upgrade() -> None:
    service_types = op.create_table(
        "service_types",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("geschuetzt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("nas_vorlage", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    marker_types = op.create_table(
        "marker_types",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("exportieren", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("klang_klasse", sa.String(), nullable=True),
        sa.Column("anker", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_marker", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("dauer_min", sa.Integer(), nullable=True),
        sa.Column("dauer_max", sa.Integer(), nullable=True),
        sa.Column("geschuetzt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    ansage_regeln = op.create_table(
        "ansage_regeln",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("muster", sa.String(), nullable=False),
        sa.Column("marker_type", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.bulk_insert(service_types, SERVICE_TYPES)
    op.bulk_insert(marker_types, MARKER_TYPES)
    op.bulk_insert(ansage_regeln, ANSAGE_REGELN)


def downgrade() -> None:
    op.drop_table("ansage_regeln")
    op.drop_table("marker_types")
    op.drop_table("service_types")
