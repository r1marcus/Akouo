#!/usr/bin/env python3
"""Prüft die NAS-Verbindung, bevor der Upload scharfgeschaltet wird.

    ~/akouo-audio-venv/bin/python audio-service/scripts/check_nas.py

Liest NAS_* aus audio-service/.env und geht denselben Weg wie der Export:
Anmeldung, Freigabe öffnen, Testdatei hochladen, wieder löschen. Das Passwort
wird nie ausgegeben — auch nicht in Fehlermeldungen.

Beendet sich mit 0, wenn alles geklappt hat, sonst mit 1.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent.parent   # audio-service/
sys.path.insert(0, str(HERE))

from dotenv import load_dotenv

load_dotenv(HERE / ".env")

from config import config          # noqa: E402  — muss nach load_dotenv kommen
from nas_client import NASClient   # noqa: E402


def mask(value: str) -> str:
    """Zeigt, ob etwas gesetzt ist, ohne es zu verraten."""
    return f"gesetzt ({len(value)} Zeichen)" if value else "LEER"


def main() -> int:
    print("NAS-Einstellungen aus audio-service/.env")
    print(f"  NAS_SERVER    {config.nas_server or 'LEER'}")
    print(f"  NAS_SHARE     {config.nas_share or 'LEER'}")
    print(f"  NAS_USER      {config.nas_user or 'LEER'}")
    print(f"  NAS_PASSWORD  {mask(config.nas_password)}")
    print(f"  NAS_BASE_PATH {config.nas_base_path or 'LEER'}")
    print()

    fehlend = [name for name, wert in (
        ("NAS_SERVER", config.nas_server),
        ("NAS_SHARE", config.nas_share),
        ("NAS_USER", config.nas_user),
        ("NAS_PASSWORD", config.nas_password),
    ) if not wert]
    if fehlend:
        print("Es fehlen noch: " + ", ".join(fehlend))
        return 1

    client = NASClient(config)
    stempel = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"_verbindungstest_{stempel}.txt"

    with tempfile.TemporaryDirectory() as tmp:
        lokal = pathlib.Path(tmp) / name
        lokal.write_text(
            "Verbindungstest der Tontechnik-Anlage.\n"
            f"Erstellt {datetime.now().isoformat(timespec='seconds')}.\n"
            "Diese Datei darf gelöscht werden.\n",
            encoding="utf-8",
        )
        print(f"Lade Testdatei hoch: {name}")
        ergebnis = client.upload_file(lokal, name)

    if ergebnis["status"] != "uploaded":
        print(f"FEHLGESCHLAGEN: {ergebnis['error']}")
        print()
        print("Häufige Ursachen:")
        print("  - Benutzername oder Passwort falsch")
        print("  - Freigabename stimmt nicht (Groß-/Kleinschreibung beachten)")
        print("  - Benutzer hat auf der Freigabe kein Schreibrecht")
        print("  - SMB auf der Synology abgeschaltet (Systemsteuerung > Dateidienste)")
        return 1

    print(f"Hochgeladen nach: {ergebnis['nas_path']}")

    # Aufräumen: der Test soll nichts zurücklassen.
    try:
        import smbclient

        smbclient.remove(ergebnis["nas_path"])
        print("Testdatei wieder entfernt.")
    except Exception as exc:  # noqa: BLE001
        print(f"Hinweis: Testdatei konnte nicht entfernt werden ({exc}).")
        print("Der Upload selbst hat funktioniert — bitte von Hand löschen.")

    print()
    print("Verbindung steht. Zum Scharfschalten den Audio-Dienst neu starten:")
    print("  launchctl kickstart -k gui/$(id -u)/de.akouo.audio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
