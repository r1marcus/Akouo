"""Ordnervorlagen für die NAS-Ablage.

Das ist der riskanteste Teil der Einstellbarkeit: eine unbrauchbare Vorlage verstreut
Aufnahmen über ein gewachsenes Archiv, und bestehende Ordner werden nicht mehr
wiedergefunden. Deshalb wird hier geprüft, was alles NICHT passieren darf.

Dass die Standardvorlagen die gewachsene Ablage genau treffen, prüft
tests/test_segments_api.py.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, "api")

import pytest

import nas_pfad


class _Sess:
    def __init__(self, service_type="Sonntagmorgen", couple_name=None,
                 wedding_part=None, thema=None):
        self.service_type = service_type
        self.couple_name = couple_name
        self.wedding_part = wedding_part
        self.thema = thema
        self.started_at = datetime(2026, 10, 4, 9, 55)


# ─── Platzhalter und Werte ──────────────────────────────────────────────────

def test_werte_enthalten_alle_platzhalter():
    """Sonst verspricht die Hilfe in der Oberfläche etwas, das nicht eingesetzt wird."""
    werte = nas_pfad.werte_fuer(_Sess(), datetime(2026, 10, 4, 9, 55))
    assert set(werte) == set(nas_pfad.PLATZHALTER)


def test_tageszeit_haengt_an_der_uhrzeit_nicht_an_der_art():
    morgen = nas_pfad.werte_fuer(_Sess(), datetime(2026, 10, 4, 11, 59))
    abend = nas_pfad.werte_fuer(_Sess(), datetime(2026, 10, 4, 12, 0))
    assert morgen["tageszeit"] == "1. morgens"
    assert abend["tageszeit"] == "2. abends"


# ─── Optionale Gruppen ──────────────────────────────────────────────────────

def test_klammergruppe_faellt_bei_fehlendem_wert_ganz_weg():
    werte = {"datum": "2026-06-06", "brautpaar": "", "teil": ""}
    assert nas_pfad.rendern("Hochzeiten/{datum}[ {brautpaar}][/{teil}. Teil]", werte) == \
        "Hochzeiten/2026-06-06"


def test_klammergruppe_bleibt_bei_vorhandenem_wert():
    werte = {"datum": "2026-06-06", "brautpaar": "Robin & Laura", "teil": "2"}
    assert nas_pfad.rendern("Hochzeiten/{datum}[ {brautpaar}][/{teil}. Teil]", werte) == \
        "Hochzeiten/2026-06-06 Robin & Laura/2. Teil"


def test_gruppe_mit_mehreren_platzhaltern_braucht_alle():
    """Eine Gruppe fällt weg, sobald EINER ihrer Werte fehlt — nicht erst, wenn alle
    fehlen. Sonst entstünde „2026-06-06 Robin & Laura/. Teil“."""
    vorlage = "Ordner/[{brautpaar} {teil}]Rest"
    assert nas_pfad.rendern(vorlage, {"brautpaar": "Robin", "teil": "2"}) == \
        "Ordner/Robin 2Rest"
    assert nas_pfad.rendern(vorlage, {"brautpaar": "Robin", "teil": ""}) == "Ordner/Rest"
    assert nas_pfad.rendern(vorlage, {"brautpaar": "", "teil": "2"}) == "Ordner/Rest"


# ─── Sicherungen: darf nicht aus der Freigabe herausführen ──────────────────

def test_absolute_vorlage_wird_abgelehnt():
    with pytest.raises(nas_pfad.VorlagenFehler, match="nicht mit /"):
        nas_pfad.pruefen("/Volumes/Aufnahmen/{datum}")


def test_punkt_punkt_wird_abgelehnt():
    with pytest.raises(nas_pfad.VorlagenFehler, match="„\\.\\.“"):
        nas_pfad.pruefen("../../etc/{datum}")


def test_unbekannter_platzhalter_wird_abgelehnt():
    with pytest.raises(nas_pfad.VorlagenFehler, match="Unbekannte Platzhalter"):
        nas_pfad.pruefen("Ordner/{gibtsnicht}")


def test_unpaarige_klammer_wird_abgelehnt():
    with pytest.raises(nas_pfad.VorlagenFehler, match="schließende"):
        nas_pfad.pruefen("Ordner/[{datum}")


def test_leere_vorlage_wird_abgelehnt():
    with pytest.raises(nas_pfad.VorlagenFehler, match="nicht leer"):
        nas_pfad.pruefen("   ")


def test_vorlage_die_zu_leerem_pfad_wird():
    """Eine Vorlage, die nur aus optionalen Gruppen besteht, kann leer ausgehen."""
    with pytest.raises(nas_pfad.VorlagenFehler, match="leeren Pfad"):
        nas_pfad.rendern("[{brautpaar}]", {"brautpaar": ""})


def test_leere_segmente_verschwinden():
    """Doppelte Schrägstriche würden auf SMB einen Ordner ohne Namen erzeugen."""
    assert nas_pfad.rendern("a//{datum}///b", {"datum": "2026-01-01"}) == "a/2026-01-01/b"


# ─── Säubern der Segmente ───────────────────────────────────────────────────

def test_doppelpunkt_wird_ersetzt():
    """Der Doppelpunkt ist der wichtigste Fall: im Finder wird er zum Schrägstrich."""
    assert nas_pfad.rendern("{thema}", {"thema": "Thema: Dankbarkeit"}) == "Thema- Dankbarkeit"


def test_weitere_unerlaubte_zeichen():
    assert nas_pfad.rendern("{thema}", {"thema": 'a\\b<c>d"e|f?g*h'}) == "a-b-c-d-e-f-g-h"


def test_mehrfache_leerzeichen_werden_zusammengezogen():
    assert nas_pfad.rendern("{thema}", {"thema": "viel    Luft"}) == "viel Luft"


def test_punkte_am_rand_verschwinden():
    """Ein Ordner, der mit einem Punkt beginnt, ist auf macOS versteckt."""
    assert nas_pfad.rendern("{thema}", {"thema": ".versteckt."}) == "versteckt"


# ─── Vorschau für die Oberfläche ────────────────────────────────────────────

def test_vorschau_zeigt_beide_faelle():
    """Mit und ohne die optionalen Werte — der zweite Fall wird am leichtesten
    übersehen."""
    v = nas_pfad.vorschau(nas_pfad.STANDARD_VORLAGEN["nas_vorlage_hochzeit"])
    assert v["gueltig"] is True
    assert v["pfad"] == "Hochzeiten/2026/2026-06-06 Robin & Laura/2. Teil"
    assert v["ohne_optionale"] == "Hochzeiten/2026/2026-06-06"


def test_vorschau_meldet_fehler_statt_zu_werfen():
    """Die Oberfläche braucht einen Satz, keinen Stacktrace."""
    v = nas_pfad.vorschau("/{gibtsnicht}")
    assert v["gueltig"] is False
    assert v["fehler"]
    assert v["pfad"] is None
