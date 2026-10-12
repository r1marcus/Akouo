"""Was der Audio-Dienst mit der von der API geschobenen Erkennungskonfiguration macht.

Gegenstück zu tests/test_typen.py, das die andere Seite prüft: dort wird aus der
Datenbank die Nutzlast gebaut, hier wird sie übernommen. Getrennte Dateien, weil die
beiden Dienste eigene Umgebungen haben — classifier braucht numpy, die API-Seite
SQLAlchemy. Dieselbe Trennung wie im Betrieb.

Die Nutzlast unten muss zu typen.erkennung_nutzlast() passen. Ändert sich dort ein
Feldname, schlägt hier nichts fehl — deshalb prüft test_typen.py die Feldnamen
ausdrücklich ab.
"""
import sys

sys.path.insert(0, "audio-service")

import pytest

import automode
import classifier


@pytest.fixture
def startwerte():
    """Den Modulzustand nach jedem Test zurücksetzen — classifier.anwenden() schreibt
    Modulglobale, und die Tests liefen sonst reihenfolgeabhängig."""
    vorher = classifier.aktueller_stand()
    anker_vorher = automode.ANKER_TYPEN
    yield vorher
    classifier.anwenden(vorher)
    automode.anker_setzen(anker_vorher)


NUTZLAST = {
    "beitragsarten": ["Predigt", "Posaunenchor", "Unbenannt"],
    "musik_typen": ["Posaunenchor"],
    "sprach_typen": ["Predigt"],
    "kein_auto_marker": ["Gemeindegesang"],
    "anker_typen": ["Predigt", "Posaunenchor"],
    "typische_dauer": {"Predigt": [260, 2000], "Posaunenchor": [90, 240]},
    "ansage_regeln": [{"muster": r"\bposaunenchor\b", "typ": "Posaunenchor"}],
    "nur_auf_ansage": {"Musikstück": "Musik"},
    "min_stille_s": 6.5,
    "min_beitrag_s": 30.0,
    "orchester_flachheit": 0.25,
    "stimmen_fuer_sprache": 2,
}


def test_nutzlast_wird_vollstaendig_uebernommen(startwerte):
    uebernommen = classifier.anwenden(NUTZLAST)
    automode.anker_setzen(NUTZLAST["anker_typen"])

    assert classifier.MUSIK_TYPEN == ("Posaunenchor",)
    assert classifier.SPRACH_TYPEN == ("Predigt",)
    assert classifier.TYPISCHE_DAUER["Posaunenchor"] == (90, 240)
    assert classifier.MIN_STILLE_S == 6.5
    assert classifier.MIN_BEITRAG_S == 30.0
    assert classifier.ORCHESTER_FLACHHEIT == 0.25
    assert classifier.STIMMEN_FUER_SPRACHE == 2
    assert automode.ANKER_TYPEN == ("Predigt", "Posaunenchor")
    assert uebernommen["ansage_regeln"] == 1


def test_ein_selbst_angelegter_typ_wird_der_klasse_zugeordnet(startwerte):
    """Der eigentliche Gewinn: ein neuer Typ aus der Oberfläche nimmt an der Erkennung
    teil, statt ignoriert zu werden.

    _klasse_von entscheidet, ob ein Typ zur gemessenen Klangklasse passt. Die Methode
    greift nur auf die classifier-Listen zu, deshalb genügt hier eine Instanz ohne
    Aufnahmegerät."""
    classifier.anwenden(NUTZLAST)
    modus = automode.AutoModus(None)
    assert modus._klasse_von("Posaunenchor") == "Musik"
    assert modus._klasse_von("Predigt") == "Sprache"
    assert modus._klasse_von("Musikstück") == "Musik"   # nur auf Ansage, aber zuordenbar


def test_fehlende_felder_lassen_die_startwerte_stehen(startwerte):
    classifier.anwenden({"min_stille_s": 9.0})
    assert classifier.MIN_STILLE_S == 9.0
    assert classifier.MIN_BEITRAG_S == startwerte["min_beitrag_s"]
    assert len(classifier.MUSIK_TYPEN) == len(startwerte["musik_typen"])


def test_leere_anker_liste_wird_ignoriert(startwerte):
    """Ohne Anker pflanzt sich ein einziger nie angesagter Punkt über den ganzen
    Gottesdienst fort. Eine leere Liste darf das nicht auslösen."""
    automode.anker_setzen(["Predigt"])
    automode.anker_setzen([])
    assert automode.ANKER_TYPEN == ("Predigt",)


def test_kaputtes_ansage_muster_legt_die_erkennung_nicht_lahm(startwerte):
    """Die Oberfläche prüft beim Speichern. Das hier ist die zweite Sicherung: ein
    unbrauchbarer regulärer Ausdruck darf die übrigen Regeln nicht mitnehmen."""
    uebernommen = classifier.anwenden({"ansage_regeln": [
        {"muster": r"\bchorlied\b", "typ": "Chorlied"},
        {"muster": "[kaputt(", "typ": "Unsinn"},
    ]})
    assert uebernommen["ansage_regeln"] == 1
    assert all(typ != "Unsinn" for _, typ in classifier._ANKUENDIGUNG)
