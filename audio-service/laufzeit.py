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
}


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
    return uebernommen
