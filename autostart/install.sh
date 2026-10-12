#!/usr/bin/env bash
set -euo pipefail

# Akouo — Autostart einrichten: Web-API, whisper-server, Dashboard.
#
# Der Audio-Dienst hat sein eigenes Skript: audio-service/install.sh
#
# Umstellbar ueber Umgebungsvariablen:
#   AKOUO_LISTEN_HOST   Standard 0.0.0.0 (im ganzen LAN erreichbar — es gibt KEINE
#                       Anmeldung, siehe SECURITY.md). 127.0.0.1 fuer nur lokal.
#   AKOUO_DB            Pfad zur SQLite-Datei. Ohne Angabe wird eine vorhandene
#                       Datenbank benutzt, sonst <repo>/data/akouo.db angelegt.
#   AKOUO_WHISPER_MODEL Pfad zum ggml-Modell. Ohne Angabe wird whisper uebersprungen.
#   AKOUO_SKIP_API      auf 1 setzen, um den API-Daemon nicht anzufassen (kein sudo).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/Akouo"

LISTEN_HOST="${AKOUO_LISTEN_HOST:-0.0.0.0}"
AUDIO_URL="${AKOUO_AUDIO_SERVICE_URL:-http://127.0.0.1:8001}"
WRAPPER="$HOME/run-akouo-api.sh"

# Die Datenbank wird NICHT geraten. Ein vor der Umbenennung eingerichteter Rechner
# hat data/tontechnik.db; zeigte der Dienst danach auf data/akouo.db, startete die
# API auf einer leeren Datenbank — ohne Fehler, nur ohne Aufnahmen. Deshalb gewinnt
# immer eine vorhandene Datei, und nur eine frische Installation legt akouo.db an.
if [ -n "${AKOUO_DB:-}" ]; then
    DB_PATH="$AKOUO_DB"
elif [ -f "$REPO_DIR/data/akouo.db" ]; then
    DB_PATH="$REPO_DIR/data/akouo.db"
elif [ -f "$REPO_DIR/data/tontechnik.db" ]; then
    DB_PATH="$REPO_DIR/data/tontechnik.db"
    echo "[DB]   vorhandene Datenbank aus der Tontechnik-Zeit benutzt: $DB_PATH"
else
    DB_PATH="$REPO_DIR/data/akouo.db"
fi

echo "=== Akouo Autostart-Installer ==="
echo "Repository: $REPO_DIR"
mkdir -p "$AGENTS" "$LOG_DIR"

# ─── 0. Vorgaenger-Labels entladen ──────────────────────────────────────────
# Das Projekt hiess vorher Tontechnik. Bleiben die alten Dienste geladen, laufen
# zwei whisper-Server auf Port 8090 und zwei Dashboard-Starter gleichzeitig.
for ALT in de.tontechnik.whisper de.tontechnik.dashboard; do
    if launchctl print "gui/$(id -u)/$ALT" >/dev/null 2>&1; then
        echo "[MIGRATION] alten Dienst $ALT entladen"
        launchctl bootout "gui/$(id -u)/$ALT" 2>/dev/null || true
    fi
    rm -f "$AGENTS/$ALT.plist"
done
if [ "${AKOUO_SKIP_API:-0}" != "1" ] && [ -f /Library/LaunchDaemons/de.tontechnik.api.plist ]; then
    echo "[MIGRATION] alten API-Daemon de.tontechnik.api entladen (sudo)"
    sudo launchctl bootout system/de.tontechnik.api 2>/dev/null || true
    sudo rm -f /Library/LaunchDaemons/de.tontechnik.api.plist
fi

# ─── 1. Web-API (LaunchDaemon, braucht sudo) ────────────────────────────────
if [ "${AKOUO_SKIP_API:-0}" = "1" ]; then
    echo "[API]  uebersprungen (AKOUO_SKIP_API=1)"
else
    if [ ! -x "$REPO_DIR/.venv/bin/uvicorn" ]; then
        echo "[API]  FEHLER: $REPO_DIR/.venv/bin/uvicorn fehlt."
        echo "       Anlegen mit:"
        echo "         python3.12 -m venv $REPO_DIR/.venv"
        echo "         $REPO_DIR/.venv/bin/pip install -r $REPO_DIR/api/requirements.txt"
        exit 1
    fi

    sed -e "s|DATABASE_URL_PLACEHOLDER|sqlite+aiosqlite:///$DB_PATH|g" \
        -e "s|AUDIO_SERVICE_URL_PLACEHOLDER|$AUDIO_URL|g" \
        -e "s|REPO_DIR_PLACEHOLDER|$REPO_DIR|g" \
        -e "s|LISTEN_HOST_PLACEHOLDER|$LISTEN_HOST|g" \
        "$SCRIPT_DIR/run-api.sh.template" > "$WRAPPER"
    chmod +x "$WRAPPER"
    echo "[API]  Startskript: $WRAPPER"
    echo "[API]  Datenbank:   $DB_PATH"

    TMP_PLIST="$(mktemp)"
    sed -e "s|RUN_AS_USER_PLACEHOLDER|$(id -un)|g" \
        -e "s|WRAPPER_PLACEHOLDER|$WRAPPER|g" \
        -e "s|REPO_DIR_PLACEHOLDER|$REPO_DIR|g" \
        -e "s|LOG_FILE_PLACEHOLDER|$LOG_DIR/api.log|g" \
        "$SCRIPT_DIR/de.akouo.api.plist" > "$TMP_PLIST"
    plutil -lint "$TMP_PLIST" >/dev/null

    echo "[API]  Installieren nach /Library/LaunchDaemons (sudo nötig)..."
    sudo cp "$TMP_PLIST" /Library/LaunchDaemons/de.akouo.api.plist
    sudo chown root:wheel /Library/LaunchDaemons/de.akouo.api.plist
    sudo chmod 644 /Library/LaunchDaemons/de.akouo.api.plist
    rm -f "$TMP_PLIST"
    sudo launchctl bootout system/de.akouo.api 2>/dev/null || true
    sudo launchctl bootstrap system /Library/LaunchDaemons/de.akouo.api.plist
    echo "[API]  geladen, lauscht auf $LISTEN_HOST:8000"
