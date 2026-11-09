"""Chorbildschirm, Seite der Automatik: das Programm des Dirigenten als Warteliste.

Der Zeiger ist der Punkt, auf dem der Dirigent steht. Er gilt als Hinweis auf den
*nächsten* Beitrag, maßgeblich ist der Stand bei dessen Beginn — der Dirigent blättert
oft schon während der Predigt weiter.
"""
import asyncio
import sys
from unittest.mock import patch

sys.path.insert(0, "audio_service")

import automode  # noqa: E402


# ─── Automatik: Vorgabe und Zeiger ───────────────────────────────────────────

class _Rekorder:
    def __init__(self):
        self.sekunden = 0

    def status(self):
        return {"state": "recording", "elapsed_seconds": self.sekunden, "wav_path": "/tmp/x.wav"}


VORGABE = [
    {"typ": "Chorlied", "titel": "Eins", "index": 1},
    {"typ": "Predigt", "titel": None, "index": 2},
    {"typ": "Chorlied", "titel": "Zwei", "index": 3},
    {"typ": "Chorlied", "titel": "Drei", "index": 4},
    {"typ": "Gedicht", "titel": "Ein Gedicht", "index": 5},
]


def _modus():
    rek = _Rekorder()
    m = automode.AutoModus(rek)
    m.aktiv = True
    return m, rek


def test_vorgabe_wird_zur_warteliste_und_veraltet_nicht():
    m, _ = _modus()
    m.vorgabe_setzen("chor-1", VORGABE, None)
    assert [e.typ for e in m.programm.offen()] == ["Chorlied", "Predigt", "Chorlied", "Chorlied", "Gedicht"]
    # Eine Stunde nach Beginn gilt das gedruckte Programm noch — eine Ansage wäre überholt
    b = automode.Beitrag(nr=1, start_s=3600.0)
    eintrag = m._aus_programm(b, "Musik")
    assert (eintrag.typ, eintrag.titel) == ("Chorlied", "Eins")


def test_vorgabe_vor_der_aufnahme_gilt_ab_dem_start():
    m, _ = _modus()
    m.aktiv = False
    m.vorgabe_setzen("chor-1", VORGABE, 3)
    assert m.programm.eintraege == []           # noch keine Aufnahme
    m._recorder.status = lambda: {"state": "recording", "elapsed_seconds": 0, "wav_path": "/tmp/x.wav"}
    with patch("asyncio.create_task"):
        asyncio.run(m.start())
    assert m.programm.hat_vorgabe()
    assert m._zeiger_bei(0.0) == 3


def test_zeiger_waehrend_der_predigt_meint_den_naechsten_beitrag():
    """Der Dirigent blättert in der Predigt schon zum nächsten Lied. Das Lied danach
    bekommt den Titel, auf dem er zu dessen Beginn stand — nicht das Lied, zu dem er
    während des Lieds schon weitergeblättert hat."""
    m, rek = _modus()
    m.vorgabe_setzen("chor-1", VORGABE, 1)
    rek.sekunden = 900                     # mitten in der Predigt: weiter auf „Drei“
    m.vorgabe_setzen("chor-1", VORGABE, 4)
    rek.sekunden = 1500                    # mitten im Lied schon auf das Gedicht
    m.vorgabe_setzen("chor-1", VORGABE, 5)

    lied = automode.Beitrag(nr=3, start_s=1400.0)
    eintrag = m._aus_programm(lied, "Musik")
    assert eintrag.titel == "Drei"
    # „Eins“, die Predigt und „Zwei“ liegen davor — gelaufen oder ausgefallen
    assert [e.titel for e in m.programm.offen()] == ["Ein Gedicht"]


def test_zeiger_mit_falscher_klasse_ist_nur_ein_hinweis():
    """Er steht schon auf dem Lied, gehört wird noch Sprache: dann zählt die Warteliste."""
    m, _ = _modus()
    m.vorgabe_setzen("chor-1", VORGABE, 3)
    eintrag = m._aus_programm(automode.Beitrag(nr=1, start_s=10.0), "Sprache")
    assert eintrag.typ == "Predigt"
    assert [e.titel for e in m.programm.offen()][:2] == ["Zwei", "Drei"]


def test_dasselbe_programm_aendert_nur_den_zeiger():
    m, _ = _modus()
    m.vorgabe_setzen("chor-1", VORGABE, 1)
    m.programm.offen()[0].erledigt = True
    antwort = m.vorgabe_setzen("chor-1", VORGABE, 2)
    assert antwort["neu"] is False
    assert len(m.programm.offen()) == 4            # das Abgehakte bleibt abgehakt
    assert m.vorgabe_setzen("chor-2", VORGABE, 0)["neu"] is True
    assert len(m.programm.offen()) == 5


def test_mit_vorgabe_werden_ansagen_nicht_mehr_gelesen():
    m, _ = _modus()
    m.vorgabe_setzen("chor-1", VORGABE, None)
    with patch("programm.lies_ansage") as lies:
        m._ansagen_mitlesen()
    lies.assert_not_called()


def test_vorgabe_loeschen():
    m, _ = _modus()
    m.vorgabe_setzen("chor-1", VORGABE, 1)
    m.vorgabe_loeschen()
    assert (m.vorgabe, m.programm.eintraege, m.status()["zeiger"]) == (None, [], None)
