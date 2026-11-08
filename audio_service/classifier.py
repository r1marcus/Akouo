"""Erkennt selbständig, was gerade läuft, und wo ein Beitrag anfängt und aufhört.

Zwei Bausteine:

  klassifiziere_bereich()  — was ist das? Sprache oder Musik, dann der Track-Typ
  finde_beitraege()        — wo sind die Grenzen? Stille plus Klassenwechsel

Gemessen an den handgesetzten Markern aus Session 11 (Sonntagmorgen, 10 Tracks):
Sprache/Musik 11/11 richtig und schon nach 3 Sekunden entschieden, Grenzen 9/10
innerhalb von 25 Sekunden, die meisten innerhalb von 10.

Bewusst ohne Modell und ohne Training: die Merkmale trennen Sprache und Musik mit
großem Abstand, und für die feineren Typen (Chor gegen Gemeinde gegen Orchester)
fehlen noch die Daten. Sobald genug gelabelte Segmente vorliegen, gehört an dieser
Stelle ein gelerntes Modell hin — die Schnittstelle bleibt dieselbe.
"""
from __future__ import annotations

import re
import wave
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from boundaries import envelope

ANALYSE_RATE = 16000
FRAME_S = 0.032
HOP_S = 0.016

# Schwellen mittig in die Lücke zwischen Musik und Sprache gelegt, gemessen an
# Session 11 und 13. Jedes Merkmal eine Stimme — ein einzelnes darf nicht kippen.
SCHWELLEN = {
    "flachheit": 0.38,        # Musik 0.29, Sprache 0.49
    "schwerpunkt": 1950.0,    # Musik 1450 Hz, Sprache 2400 Hz
    "zcr_streuung": 0.11,     # Musik 0.05, Sprache 0.18
    "leise_anteil": 0.21,     # Sprache atmet, Musik läuft durch
    "fluss_streuung": 0.95,
}
STIMMEN_FUER_SPRACHE = 3

# Stereo-Korrelation L/R als sechste, halbe Stimme. Das Rednerpult-Mikrofon liegt mittig
# auf beiden Kanälen, Chor und Instrumente kommen über mehrere Mikrofone verteilt.
# Gemessen am 8. November (Session 36): Predigt, Gebet, Ansage, Vermeldungen 0,999–1,000,
# Chorlied, Gemeindegesang, Gruppenlied 0,38–0,82. Über zehn weitere Sessions dasselbe
# Bild — aber ein Musikstück lag bei 0,96 und ein Kinderlied, das über ein einzelnes
# Mikrofon lief, bei 0,85. Darum nie ein Veto: über der oberen Schwelle eine Stimme mehr
# für Sprache, unter der unteren eine weniger, dazwischen und bei Mono gar nichts.
KORRELATION_SPRACHE = 0.95
KORRELATION_MUSIK = 0.8

MUSIK_TYPEN = ("Chorlied", "Gruppenlied", "Orchester", "Jugendchor", "Kinderchor",
               "Gemeindegesang")
SPRACH_TYPEN = ("Predigt", "Kindergeschichte", "Prosa", "Gedicht", "Ansage",
                "Vermeldungen", "Anspiel", "Gebet")

# Wird nicht als Marker angelegt, weil belegbar: die Ansage benennt den
# Gemeindegesang ausdrücklich ("wir singen gemeinsam").
#
# Gebet steht bewusst NICHT hier. Gemessen am Erntedankfest spricht das Gedicht
# Gott mit 11,2 Prozent sogar häufiger an als die drei echten Gebete mit 6,5 bis
# 8,0 — Dichtung und Gebet sind über den Text nicht zu trennen. Ein überflüssiger
# Marker kostet einen Klick, ein fehlender kostet die Aufnahme.
NICHT_EXPORTIEREN = ("Gemeindegesang", "Kinderlied")

# Diese Typen gibt es, aber die Erkennung schlägt sie nie von sich aus vor: ein
# Gebetslied klingt wie ein Chorlied, ein Musikstück (Musik auf der Bühne) wie ein
# Orchesterbeitrag, ein Kinderlied wie Gemeindegesang. Akustisch ist das nicht zu
# trennen, und Raten wäre hier schlimmer als Schweigen. Sie entstehen nur, wenn sie
# angesagt werden oder der Tontechniker sie setzt. Für die Prüfung "Typ passt zur
# Klasse" zählen sie mit.
NUR_AUF_ANSAGE = {"Gebetslied": "Musik", "Musikstück": "Musik", "Kinderlied": "Musik"}

