"""Typen der Erkennung: Orchester aus dem Text, Stufe 3 aus dem Sprachmodell.

Pegel und Klang sind gestellt; das Sprachmodell ist ersetzt. Geprüft wird, wer wen
überstimmt: Ansage schlägt Modell, Modell schlägt Klang, und ohne Transkript sagt der
Text gar nichts.
"""
import json
import sys

import httpx

sys.path.insert(0, "audio-service")

import automode  # noqa: E402
import classifier  # noqa: E402
import einordnung  # noqa: E402


def _transkript(ordner, zeilen):
    with open(ordner / "transcript.jsonl", "w", encoding="utf-8") as f:
        for start, text in zeilen:
            f.write(json.dumps({"start": start, "text": text}, ensure_ascii=False) + "\n")


def _klang(klasse, typ):
    def klang(w, von, bis, erwartet=None):
        return classifier.Einschaetzung(klasse=klasse, typ=typ, sicherheit=0.6)
    return klang


def test_vielfalt_statt_menge():
    amen = [{"text": "Amen."}] * 40 + [{"text": "Vielen Dank."}] * 5
    assert classifier.klingt_instrumental(amen, 180)
    lied = [{"text": "Stern auf den ich schaue, Fels auf dem ich steh"},
            {"text": "Führer, dem ich traue, Stab, an dem ich geh"}]
    assert not classifier.klingt_instrumental(lied, 120)


def test_chorlied_ohne_gesang_wird_orchester(tmp_path, monkeypatch):
    wav = tmp_path / "aufnahme.wav"
    wav.write_bytes(b"")
    _transkript(tmp_path, [(20.0 + i * 10, "Amen.") for i in range(18)] + [(205.0, "Danke.")])
    monkeypatch.setattr(classifier, "finde_beitraege", lambda w: [(10.0, 200.0)])
    monkeypatch.setattr(classifier, "klassifiziere_bereich", _klang("Musik", "Chorlied"))
    b = automode.nachlauf(wav, mit_modell=False)[0]
    assert (b["typ"], b["quelle"]) == ("Orchester", "text")


def test_ohne_transkript_bleibt_der_klang(tmp_path, monkeypatch):
    wav = tmp_path / "aufnahme.wav"
    wav.write_bytes(b"")
    monkeypatch.setattr(classifier, "finde_beitraege", lambda w: [(10.0, 200.0)])
    monkeypatch.setattr(classifier, "klassifiziere_bereich", _klang("Musik", "Chorlied"))
    assert automode.nachlauf(wav, mit_modell=False)[0]["typ"] == "Chorlied"


def test_modell_schlaegt_klang(tmp_path, monkeypatch):
    wav = tmp_path / "aufnahme.wav"
    wav.write_bytes(b"")
    _transkript(tmp_path, [(20.0, "Es war einmal ein Bauer, der hatte drei Söhne.")])
    monkeypatch.setattr(classifier, "finde_beitraege", lambda w: [(10.0, 600.0)])
    monkeypatch.setattr(classifier, "klassifiziere_bereich", _klang("Sprache", "Predigt"))
    monkeypatch.setattr(einordnung, "ordne_ein", lambda text, dauer: ("Prosa", "erzählt"))
    b = automode.nachlauf(wav, mit_modell=True)[0]
    assert (b["typ"], b["quelle"]) == ("Prosa", "modell")
    assert b["alternativen"][0] == "Predigt"


def test_ansage_schlaegt_modell(tmp_path, monkeypatch):
    wav = tmp_path / "aufnahme.wav"
    wav.write_bytes(b"")
    monkeypatch.setattr(classifier, "finde_beitraege", lambda w: [(10.0, 600.0)])
    monkeypatch.setattr(classifier, "klassifiziere_bereich", _klang("Sprache", "Predigt"))
    gefragt = []
    monkeypatch.setattr(einordnung, "ordne_ein", lambda t, d: gefragt.append(t) or ("Prosa", ""))

    def ansage(self, b):
        b.typ, b.quelle = "Gedicht", "ansage"
    monkeypatch.setattr(automode.AutoModus, "_ansage_auswerten", ansage)
    b = automode.nachlauf(wav, mit_modell=True)[0]
    assert (b["typ"], b["quelle"]) == ("Gedicht", "ansage")
    assert gefragt == []


def test_modell_aus_bleibt_klang(tmp_path, monkeypatch):
    wav = tmp_path / "aufnahme.wav"
    wav.write_bytes(b"")
    _transkript(tmp_path, [(20.0, " ".join(["Wort"] * 40))])
    monkeypatch.setattr(classifier, "finde_beitraege", lambda w: [(10.0, 600.0)])
    monkeypatch.setattr(classifier, "klassifiziere_bereich", _klang("Sprache", "Predigt"))
    monkeypatch.setattr(einordnung, "OLLAMA_URL", "http://127.0.0.1:9")
    b = automode.nachlauf(wav, mit_modell=True)[0]
    assert (b["typ"], b["quelle"]) == ("Predigt", "klang")


def test_antwort_ausserhalb_der_liste_wird_verworfen(monkeypatch):
    class Antwort:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": json.dumps({"typ": "Lobpreis", "grund": "x"})}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: Antwort())
    assert einordnung.ordne_ein(" ".join(["Wort"] * 30), 300) is None


def test_neue_beitragsart_kommt_ueberall_an(monkeypatch):
    """Eine in der Datenbank angelegte Art erreicht Ansagen-Leser und Einordnung —
    ohne dass eine feste Liste im Code nachgezogen werden muss."""
    import programm
    monkeypatch.setattr(programm, "ERLAUBTE_TYPEN", list(programm.ERLAUBTE_TYPEN))
    programm.erlaubte_setzen(["Predigt", "Zeugnis", "Männerchor", "Unbenannt"])
    assert programm.ERLAUBTE_TYPEN == ["Predigt", "Zeugnis", "Männerchor"]

    monkeypatch.setattr(classifier, "SPRACH_TYPEN", ("Predigt", "Zeugnis", "Lesung"))
    gesehen = {}

    class Antwort:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": json.dumps({"typ": "Zeugnis", "grund": "Ich-Form"})}

    def post(url, json=None, **k):
        gesehen["prompt"] = json["prompt"]
        return Antwort()
    monkeypatch.setattr(httpx, "post", post)
    assert einordnung.ordne_ein(" ".join(["Wort"] * 30), 300) == ("Zeugnis", "Ich-Form")
    assert "- Zeugnis: ein persönlicher Bericht" in gesehen["prompt"]
    assert "- Lesung\n" in gesehen["prompt"] + "\n"     # ohne Beschreibung, aber wählbar
