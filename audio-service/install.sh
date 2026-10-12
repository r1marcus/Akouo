#!/usr/bin/env bash
set -euo pipefail

# Akouo Audio-Dienst — Autostart einrichten (LaunchAgent de.akouo.audio, Port 8001).
#
# Einmal auf dem Rechner ausfuehren, der aufnimmt. Die uebrigen Dienste — Web-API,
# whisper-server, Dashboard — richtet autostart/install.sh ein.
#
# LaunchAgent und nicht LaunchDaemon: fuer den Zugriff auf das Audiogeraet braucht
# der Dienst eine angemeldete Benutzersitzung.
#
# Umstellbar ueber Umgebungsvariablen:
#   AKOUO_AUDIO_VENV   Pfad zum venv des Dienstes. Ohne Angabe wird ein vorhandenes
#                      venv benutzt, sonst ~/akouo-audio-venv angenommen.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/Akouo"
LABEL="de.akouo.audio"
PLIST_SRC="$SCRIPT_DIR/$LABEL.plist"
PLIST_DEST="$AGENTS/$LABEL.plist"

echo "=== Akouo Audio-Dienst — Installer ==="

# ─── 0. venv finden ─────────────────────────────────────────────────────────
# Ein vor der Umbenennung eingerichteter Rechner hat ~/tontechnik-audio-venv. Das
# venv wird bewusst NICHT umbenannt: darin stecken die uebersetzten Rad-Pakete von
# sounddevice und numpy, und ein Umzug waere ein Neubau ohne Gewinn.
if [ -n "${AKOUO_AUDIO_VENV:-}" ]; then
    VENV_DIR="$AKOUO_AUDIO_VENV"
elif [ -x "$HOME/akouo-audio-venv/bin/python" ]; then
    VENV_DIR="$HOME/akouo-audio-venv"
elif [ -x "$HOME/tontechnik-audio-venv/bin/python" ]; then
    VENV_DIR="$HOME/tontechnik-audio-venv"
    echo "[VENV] vorhandenes venv aus der Tontechnik-Zeit benutzt: $VENV_DIR"
else
    VENV_DIR="$HOME/akouo-audio-venv"
fi
VENV_PYTHON="$VENV_DIR/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then
    echo "[VENV] FEHLER: kein Python in $VENV_DIR"
    echo "       Anlegen mit (Python 3.12 genau — nicht neuer, siehe README):"
    echo "         python3.12 -m venv $VENV_DIR"
    echo "         $VENV_DIR/bin/pip install -r $SCRIPT_DIR/requirements.txt"
    exit 1
fi

PY_VERSION="$("$VENV_PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [ "$PY_VERSION" != "3.12" ]; then
    echo "[VENV] FEHLER: $VENV_PYTHON ist Python $PY_VERSION, gebraucht wird 3.12."
    echo "       pydub importiert audioop, das in 3.13 aus der Standardbibliothek"
    echo "       entfernt wurde. Der Dienst startet, der Export stirbt."
    exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[FFMPEG] FEHLER: ffmpeg nicht gefunden. brew install ffmpeg"
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "[ENV]  WARNUNG: $SCRIPT_DIR/.env fehlt."
    echo "       Vorlage: cp $SCRIPT_DIR/.env.example $SCRIPT_DIR/.env"
    echo "       Ohne eigene Werte laeuft der Dienst mit den Voreinstellungen —"
    echo "       Kanalpaare und Aufnahmeordner stimmen dann vermutlich nicht."
fi

mkdir -p "$AGENTS" "$LOG_DIR"

# ─── 1. Vorgaenger-Labels entladen ──────────────────────────────────────────
# Zwei Dienste auf demselben CoreAudio-Eingang gehen nicht gut: der zweite Stream
# desynchronisiert, und im schlechtesten Fall bleibt die Aufnahme still. Deshalb
# muss das alte Label weg, BEVOR das neue geladen wird.
for ALT in com.tontechnik.audio de.tontechnik.streaming; do
    if launchctl print "gui/$(id -u)/$ALT" >/dev/null 2>&1; then
        echo "[MIGRATION] alten Dienst $ALT entladen"
        launchctl bootout "gui/$(id -u)/$ALT" 2>/dev/null || true
    fi
    rm -f "$AGENTS/$ALT.plist"
done

# ─── 2. Plist schreiben und laden ───────────────────────────────────────────
sed -e "s|AUDIO_SERVICE_DIR_PLACEHOLDER|$SCRIPT_DIR|g" \
    -e "s|VENV_PYTHON_PLACEHOLDER|$VENV_PYTHON|g" \
    -e "s|LOG_DIR_PLACEHOLDER|$LOG_DIR|g" \
    "$PLIST_SRC" > "$PLIST_DEST"
plutil -lint "$PLIST_DEST" >/dev/null
echo "[AUDIO] Plist: $PLIST_DEST"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"

# ─── 3. Nachsehen, ob er wirklich laeuft ────────────────────────────────────
# Ein Dienst, den KeepAlive in einer Neustartschleife haelt, steht auch in
# "launchctl list". Deshalb wird der Port gefragt, nicht die Liste.
for _ in $(seq 1 15); do
    if curl -sf -o /dev/null http://127.0.0.1:8001/api/session/status; then
        echo "[AUDIO] laeuft und antwortet auf Port 8001"
        break
    fi
    sleep 1
done
if ! curl -sf -o /dev/null http://127.0.0.1:8001/api/session/status; then
    echo "[AUDIO] WARNUNG: keine Antwort auf Port 8001 — $LOG_DIR/audio-error.log ansehen."
fi

echo
echo "=== Fertig ==="
echo "Logs: $LOG_DIR/audio.log, $LOG_DIR/audio-error.log"
echo
echo "Noch von Hand zu erledigen (laesst sich nicht skripten):"
echo "  1. Mikrofonrecht fuer $VENV_PYTHON erteilen —"
echo "     Systemeinstellungen > Datenschutz & Sicherheit > Mikrofon."
echo "     Der Dialog erscheint beim ersten Aufnahmeversuch. Ohne das Recht ist"
echo "     die Aufnahme STILL, ohne Fehlermeldung."
echo "  2. Routing in der Dante-Oberflaeche (oder am Interface) pruefen —"
echo "     ohne anliegendes Signal zeigt der Pegel null und die Erkennung schweigt."