# Aus den bisherigen Aufnahmen. Dienen als Vermutung, solange die Länge noch
# nichts ausschließt — nicht als Vorhersage.
TYPISCHE_DAUER = {
    "Gedicht": (60, 160),
    "Orchester": (120, 200),
    "Gruppenlied": (150, 260),
    "Chorlied": (120, 340),
    "Ansage": (30, 200),
    "Prosa": (300, 900),
    "Kindergeschichte": (300, 900),
    "Gemeindegesang": (120, 340),
    "Gebet": (45, 400),
    "Predigt": (260, 2000),
}

# Segmentierung
BLOCK_MS = 250
MIN_STILLE_S = 8.0     # kürzere Pausen trennen nicht — Predigten atmen
MIN_BEITRAG_S = 45.0
STILLE_UEBER_GRUND_DB = 6.0
KLASSEN_FENSTER_S = 6.0
KLASSEN_HALTEN_S = 30.0

# Zweite, tiefere Schwelle für den Anfang. Die Stille-Schwelle oben bestätigt einen
# Beitrag, setzt seinen Anfang aber zu spät, sobald er leise beginnt: am 8. November
# (Sonntagabend) begann ein Chorlied mit einem Intro bei −53 bis −34 dBFS, die
# Schwelle stand bei −32, und der Marker landete acht Sekunden hinter dem Anfang.
# Von der bestätigten Stelle aus wird deshalb rückwärts gesucht, solange es über dem
# örtlichen Grundpegel bleibt — höchstens so weit, nie in den Beitrag davor.
VORLAUF_MAX_S = 15.0
VORLAUF_UEBER_GRUND_DB = 6.0
VORLAUF_LUECKE_S = 0.5        # so lange darf ein leises Intro kurz absacken
DIGITALE_STILLE_DBFS = -60.0  # Untergrenze der Hüllkurve: Kanal stumm, hier ist sicher Schluss


def start_vorziehen(pegel, start: int, grenze: int, block_s: float) -> int:
    """Index, ab dem ein Beitrag wirklich anfängt — nie später als `start`.

    `pegel` ist die Hüllkurve in dBFS je Block, `start` der Block, an dem die
    Stille-Schwelle den Beitrag bestätigt hat, `grenze` der erste Block, den er
    belegen darf (Ende des vorigen Beitrags).

    Der Grundpegel wird örtlich gemessen, aus der halben Minute davor: über die ganze
    Aufnahme gemittelt steigt er mit jeder lauten Strecke, und genau daran scheitert
    ein leises Intro. Digitale Stille ist eine harte Grenze — davor kann nichts
    gesungen worden sein.
    """
    pegel = np.asarray(pegel, dtype=float)
    max_bloecke = int(VORLAUF_MAX_S / block_s)
    untergrenze = max(0, grenze, start - max_bloecke)
    if untergrenze >= start or start > len(pegel):
        return start
    umgebung = pegel[max(0, start - 2 * max_bloecke): start]
    leise = float(np.percentile(umgebung, 20)) + VORLAUF_UEBER_GRUND_DB
    luecke_max = max(1, round(VORLAUF_LUECKE_S / block_s))
    neu, luecke = start, 0
    for i in range(start - 1, untergrenze - 1, -1):
        if pegel[i] <= DIGITALE_STILLE_DBFS:
            break
        if pegel[i] > leise:
            neu, luecke = i, 0
        else:
            luecke += 1
            if luecke > luecke_max:
                break
    return neu


@dataclass
class Einschaetzung:
    """Was das System gerade glaubt — mit Alternativen, damit man wechseln kann."""
    klasse: str                      # "Musik" | "Sprache"
    typ: str
    sicherheit: float                # 0..1
    alternativen: list = field(default_factory=list)
    stimmen: int = 0
    dauer_s: float = 0.0


