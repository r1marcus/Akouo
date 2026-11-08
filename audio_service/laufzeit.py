"""Werte, die die API zur Laufzeit hereinschiebt.

Der Audio-Dienst hat keinen Datenbankzugriff; die Einstellungen stehen aber dort. Was
sich im Betrieb ändern lassen soll, landet deshalb hier und wird von den Aufrufstellen
bei jedem Export bzw. jeder Grenzensuche frisch gelesen — nicht beim Import gebunden.

Die Startwerte sind die bisherigen Konstanten, damit der Dienst auch ohne die API
sinnvoll arbeitet (etwa wenn er nach einem Neustart vor ihr hochkommt).
"""

EXPORT = {
    "bitrate": "192k",      # MP3-Bitrate
    "peak_dbfs": -3.0,      # Spitzenpegel, auf den normalisiert wird
    "fade_ms": 1000,        # Ein- und Ausblende je Segment
    "normalisierung": "Spitzenpegel",   # oder "Lautheit"
    "lufs_sprache": -16.0,  # Ziel bei "Lautheit" für Gesprochenes
    "lufs_musik": -14.0,    # Ziel bei "Lautheit" für Gesungenes und Gespieltes
}

# Beitragsarten ohne Klangklasse, die trotzdem Musik sind. Sie stehen in der Datenbank
# bewusst ohne Klasse ("nur auf Ansage"), damit die Klangerkennung sie nicht vorschlägt —
# eine Klasse dort würde die Erkennung verändern. Für die Lautheit zählt aber, dass hier
# gesungen oder gespielt wird: Musik bei −16 LUFS klingt neben einem Chorlied zu leise.
MUSIK_OHNE_KLANGKLASSE = frozenset({"Gebetslied", "Musikstück", "Kinderlied"})


def klasse_fuer(klang_klasse: "str | None", beitrag: "str | None" = None) -> str:
    """„musik“ oder „sprache“ — was für die Lautheit zählt.

    Ohne Klangklasse gilt Sprache, außer die Beitragsart ist als Musik bekannt.
    """
    if klang_klasse in ("musik", "sprache"):
        return klang_klasse
    return "musik" if beitrag in MUSIK_OHNE_KLANGKLASSE else "sprache"


def ziel_lufs(klang_klasse: "str | None" = None, beitrag: "str | None" = None) -> "float | None":
    """Die Ziellautheit je Klangklasse, wenn auf Lautheit normalisiert wird — sonst None
    (Spitze). Ohne Angaben gilt das Sprachziel: ein ganzer Gottesdienst ist überwiegend
    gesprochen."""
    if EXPORT["normalisierung"] != "Lautheit":
        return None
    return EXPORT["lufs_" + klasse_fuer(klang_klasse, beitrag)]


def export_setzen(cfg: dict) -> dict:
    """Export-Werte übernehmen. Gibt zurück, was tatsächlich gesetzt wurde."""
    uebernommen = {}
    if cfg.get("export_bitrate"):
        EXPORT["bitrate"] = str(cfg["export_bitrate"])
        uebernommen["bitrate"] = EXPORT["bitrate"]
    if cfg.get("export_peak_dbfs") is not None:
        EXPORT["peak_dbfs"] = float(cfg["export_peak_dbfs"])
        uebernommen["peak_dbfs"] = EXPORT["peak_dbfs"]
    if cfg.get("export_fade_ms") is not None:
        EXPORT["fade_ms"] = int(cfg["export_fade_ms"])
        uebernommen["fade_ms"] = EXPORT["fade_ms"]
    if cfg.get("export_normalisierung") in ("Spitzenpegel", "Lautheit"):
        EXPORT["normalisierung"] = cfg["export_normalisierung"]
        uebernommen["normalisierung"] = EXPORT["normalisierung"]
    # Früher gab es ein Ziel für alles. Schickt jemand nur das alte, gilt es für beide.
    alt = cfg.get("export_lufs")
    for klasse in ("sprache", "musik"):
        wert = cfg.get(f"export_lufs_{klasse}")
        if wert is None:
            wert = alt
        if wert is not None:
            EXPORT[f"lufs_{klasse}"] = float(wert)
            uebernommen[f"lufs_{klasse}"] = EXPORT[f"lufs_{klasse}"]
    return uebernommen
