"""„Neu erkennen" auf der Seite des Audio-Dienstes: automode.nachlauf.

Pegel und Klang sind gestellt — was hier geprüft wird, ist die Reihenfolge: eine
Ansage, die live vor dem Einschalten lag und ungelesen blieb, muss im Nachlauf
gelesen sein, bevor der Beitrag bewertet wird, den sie ankündigt.
"""
import json
import sys

sys.path.insert(0, "audio-service")

import automode
import classifier
import programm


def _transkript(ordner, zeilen):
    with open(ordner / "transcript.jsonl", "w", encoding="utf-8") as f:
        for start, text in zeilen:
            f.write(json.dumps({"start": start, "text": text}, ensure_ascii=False) + "\n")


def test_nachlauf_liest_ansagen_vor_dem_angekuendigten_beitrag(tmp_path, monkeypatch):
    wav = tmp_path / "aufnahme.wav"
    wav.write_bytes(b"")
    _transkript(tmp_path, [(30.0, "Anschließend hören wir ein Lied von der Jugend.")])

    monkeypatch.setattr(classifier, "finde_beitraege", lambda w: [(10.0, 100.0), (120.0, 400.0)])

    def klang(w, von, bis, erwartet=None):
        musik = von >= 120
        return classifier.Einschaetzung(klasse="Musik" if musik else "Sprache",
                                        typ="Chorlied" if musik else "Prosa", sicherheit=0.6)
    monkeypatch.setattr(classifier, "klassifiziere_bereich", klang)

    gefragt = []

    def ansage(text, modell=None):
        gefragt.append(text)
        return [{"typ": "Gruppenlied", "titel": None}]
    monkeypatch.setattr(programm, "lies_ansage", ansage)
    monkeypatch.setattr(automode, "PROGRAMM_AUTOMATISCH", True)

    beitraege = automode.nachlauf(wav)

    assert [(b["start_s"], b["ende_s"]) for b in beitraege] == [(10.0, 100.0), (120.0, 400.0)]
    assert gefragt == ["Anschließend hören wir ein Lied von der Jugend."]
    # Der Klang allein hätte Chorlied gesagt — die Ansage weiß es besser
    assert beitraege[1]["typ"] == "Gruppenlied"
    assert beitraege[1]["quelle"] == "programm"
    assert beitraege[0]["typ"] == "Prosa"


def test_nachlauf_ohne_transkript_und_ohne_beitraege(tmp_path, monkeypatch):
    wav = tmp_path / "aufnahme.wav"
    wav.write_bytes(b"")
    monkeypatch.setattr(classifier, "finde_beitraege", lambda w: [])
    assert automode.nachlauf(wav) == []


def test_typ_ohne_bekannte_dauer_schlaegt_keine_predigt(monkeypatch):
    """Ein selbst angelegter Typ ohne typische Dauer passte vorher auf jede Länge und
    gewann jeden Gleichstand, sobald er in der Liste vorn stand."""
    monkeypatch.setattr(classifier, "SPRACH_TYPEN", ("Anspiel", "Predigt", "Gedicht"))
    monkeypatch.setattr(classifier, "TYPISCHE_DAUER",
                        {"Predigt": (260, 2000), "Gedicht": (60, 160)})
    assert classifier._typ_vermutung("Sprache", 1340.0, None)[0] == "Predigt"


def test_wiederholte_stellenangabe_ist_kein_gesang():
    """Whisper wiederholt in Lesepausen die letzte Angabe — das ist kein Refrain."""
    lesepause = [{"text": "Vers 2."}] * 4 + [{"text": "Vers 10. Vers 10."}] * 5
    assert classifier.gesang_aus_text(lesepause) == (False, "")
    refrain = [{"text": "Gott sei Dank."}] * 4
    assert classifier.gesang_aus_text(refrain)[0] is True