def _korrelation(kanaele: np.ndarray) -> Optional[float]:
    """Pearson-Korrelation der ersten beiden Kanäle. None bei Mono oder einem stillen Kanal
    — dann sagt sie nichts, und ein stummer Kanal ist kein Hinweis auf Sprache."""
    if kanaele.ndim != 2 or kanaele.shape[1] < 2 or kanaele.shape[0] < 2:
        return None
    links = kanaele[:, 0] - kanaele[:, 0].mean()
    rechts = kanaele[:, 1] - kanaele[:, 1].mean()
    nenner = float(np.sqrt(np.dot(links, links) * np.dot(rechts, rechts)))
    if nenner < 1e-6:
        return None
    return float(np.dot(links, rechts) / nenner)


def _lade(wav_path, von_s: float, bis_s: float):
    """Mono-Signal für die Merkmale plus die L/R-Korrelation des Bereichs.

    Die Korrelation wird vor dem Mitteln bestimmt, danach ist sie weg. Sie läuft hier,
    im Analysefaden, und nie im PortAudio-Callback.
    """
    with wave.open(str(wav_path), "rb") as wf:
        rate, kanaele, gesamt = wf.getframerate(), wf.getnchannels(), wf.getnframes()
        erster = max(0, int(von_s * rate))
        letzter = min(gesamt, int(bis_s * rate))
        if letzter <= erster:
            return np.array([]), rate, None
        wf.setpos(erster)
        roh = wf.readframes(letzter - erster)
    daten = np.frombuffer(roh, dtype=np.int16)
    nutzbar = (len(daten) // kanaele) * kanaele
    alle = daten[:nutzbar].reshape(-1, kanaele).astype(np.float32) / 32768.0
    mono = alle.mean(axis=1)
    schritt = max(1, rate // ANALYSE_RATE)
    return mono[::schritt], rate // schritt, _korrelation(alle)


def merkmale(signal: np.ndarray, rate: int, korrelation: Optional[float] = None) -> Optional[dict]:
    """Klassische Sprache/Musik-Merkmale. None, wenn zu wenig Material da ist.

    `korrelation` ist die L/R-Korrelation aus _lade; sie wird nur durchgereicht."""
    if signal.size < rate:
        return None
    n, h = int(FRAME_S * rate), int(HOP_S * rate)
    anzahl = 1 + (len(signal) - n) // h
    if anzahl < 10:
        return None
    fenster = np.lib.stride_tricks.as_strided(
        signal, shape=(anzahl, n), strides=(signal.strides[0] * h, signal.strides[0])
    ) * np.hanning(n)

    energie = np.sqrt(np.mean(fenster ** 2, axis=1)) + 1e-9
    db = 20 * np.log10(energie)
    zcr = np.mean(np.abs(np.diff(np.sign(fenster), axis=1)) > 0, axis=1)

    spektrum = np.abs(np.fft.rfft(fenster, axis=1)) + 1e-9
    freq = np.fft.rfftfreq(n, 1 / rate)
    schwerpunkt = np.sum(spektrum * freq, axis=1) / np.sum(spektrum, axis=1)
    flachheit = np.exp(np.mean(np.log(spektrum), axis=1)) / np.mean(spektrum, axis=1)
    fluss = np.sqrt(np.sum(np.diff(spektrum, axis=0) ** 2, axis=1))

    return {
        "zcr_mittel": float(np.mean(zcr)),
        "zcr_streuung": float(np.std(zcr)),
        "schwerpunkt": float(np.mean(schwerpunkt)),
        "flachheit": float(np.mean(flachheit)),
        "fluss_streuung": float(np.std(fluss) / (np.mean(fluss) + 1e-9)),
        "leise_anteil": float(np.mean(db < (np.mean(db) - 8))),
        "korrelation": korrelation,
    }


def _stereo_stimme(korrelation: Optional[float]) -> int:
    """+1 für Sprache, −1 für Musik, 0 wenn die Korrelation nichts sagt."""
    if korrelation is None:
        return 0
    if korrelation > KORRELATION_SPRACHE:
        return 1
    if korrelation < KORRELATION_MUSIK:
        return -1
    return 0


def _stimmen_fuer_sprache(f: dict) -> int:
    """Stimmen der fünf Klangmerkmale, verschoben um die Stereo-Stimme.

    Die Skala bleibt 0 bis 5, damit STIMMEN_FUER_SPRACHE (einstellbar) und die
    Sicherheit in klassifiziere_bereich ihre Bedeutung behalten. Die Stereo-Stimme kann
    einen Grenzfall kippen (2 → 3, 3 → 2), aber nie ein klares Urteil umdrehen.
    """
    klang = sum([
        f["flachheit"] > SCHWELLEN["flachheit"],
        f["schwerpunkt"] > SCHWELLEN["schwerpunkt"],
        f["zcr_streuung"] > SCHWELLEN["zcr_streuung"],
        f["leise_anteil"] > SCHWELLEN["leise_anteil"],
        f["fluss_streuung"] > SCHWELLEN["fluss_streuung"],
    ])
    return min(5, max(0, klang + _stereo_stimme(f.get("korrelation"))))


# Rein instrumentale Musik ist deutlich tonaler als gesungene: gemessen 0.14 und
# 0.17 beim Orchester gegen 0.27 bis 0.37 bei Chor und Gemeinde. Stimmen bringen
# Rauschanteile mit, Instrumente nicht.
ORCHESTER_FLACHHEIT = 0.22


def _typ_vermutung(klasse: str, dauer_s: float, erwartet: Optional[list],
                   f: Optional[dict] = None) -> list:
    """Kandidaten der Klasse, sortiert: passende Länge zuerst, dann Ablauf-Vorlage."""
    # NUR_AUF_ANSAGE bleibt bewusst draußen — geraten wird es nie.
    kandidaten = MUSIK_TYPEN if klasse == "Musik" else SPRACH_TYPEN

    instrumental = f is not None and f["flachheit"] < ORCHESTER_FLACHHEIT

    def punkte(typ: str) -> float:
        p = 0.0
        if klasse == "Musik":
            # Kein Gesang zu hören heißt Orchester, Gesang schließt es aus.
            if typ == "Orchester":
                p += 3.0 if instrumental else -3.0
            elif instrumental:
                p -= 2.0
        # Ohne bekannte Dauer weder Plus noch Minus. Vorher galt (0, ∞) — dann passte
        # die Länge immer, und ein selbst angelegter Typ wie „Anspiel" schlug jede
        # Predigt, nur weil er in der Liste weiter vorn stand.
        von, bis = TYPISCHE_DAUER.get(typ, (None, None))
        if von is None:
            pass
        elif von <= dauer_s <= bis:
            p += 2.0
        elif dauer_s > bis:
            p -= 1.5          # länger als je gesehen — spricht dagegen
        elif dauer_s < von:
            p += 0.5          # noch zu kurz, kann aber noch hineinwachsen
        if erwartet and typ in erwartet:
            p += 0.7          # Ablauf-Vorlage als Tiebreaker, nicht als Vorhersage
        return p

    return sorted(kandidaten, key=punkte, reverse=True)


def klassifiziere_bereich(wav_path, von_s: float, bis_s: float,
                          erwartet: Optional[list] = None) -> Optional[Einschaetzung]:
    """Was läuft zwischen von_s und bis_s?

    Für die Klasse genügen 30 Sekunden aus der Mitte — mehr zu lesen kostet nur Zeit.
    Bei einem noch laufenden Beitrag ist bis_s einfach "jetzt": die Einschätzung wird
    mit wachsender Dauer von selbst schärfer.
    """
    dauer = bis_s - von_s
    mitte = (von_s + bis_s) / 2
    von = max(von_s, mitte - 15)
    signal, rate, korrelation = _lade(wav_path, von, min(bis_s, von + 30))
    f = merkmale(signal, rate, korrelation)
    if f is None:
        return None

    stimmen = _stimmen_fuer_sprache(f)
    klasse = "Sprache" if stimmen >= STIMMEN_FUER_SPRACHE else "Musik"
    # 0 oder 5 Stimmen heißt eindeutig, 2 oder 3 heißt Grenzfall
    eindeutig = abs(stimmen - 2.5) / 2.5

    rang = _typ_vermutung(klasse, dauer, erwartet, f)
    von_d, bis_d = TYPISCHE_DAUER.get(rang[0], (0, 1e9))
    passt = 1.0 if von_d <= dauer <= bis_d else 0.5
    return Einschaetzung(
        klasse=klasse,
        typ=rang[0],
        sicherheit=round(min(1.0, eindeutig * passt), 2),
        alternativen=rang[1:],
        stimmen=stimmen,
        dauer_s=round(dauer, 1),
    )


# ── Transkript ───────────────────────────────────────────────────────────────
# Was der Klang nicht hergibt, steht oft im Text. Gemessen an Session 11:
# Gebete sprechen zu 8 Prozent Gott direkt an, Predigten zu unter 2 — und eine
# Ansage sagt rundheraus, was als Nächstes kommt.

_ANREDE = re.compile(r"\b(herr|vater|dir|dich|du|dein[emnrs]?)\b", re.I)
_BITTE = re.compile(r"\b(hilf|bitte|segne|schenke|bewahre|vergib|wir danken)\b", re.I)
_BIBELSTELLE = re.compile(r"\b(kapitel|vers)\s*\d+", re.I)

# Ankündigungen. Bewusst eng gefasst: ein weites Muster fängt zehnmal
# "Wort Gottes" mitten in der Predigt ein.
_ANKUENDIGUNG = [
    # "unser Chor singt" gegen "wir singen gemeinsam" — das ist der Unterschied
    # zwischen einem Beitrag, der ins Archiv gehört, und dem Gemeindegesang, der
    # nicht exportiert wird. Klanglich sind beide nicht zu trennen, die Ansage
    # sagt es in vier Wörtern.
    (re.compile(r"\b(unser |der )?chor\s+singt\b", re.I), "Chorlied"),
    (re.compile(r"\bchorlied\b", re.I), "Chorlied"),
    (re.compile(r"\bder (jugendchor|kinderchor)\b", re.I), "Jugendchor"),
    (re.compile(r"\bwir singen (gemeinsam|zusammen|alle)\b", re.I), "Gemeindegesang"),
    (re.compile(r"\b(gemeinsam|zusammen) singen wir\b", re.I), "Gemeindegesang"),
    (re.compile(r"\bdas orchester\b", re.I), "Orchester"),
    (re.compile(r"\bkindergeschichte\b", re.I), "Kindergeschichte"),
    (re.compile(r"\b(ein |das )?gedicht\b.{0,30}\b(vortragen|hören|folgt)\b", re.I), "Gedicht"),
    (re.compile(r"\bwir hören (nun |jetzt )?(ein|das) wort\b", re.I), "Predigt"),
]
_LIEDNUMMER = re.compile(r"\b(?:lied|nummer)\s*(\d{1,3})\b", re.I)


# Whispers Verhalten bei Nicht-Sprache. Diese Zeilen erscheinen, wenn nichts
# Verständliches anliegt — gemessen am Erntedankfest: 17x "MUZIEK" während des
# Orchesters, 28x "Untertitelung des ZDF" über Musik, 5x "Vielen Dank".
_HALLUZINATION = (
    "muziek", "music", "untertitelung", "untertitel", "vielen dank",
    "das war's", "bis zum nächsten mal", "copyright", "swr", "zdf",
)
MIN_WIEDERHOLUNG = 4        # so oft dieselbe Zeile, dann ist es kein Gespräch
# Reine Stellenangaben zählen nicht als Refrain. In einer Lesepause wiederholt Whisper
# die letzte Angabe: viermal "Vers 2." in einer Bibelstunde, fünfmal "Vers 10. Vers 10."
# in einer Predigt — beide wurden zu Gesang erklärt.
_NUR_STELLE = re.compile(r"^(?:(?:vers|verse|kapitel)\s*\d+[\s.,:;-]*)+$", re.I)


def gesang_aus_text(zeilen: list) -> tuple[bool, str]:
    """Deutet der Text darauf hin, dass hier gesungen und nicht geredet wird?

    Zwei Signale. Erstens Whispers Halluzinationen: bei Musik ohne Text wirft es
    Füllsätze aus, immer dieselben. Zweitens echte Wiederholung — ein Refrain
    kommt mehrfach wortgleich, ein Redner wiederholt sich nie so.

    "Amen" zählt bewusst nicht als Refrain: das ist die Antwort der Gemeinde im
    Gebet und würde jedes Gebet zu Gesang machen.
    """
    texte = [z.get("text", "").strip() for z in zeilen if z.get("text", "").strip()]
    if not texte:
        return False, ""

    treffer = [t for t in texte if any(h in t.lower() for h in _HALLUZINATION)]
    if len(treffer) >= MIN_WIEDERHOLUNG:
        return True, f"{len(treffer)}x Füllsatz ({treffer[0][:30]})"

    lauf, letzte, groesster, wovon = 1, None, 1, ""
    for t in texte:
        if _NUR_STELLE.match(t):
            lauf, letzte = 1, None
            continue
        if t == letzte:
            lauf += 1
            if lauf > groesster:
                groesster, wovon = lauf, t
        else:
            lauf, letzte = 1, t
    if groesster >= MIN_WIEDERHOLUNG and wovon.lower().rstrip(".!").strip() != "amen":
        return True, f"{groesster}x dieselbe Zeile ({wovon[:30]})"
    return False, ""


# Bei Instrumentalmusik schreibt Whisper trotzdem etwas — "Amen. Amen. Amen." oder
# "Vielen Dank" —, im Mittel 26 Wörter pro Minute. Die Menge trennt deshalb nicht,
# die Vielfalt schon: gemessen an 22 Orchesterstücken und 41 gesungenen Beiträgen
# (Stand 6. November 2026) haben 14 Orchesterstücke weniger als 4 unterschiedliche
# Wörter pro Minute, aber nur 2 gesungene. Umgekehrt beweist viel Text keinen Gesang:
# ein Orchester, das die Gemeinde begleitet, kommt auf bis zu 22.
_FUELLWORT = {"amen", "vielen", "dank", "danke", "untertitel", "untertitelung", "im",
              "auftrag", "des", "zdf", "swr", "wdr", "musik", "muziek", "music",
              "applaus", "copyright", "fürs", "zuschauen"}
INSTRUMENTAL_UNTER = 4.0     # unterschiedliche Wörter je Minute


def eigene_woerter_je_minute(zeilen: list, dauer_s: float) -> float:
    """Unterschiedliche Wörter je Minute, ohne Whispers Füllsätze."""
    if dauer_s <= 0:
        return 0.0
    text = " ".join(z.get("text", "") for z in zeilen).lower()
    woerter = {w for w in re.findall(r"[a-zäöüß]+", text) if w not in _FUELLWORT}
    return len(woerter) / (dauer_s / 60)


def klingt_instrumental(zeilen: list, dauer_s: float) -> bool:
    """Musik, in der niemand singt — Orchester, nicht Chorlied mit Begleitung."""
    return eigene_woerter_je_minute(zeilen, dauer_s) < INSTRUMENTAL_UNTER


def ist_gebet(text: str) -> tuple[bool, float]:
    """Gebet oder Verkündigung? Gibt (ja/nein, Anteil der Gottesanrede) zurück.

    Ein Gebet spricht Gott an, eine Predigt spricht über ihn und nennt Stellen.
    """
    woerter = max(1, len(text.split()))
    anteil = len(_ANREDE.findall(text)) / woerter * 100
    stellen = len(_BIBELSTELLE.findall(text))
    bitten = len(_BITTE.findall(text)) / woerter * 100
    return (anteil > 4.5 and stellen == 0) or (anteil > 3.0 and bitten > 1.5), round(anteil, 1)


# Nur ausdrücklich Vergangenes ist keine Ankündigung. "Nach dem Chorlied hören wir
# die Kindergeschichte" kündigt dagegen BEIDES an — der Sprecher blickt nach vorn,
# und beide Beiträge stehen noch bevor.
_RUECKVERWEIS = re.compile(r"\b(haben wir (eben|gerade)|vorhin|soeben)\b", re.I)


def erkenne_ankuendigung(text: str) -> Optional[dict]:
    """Sagt eine Ansage, was als Nächstes kommt?

    Das löst den Fall, an dem der Klang scheitert: "unser Chor singt" gegen
    "wir singen gemeinsam" trennt einen Chorbeitrag vom Gemeindegesang. Beides
    klingt ähnlich und ist gleich lang, aber nur das eine wird exportiert.
    """
    for muster, typ in _ANKUENDIGUNG:
        treffer = muster.search(text)
        if treffer and not _RUECKVERWEIS.search(text):
            nummer = _LIEDNUMMER.search(text)
            return {"erwartet": typ,
                    "exportieren": typ not in NICHT_EXPORTIEREN,
                    "liednummer": int(nummer.group(1)) if nummer else None}
    return None


def _klassenspur(wav_path, start: float, ende: float) -> list:
    spur, t = [], start
    while t < ende - 1:
        signal, rate, korrelation = _lade(wav_path, t, min(t + KLASSEN_FENSTER_S, ende))
        f = merkmale(signal, rate, korrelation)
        spur.append("?" if f is None else
                    ("Sprache" if _stimmen_fuer_sprache(f) >= STIMMEN_FUER_SPRACHE else "Musik"))
        t += KLASSEN_FENSTER_S
    return spur


def _an_klassenwechsel_teilen(wav_path, start: float, ende: float) -> list:
    """Ein Chorlied und die Ansage danach trennt oft keine Pause — aber die Klasse."""
    spur = _klassenspur(wav_path, start, ende)
    halten = max(1, int(KLASSEN_HALTEN_S / KLASSEN_FENSTER_S))
    if len(spur) < 2 * halten:
        return [(start, ende)]
    # Mehrheitsglättung: an einer Grenze flackert die Spur ein paar Fenster lang,
    # das darf den Wechsel nicht verstecken.
    geglaettet = []
    for i in range(len(spur)):
        f = spur[max(0, i - halten // 2): i + halten // 2 + 1]
        geglaettet.append(max(set(f), key=f.count))
    schnitte = [i for i in range(halten, len(geglaettet) - halten)
                if geglaettet[i] != geglaettet[i - 1]]
    gefiltert = [s for j, s in enumerate(schnitte) if j == 0 or s - schnitte[j - 1] > halten]
    grenzen = [start] + [start + s * KLASSEN_FENSTER_S for s in gefiltert] + [ende]
    return [(grenzen[i], grenzen[i + 1]) for i in range(len(grenzen) - 1)]


def finde_beitraege(wav_path, bis_s: Optional[float] = None) -> list:
    """Alle Beiträge einer Aufnahme: [(start_s, ende_s), ...].

    Zwei Stufen: grob an ruhigen Strecken trennen, dann innerhalb der Stücke dort
    schneiden, wo Musik in Sprache übergeht oder umgekehrt.
    """
    pegel, _, block_s = envelope(wav_path, 0, bis_s, block_ms=BLOCK_MS)
    if not pegel:
        return []
    schwelle = float(np.percentile(pegel, 20)) + STILLE_UEBER_GRUND_DB
    laut = np.array(pegel) > schwelle
    min_stille = int(MIN_STILLE_S / block_s)
    min_beitrag = int(MIN_BEITRAG_S / block_s)

    grob, start, stille, voriges_ende = [], None, 0, 0
    for i, ist_laut in enumerate(laut):
        if ist_laut:
            if start is None:
                start = i
            stille = 0
        else:
            stille += 1
            if start is not None and stille >= min_stille:
                ende = i - stille + 1
                if ende - start >= min_beitrag:
                    anfang = start_vorziehen(pegel, start, voriges_ende, block_s)
                    grob.append((anfang * block_s, ende * block_s))
                    voriges_ende = ende
                start = None
    if start is not None and len(laut) - start >= min_beitrag:
        anfang = start_vorziehen(pegel, start, voriges_ende, block_s)
        grob.append((anfang * block_s, len(laut) * block_s))

    fein = []
    for s, e in grob:
        fein.extend(_an_klassenwechsel_teilen(wav_path, s, e))
    return [(s, e) for s, e in fein if e - s >= MIN_BEITRAG_S]


# ─── Von der API geschobene Konfiguration ───────────────────────────────────
# Die Werte oben sind die Startwerte. Welche Beitragsarten es gibt, wie sie klingen,
# wie lange sie typisch dauern und woran eine Ansage sie erkennt, steht seit
# Migration 014 in der Datenbank. Der Audio-Dienst hat keinen Datenbankzugriff — die
# API schiebt den Stand nach jeder Aenderung per PUT /api/config/erkennung herein.
#
# Alle Leser oben greifen innerhalb von Funktionen auf die Modulnamen zu, deshalb
# genuegt ein Neusetzen. Fehlt ein Feld, bleibt der Startwert stehen.

def anwenden(cfg: dict) -> dict:
    """Erkennungskonfiguration uebernehmen. Gibt zurueck, was tatsaechlich gesetzt wurde."""
    global MUSIK_TYPEN, SPRACH_TYPEN, NUR_AUF_ANSAGE, NICHT_EXPORTIEREN
    global TYPISCHE_DAUER, _ANKUENDIGUNG
    global MIN_STILLE_S, MIN_BEITRAG_S, ORCHESTER_FLACHHEIT, STIMMEN_FUER_SPRACHE

    uebernommen = {}

    if "musik_typen" in cfg and cfg["musik_typen"]:
        MUSIK_TYPEN = tuple(cfg["musik_typen"])
        uebernommen["musik_typen"] = len(MUSIK_TYPEN)
    if "sprach_typen" in cfg and cfg["sprach_typen"]:
        SPRACH_TYPEN = tuple(cfg["sprach_typen"])
        uebernommen["sprach_typen"] = len(SPRACH_TYPEN)
    if "nur_auf_ansage" in cfg:
        NUR_AUF_ANSAGE = dict(cfg["nur_auf_ansage"])
        uebernommen["nur_auf_ansage"] = len(NUR_AUF_ANSAGE)
    if "kein_auto_marker" in cfg:
        NICHT_EXPORTIEREN = tuple(cfg["kein_auto_marker"])
        uebernommen["kein_auto_marker"] = len(NICHT_EXPORTIEREN)
    if "typische_dauer" in cfg and cfg["typische_dauer"]:
        TYPISCHE_DAUER = {k: tuple(v) for k, v in cfg["typische_dauer"].items()}
        uebernommen["typische_dauer"] = len(TYPISCHE_DAUER)

    if cfg.get("ansage_regeln"):
        neu = []
        for regel in cfg["ansage_regeln"]:
            try:
                neu.append((re.compile(regel["muster"], re.I), regel["typ"]))
            except re.error:
                # Ein kaputtes Muster darf nicht die ganze Erkennung lahmlegen. Die
                # Oberflaeche prueft beim Speichern, hier ist es die zweite Sicherung.
                continue
        if neu:
            _ANKUENDIGUNG = neu
            uebernommen["ansage_regeln"] = len(neu)

    for feld, name in (("min_stille_s", "MIN_STILLE_S"), ("min_beitrag_s", "MIN_BEITRAG_S"),
                       ("orchester_flachheit", "ORCHESTER_FLACHHEIT")):
        if cfg.get(feld) is not None:
            globals()[name] = float(cfg[feld])
            uebernommen[feld] = float(cfg[feld])
    if cfg.get("stimmen_fuer_sprache") is not None:
        STIMMEN_FUER_SPRACHE = int(cfg["stimmen_fuer_sprache"])
        uebernommen["stimmen_fuer_sprache"] = STIMMEN_FUER_SPRACHE

    return uebernommen


def aktueller_stand() -> dict:
    """Was gerade gilt — fuer die Anzeige in der Oberflaeche und zum Nachsehen."""
    return {
        "musik_typen": list(MUSIK_TYPEN),
        "sprach_typen": list(SPRACH_TYPEN),
        "nur_auf_ansage": dict(NUR_AUF_ANSAGE),
        "kein_auto_marker": list(NICHT_EXPORTIEREN),
        "typische_dauer": {k: list(v) for k, v in TYPISCHE_DAUER.items()},
        "ansage_regeln": [{"muster": rx.pattern, "typ": t} for rx, t in _ANKUENDIGUNG],
        "min_stille_s": MIN_STILLE_S,
        "min_beitrag_s": MIN_BEITRAG_S,
        "orchester_flachheit": ORCHESTER_FLACHHEIT,
        "stimmen_fuer_sprache": STIMMEN_FUER_SPRACHE,
    }
