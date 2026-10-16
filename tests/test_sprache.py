"""Mehrsprachige Oberfläche.

Der wichtigste Test hier ist der auf Vollständigkeit: Eine vergessene Übersetzung fällt
sonst erst auf, wenn jemand die Oberfläche auf Russisch öffnet — und das ist im Zweifel
fünf Minuten vor dem Gottesdienst.
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
# Discard-Port: die Lifespan schiebt die Erkennungskonfiguration zum Audio-Dienst.
os.environ.setdefault("AUDIO_SERVICE_URL", "http://127.0.0.1:9")
sys.path.insert(0, "api")

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import sprache  # noqa: E402

# main, models und database werden bewusst ERST IM TEST importiert, nicht hier.
# test_archive.py loescht diese Module aus sys.modules und importiert sie neu, um eine
# frische Datenbank zu bekommen. Ein Import auf Modulebene passiert schon beim Einsammeln
# der Tests und vereitelt das: die Router blieben im Cache und zeigten weiter auf die
# alte Engine — danach fand test_archive seine Tabellen nicht mehr.


def _client() -> TestClient:
    """TestClient bewusst OHNE "with": das wuerde die Lifespan starten, und wenn sie
    endet, schliesst die Verbindung zur :memory:-Datenbank — danach finden die uebrigen
    Tests im selben Prozess ihre Tabellen nicht mehr. Fuer Umschalter und Seitenaufbau
    braucht es die Lifespan nicht."""
    from main import app
    return TestClient(app)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def db_client():
    """Eigene Engine je Test, wie in test_konfiguration_api.py — nie die modulweite."""
    from database import get_db
    from main import app
    from models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        async def override_get_db():
            yield session
        app.dependency_overrides[get_db] = override_get_db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
        app.dependency_overrides.clear()
    await engine.dispose()

ORDNER = Path(__file__).resolve().parent.parent / "api" / "sprachen"


def _katalog(code: str) -> dict:
    with (ORDNER / f"{code}.json").open(encoding="utf-8") as f:
        return json.load(f)


def test_jede_sprache_hat_eine_datei():
    for code in sprache.SPRACHEN:
        assert (ORDNER / f"{code}.json").exists(), f"api/sprachen/{code}.json fehlt"


@pytest.mark.parametrize("code", [c for c in sprache.SPRACHEN if c != sprache.AUSGANGSSPRACHE])
def test_keine_fehlende_uebersetzung(code):
    """Jeder deutsche Satz hat eine Entsprechung — und keine ist leer."""
    fehlend = sprache.fehlende(code)
    assert not fehlend, (
        f"{len(fehlend)} Texte ohne {code}-Übersetzung, z.B.: {fehlend[:5]}"
    )


@pytest.mark.parametrize("code", [c for c in sprache.SPRACHEN if c != sprache.AUSGANGSSPRACHE])
def test_keine_verwaisten_eintraege(code):
    """Nichts übersetzen, was es im Deutschen nicht mehr gibt — sonst wächst der
    Katalog mit Sätzen zu, die niemand mehr anzeigt."""
    verwaist = sorted(set(_katalog(code)) - set(_katalog(sprache.AUSGANGSSPRACHE)))
    assert not verwaist, f"{len(verwaist)} verwaiste {code}-Einträge: {verwaist[:5]}"


def test_platzhalter_bleiben_erhalten():
    """Texte, die im Code verkettet werden, dürfen ihre Form nicht verlieren."""
    de = _katalog("de")
    for code in ("en", "ru"):
        ziel = _katalog(code)
        for schluessel, deutsch in de.items():
            if deutsch.endswith(":"):
                assert ziel[schluessel].endswith(":"), (
                    f"{code}: „{schluessel}“ endet nicht auf Doppelpunkt — "
                    "die Meldung dahinter klebt sonst am Text"
                )
            if deutsch.endswith("("):
                assert ziel[schluessel].endswith("("), f"{code}: „{schluessel}“ verliert die Klammer"


def test_unbekanntes_faellt_auf_deutsch_zurueck():
    assert sprache.uebersetze("Gibt es nicht im Katalog", "ru") == "Gibt es nicht im Katalog"
    assert sprache.gueltig("fr") == "de"
    assert sprache.gueltig(None) == "de"
    assert sprache.gueltig("ru-RU") == "ru"


def test_deutsch_ist_die_identitaet():
    """Auf Deutsch wird nichts nachgeschlagen — der Quelltext ist die Anzeige."""
    assert sprache.uebersetze("Aufnahme starten", "de") == "Aufnahme starten"
    assert sprache.katalog("de") == {}


def test_umschalter_setzt_cookie_und_kehrt_zurueck():
    antwort = _client().get("/sprache/ru?weiter=/archiv", follow_redirects=False)
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/archiv"
    assert antwort.cookies["akouo_sprache"] == "ru"


def test_umschalter_leitet_nicht_nach_draussen():
    """Ohne diese Prüfung wäre der Umschalter eine offene Weiterleitung."""
    client = _client()
    for ziel in ("https://example.invalid/", "//example.invalid/"):
        antwort = client.get(f"/sprache/en?weiter={ziel}", follow_redirects=False)
        assert antwort.headers["location"] == "/"


def test_unbekannte_sprache_wird_deutsch():
    antwort = _client().get("/sprache/fr", follow_redirects=False)
    assert antwort.cookies["akouo_sprache"] == "de"


def test_seite_kommt_in_der_gewaehlten_sprache():
    client = _client()
    for code, erwartet in (("de", 'lang="de"'), ("en", 'lang="en"'), ("ru", 'lang="ru"')):
        client.cookies.set("akouo_sprache", code)
        antwort = client.get("/")
        assert antwort.status_code == 200
        assert erwartet in antwort.text



@pytest.mark.anyio
async def test_einstellungen_kommen_uebersetzt(db_client):
    """Die Beschriftungen der Einstellungen stehen in Python, nicht im Template —
    sie gingen beim ersten Anlauf unübersetzt hinaus."""
    db_client.cookies.set("akouo_sprache", "ru")
    daten = (await db_client.get("/api/konfiguration")).json()
    namen = [f["name"] for f in daten["felder"]]
    assert any("Битрейт" in n for n in namen), namen[:5]


@pytest.mark.anyio
async def test_anzeigenamen_kommen_mit(db_client):
    """Die Arten tragen ihren deutschen Namen plus Anzeigenamen je Sprache."""
    from models import MarkerType, ServiceType
    daten = (await db_client.get("/api/konfiguration")).json()
    for schluessel in ("gottesdienstarten", "beitragsarten"):
        for eintrag in daten[schluessel]:
            assert "anzeige_en" in eintrag and "anzeige_ru" in eintrag
    assert ServiceType and MarkerType


def test_anzeigename_faellt_auf_den_deutschen_namen_zurueck():
    """Ein selbst angelegter Typ hat keine Übersetzung — dann steht dort der deutsche
    Name. Das ist richtig so: niemand soll raten müssen, wie ein Typ auf Russisch heißt."""
    import typen
    assert typen.anzeige_beitragsart("Posaunenchor", "ru") == "Posaunenchor"
    assert typen.anzeige_gottesdienstart("Jugendstunde", "en") == "Jugendstunde"


def test_der_name_bleibt_der_schluessel(monkeypatch):
    """Anzeigenamen dürfen nie dort landen, wo der Name gebraucht wird — an ihm hängen
    Anker, Ansage-Regeln und NAS-Vorlagen."""
    import typen
    t = typen.Typen()
    t.anzeige_beitrag = {"ru": {"Predigt": "Проповедь"}}
    # monkeypatch statt einer blanken Zuweisung: typen.aktuell ist modulweit, und ein
    # Test, der ihn stehen lässt, vergiftet jeden nachfolgenden im selben Prozess.
    monkeypatch.setattr(typen, "aktuell", t)
    assert typen.anzeige_beitragsart("Predigt", "ru") == "Проповедь"
    # … der Name selbst bleibt unangetastet
    assert "Predigt" in t.beitragsarten_gueltig


def test_jeder_text_aus_den_templates_steht_im_katalog():
    """Ein t("…") mit einem Satz, den der Katalog nicht kennt, fällt sonst nirgends auf:
    die Oberfläche zeigt klaglos Deutsch, und die Vollständigkeitstests vergleichen nur
    die Kataloge untereinander. Genau so blieb „Unbekannter Fehler" unübersetzt."""
    import re

    ordner = Path(__file__).resolve().parent.parent / "api" / "templates"
    de = set(_katalog("de"))
    fehlend = set()
    for datei in sorted(ordner.rglob("*.html")):
        quelle = datei.read_text()
        for m in re.finditer(r'(?<![\w.$])t\(\s*"((?:[^"\\]|\\.)*)"\s*\)', quelle):
            wert = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
            if wert and wert not in de:
                fehlend.add(f"{datei.name}: {wert}")
        # tr('…') und trf('…', {…}) — beide holen ihren Text aus demselben Katalog
        for muster in (r"(?<![\w.$])tr\('((?:[^'\\]|\\.)*)'\)",
                       r"(?<![\w.$])trf\('((?:[^'\\]|\\.)*)'"):
            for m in re.finditer(muster, quelle):
                wert = m.group(1).replace("\\'", "'").replace("\\\\", "\\")
                if wert and wert not in de:
                    fehlend.add(f"{datei.name}: {wert}")
    assert not fehlend, f"{len(fehlend)} Texte ohne Katalogeintrag: {sorted(fehlend)[:8]}"


