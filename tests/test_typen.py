"""Gottesdienstarten und Beitragsarten kommen aus der Datenbank, nicht aus dem Code.

Die Naht, die hier geprüft wird: vorher stand die Liste der Beitragsarten in
api/service_types.py UND in audio-service/recorder.py, und die beiden wichen schon
voneinander ab (der Audio-Dienst kannte "Unbenannt", die API nicht). Jetzt besitzt die
API die Liste und schiebt dem Audio-Dienst, was er für die Erkennung braucht.
"""
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
# Discard-Port: die Lifespan schiebt die Erkennungskonfiguration zum Audio-Dienst.
# Ohne das würde ein Testlauf einen produktiv laufenden Dienst umkonfigurieren.
os.environ.setdefault("AUDIO_SERVICE_URL", "http://127.0.0.1:9")
sys.path.insert(0, "api")

import pytest
from httpx import ASGITransport, AsyncClient

import typen
from database import AsyncSessionLocal, engine
from models import AnsageRegel, Base, MarkerType, ServiceType


@pytest.fixture
async def db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def test_ohne_tabelleninhalt_gelten_die_rueckfallwerte(db):
    """api/main.py ruft beim Start create_all auf. Auf einer so entstandenen Datenbank
    sind die Tabellen leer, ohne dass die Migration gelaufen wäre — dann muss das
    Dashboard trotzdem bedienbar sein."""
    t = await typen.neu_laden(db)
    assert t.geladen is False
    assert "Predigt" in t.beitragsarten
    assert "Sonntagmorgen" in t.gottesdienstarten


async def test_listen_kommen_aus_der_datenbank(db):
    db.add_all([
        ServiceType(name="Jugendstunde", sort_order=10, is_primary=True),
        ServiceType(name="Altes", sort_order=20, active=False),
        MarkerType(name="Posaunenchor", sort_order=10, klang_klasse="musik",
                   dauer_min=90, dauer_max=240),
        MarkerType(name="Fürbitte", sort_order=20, klang_klasse="sprache",
                   exportieren=False),
    ])
    await db.commit()

    t = await typen.neu_laden(db)
    assert t.geladen is True
    assert t.gottesdienstarten == ["Jugendstunde"]          # "Altes" ist ausgeblendet
    assert t.haupt_gottesdienstarten == ["Jugendstunde"]
    assert t.beitragsarten == ["Posaunenchor", "Fürbitte"]  # nach sort_order
    assert t.nicht_exportieren == {"Fürbitte"}
    assert typen.exportieren("Posaunenchor") is True
    assert typen.exportieren("Fürbitte") is False


async def test_ausgeblendeter_typ_bleibt_gueltig(db):
    """Alte Aufnahmen verweisen per Name auf ihren Typ. Ein ausgeblendeter Typ darf
    deshalb nicht ungültig werden — er verschwindet nur aus den Knöpfen."""
    db.add_all([
        MarkerType(name="Predigt", sort_order=10, klang_klasse="sprache"),
        MarkerType(name="Unbenannt", sort_order=999, active=False),
    ])
    await db.commit()

    t = await typen.neu_laden(db)
    assert "Unbenannt" not in t.beitragsarten           # kein Knopf
    assert "Unbenannt" in t.beitragsarten_gueltig       # aber gültiger Wert


async def test_erkennung_nutzlast_enthaelt_was_der_audio_dienst_braucht(db):
    db.add_all([
        MarkerType(name="Predigt", sort_order=10, klang_klasse="sprache",
                   anker=True, dauer_min=260, dauer_max=2000),
        MarkerType(name="Chorlied", sort_order=20, klang_klasse="musik",
                   dauer_min=120, dauer_max=340),
        MarkerType(name="Gemeindegesang", sort_order=30, klang_klasse="musik",
                   auto_marker=False, exportieren=False),
        MarkerType(name="Musikstück", sort_order=40),     # nur auf Ansage
        AnsageRegel(muster=r"\bchorlied\b", marker_type="Chorlied", sort_order=10),
    ])
    await db.commit()
    t = await typen.neu_laden(db)

    n = typen.erkennung_nutzlast(t)
    assert n["anker_typen"] == ["Predigt"]
    assert set(n["musik_typen"]) == {"Chorlied", "Gemeindegesang"}
    assert n["sprach_typen"] == ["Predigt"]
    assert n["kein_auto_marker"] == ["Gemeindegesang"]
    assert n["typische_dauer"]["Predigt"] == [260, 2000]
    assert n["ansage_regeln"] == [{"muster": r"\bchorlied\b", "typ": "Chorlied"}]
    # Ein Typ ohne Klangklasse wird nie geraten, soll aber einer Klasse zuordenbar sein
    assert n["nur_auf_ansage"] == {"Musikstück": "Musik"}


# Die Gegenseite des Vertrags — was der Audio-Dienst mit dieser Nutzlast macht — steht in
# tests/test_erkennung_uebernahme.py. Getrennt, weil classifier numpy braucht und diese
# Datei SQLAlchemy: die beiden Dienste haben eigene Umgebungen, genau wie im Betrieb.
