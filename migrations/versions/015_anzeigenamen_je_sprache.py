"""Anzeigenamen je Sprache für Gottesdienst- und Beitragsarten.

Die Namen selbst bleiben deutsch, und das ist kein Versäumnis: An ihnen hängt Logik.
„Predigt" und „Orchester" sind Anker der Programmzuordnung, andere Namen stehen in den
Ansage-Regeln, „Hochzeit" und „Sonstiges" schalten eigene NAS-Layouts frei. Ein
übersetzter Name würde die Erkennung still aufhören lassen — und still ist das
Schlimmste, was eine Automatik tun kann.

Deshalb zwei zusätzliche Spalten je Tabelle. Sie tragen nur, was der Tontechniker liest.
Leer heißt: den deutschen Namen anzeigen. Ein selbst angelegter Typ beginnt leer und
zeigt dann überall seinen deutschen Namen — das ist richtig so, niemand soll raten
müssen, wie ein Typ auf Russisch heißt.

Revision ID: 015
Revises: 014
"""
import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


# Gottesdienstarten. Die Feste tragen ihre kirchlich gebräuchlichen Namen, nicht
# wörtliche Übersetzungen — „Erntedankfest" ist im Russischen das Erntedankfest der
# Gemeinde, nicht ein amerikanischer Feiertag.
GOTTESDIENSTARTEN = {
    "Bibelstunde":    ("Bible study", "Разбор Библии"),
    "Gebetsstunde":   ("Prayer meeting", "Молитвенное собрание"),
    "Sonntagmorgen":  ("Sunday morning", "Воскресное утро"),
    "Sonntagabend":   ("Sunday evening", "Воскресный вечер"),
    "Hochzeit":       ("Wedding", "Венчание"),
    "Heiligabend":    ("Christmas Eve", "Сочельник"),
    "Weihnachten":    ("Christmas", "Рождество"),
    "Silvester":      ("New Year's Eve", "Новогодний вечер"),
    "Neujahr":        ("New Year's Day", "Новый год"),
    "Karfreitag":     ("Good Friday", "Страстная пятница"),
    "Ostern":         ("Easter", "Пасха"),
    "Himmelfahrt":    ("Ascension", "Вознесение"),
    "Pfingsten":      ("Pentecost", "Пятидесятница"),
    "Erntedankfest":  ("Harvest festival", "Праздник урожая"),
    "Besuch":         ("Visiting speaker", "Гости"),
    "Sonstiges":      ("Other", "Прочее"),
}

# Beitragsarten — die Knöpfe, die während der Aufnahme am häufigsten gedrückt werden.
BEITRAGSARTEN = {
    "Chorlied":         ("Choir piece", "Пение хора"),
    "Orchester":        ("Orchestra", "Оркестр"),
    "Jugendchor":       ("Youth choir", "Молодёжный хор"),
    "Kinderchor":       ("Children's choir", "Детский хор"),
    "Gruppenlied":      ("Group song", "Пение группы"),
    "Gedicht":          ("Poem", "Стихотворение"),
    "Anspiel":          ("Sketch", "Сценка"),
    "Predigt":          ("Sermon", "Проповедь"),
    "Kindergeschichte": ("Children's story", "Рассказ для детей"),
    "Prosa":            ("Prose reading", "Чтение"),
    "Gebetslied":       ("Prayer song", "Молитвенная песня"),
    "Musikstück":       ("Instrumental piece", "Музыкальный номер"),
    "Gemeindegesang":   ("Congregational singing", "Общее пение"),
    "Kinderlied":       ("Children's song", "Детская песня"),
    "Gebet":            ("Prayer", "Молитва"),
    "Ansage":           ("Announcement", "Объявление"),
    "Vermeldungen":     ("Notices", "Объявления собрания"),
    "Unbenannt":        ("Unnamed", "Без названия"),
    "Sonstiges":        ("Other", "Прочее"),
}


def upgrade() -> None:
    for tabelle in ("service_types", "marker_types"):
        op.add_column(tabelle, sa.Column("anzeige_en", sa.String(), nullable=True))
        op.add_column(tabelle, sa.Column("anzeige_ru", sa.String(), nullable=True))

    verbindung = op.get_bind()
    for tabelle, namen in (("service_types", GOTTESDIENSTARTEN), ("marker_types", BEITRAGSARTEN)):
        for name, (en, ru) in namen.items():
            verbindung.execute(
                sa.text(
                    f"UPDATE {tabelle} SET anzeige_en = :en, anzeige_ru = :ru "  # noqa: S608
                    "WHERE name = :name"
                ),
                {"en": en, "ru": ru, "name": name},
            )


def downgrade() -> None:
    for tabelle in ("service_types", "marker_types"):
        op.drop_column(tabelle, "anzeige_ru")
        op.drop_column(tabelle, "anzeige_en")