def test_jede_meldung_aus_python_steht_im_katalog():
    """Dasselbe für die Meldungen, die über `uebersetze(…)` in einen Toast wandern.

    Sie fallen noch leichter durch als die Templates: Ein fehlender Eintrag sieht auf
    Deutsch völlig richtig aus — es ist ja der deutsche Satz."""
    import re

    api = Path(__file__).resolve().parent.parent / "api"
    de = set(_katalog("de"))
    fehlend = {
        f"{datei.relative_to(api)}: {m.group(1)}"
        for datei in sorted(api.rglob("*.py"))
        for m in re.finditer(r'uebersetze\(\s*"((?:[^"\\]|\\.)*)"', datei.read_text())
        if m.group(1) and m.group(1).replace('\\"', '"') not in de
    }
    assert not fehlend, f"{len(fehlend)} Meldungen ohne Katalogeintrag: {sorted(fehlend)[:8]}"


def test_platzhalter_ueberleben_die_uebersetzung():
    """`{feld}` in einem Satz muss in jeder Sprache vorkommen — sonst wirft `.format()`
    nicht, sondern lässt den Wert einfach weg, und der Nutzer liest einen Satz mit
    einem Loch darin."""
    import re

    de = _katalog("de")
    for code in ("en", "ru"):
        ziel = _katalog(code)
        for schluessel, deutsch in de.items():
            namen = set(re.findall(r"\{(\w+)\}", deutsch))
            if namen:
                assert set(re.findall(r"\{(\w+)\}", ziel[schluessel])) == namen, (
                    f"{code}: „{schluessel}“ verliert oder erfindet einen Platzhalter"
                )