fi

# ─── 2. whisper-server ──────────────────────────────────────────────────────
if [ -z "${AKOUO_WHISPER_MODEL:-}" ]; then
    echo "[WHISPER] uebersprungen — AKOUO_WHISPER_MODEL nicht gesetzt."
    echo "          Einrichten mit:"
    echo "            brew install whisper-cpp"
    echo "            # Modell holen, z.B. ggml-large-v3-turbo.bin (~1,5 GB) von"
    echo "            # https://huggingface.co/ggerganov/whisper.cpp"
    echo "            AKOUO_WHISPER_MODEL=~/ai/models/ggml-large-v3-turbo.bin bash $0"
elif [ ! -f "$AKOUO_WHISPER_MODEL" ]; then
    echo "[WHISPER] FEHLER: Modell nicht gefunden: $AKOUO_WHISPER_MODEL"
    exit 1
else
    BREW_PREFIX="$(brew --prefix 2>/dev/null || echo /opt/homebrew)"
    if [ ! -x "$BREW_PREFIX/bin/whisper-server" ]; then
        echo "[WHISPER] FEHLER: $BREW_PREFIX/bin/whisper-server fehlt. brew install whisper-cpp"
        exit 1
    fi
    THREADS="$(sysctl -n hw.perflevel0.logicalcpu 2>/dev/null || sysctl -n hw.ncpu)"
    LANG_CODE="${TRANSCRIBE_LANGUAGE:-de}"
    sed -e "s|WHISPER_MODEL_PLACEHOLDER|$AKOUO_WHISPER_MODEL|g" \
        -e "s|WHISPER_BIN_PLACEHOLDER|$BREW_PREFIX/bin/whisper-server|g" \
        -e "s|WHISPER_THREADS_PLACEHOLDER|$THREADS|g" \
        -e "s|WHISPER_LANG_PLACEHOLDER|$LANG_CODE|g" \
        -e "s|LOG_DIR_PLACEHOLDER|$LOG_DIR|g" \
        "$SCRIPT_DIR/de.akouo.whisper.plist" > "$AGENTS/de.akouo.whisper.plist"
    plutil -lint "$AGENTS/de.akouo.whisper.plist" >/dev/null
    launchctl bootout "gui/$(id -u)/de.akouo.whisper" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$AGENTS/de.akouo.whisper.plist"
    echo "[WHISPER] geladen: Modell $(basename "$AKOUO_WHISPER_MODEL"), $THREADS Threads, Sprache $LANG_CODE"
fi

# ─── 3. Dashboard in Safari ─────────────────────────────────────────────────
sed -e "s|AUTOSTART_DIR_PLACEHOLDER|$SCRIPT_DIR|g" \
    -e "s|LOG_DIR_PLACEHOLDER|$LOG_DIR|g" \
    "$SCRIPT_DIR/de.akouo.dashboard.plist" > "$AGENTS/de.akouo.dashboard.plist"
plutil -lint "$AGENTS/de.akouo.dashboard.plist" >/dev/null
launchctl bootout "gui/$(id -u)/de.akouo.dashboard" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENTS/de.akouo.dashboard.plist"
echo "[DASHBOARD] geladen"

echo
echo "=== Fertig ==="
echo "Logs: $LOG_DIR"
echo
echo "Noch zu erledigen:"
echo "  1. Audio-Dienst einrichten — ohne ihn nimmt nichts auf:"
echo "       bash $REPO_DIR/audio-service/install.sh"
echo "  2. Ruhezustand abschalten:  sudo pmset -a sleep 0 disksleep 0 displaysleep 0 womp 1"
echo "  3. Automatische Anmeldung aktivieren, damit der Audio-LaunchAgent nach"
echo "     einem Stromausfall ohne Eingriff hochkommt (geht nur ohne FileVault)."
echo "  4. Ollama: brew install ollama && ollama pull qwen2.5:14b-instruct  (~9 GB)"
