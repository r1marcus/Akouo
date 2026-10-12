# Akouo — gaengige Entwicklungsaufgaben.
#
# Die Tests laufen in ZWEI Prozessen, nicht aus Bequemlichkeit: api/main.py und
# audio-service/main.py heissen beide "main" und verdecken sich in einem Prozess
# gegenseitig. Siehe CONTRIBUTING.md.

PY        ?= .venv/bin/python
# Das venv des Audio-Dienstes liegt ausserhalb des Repositorys. Ein Rechner aus der
# Tontechnik-Zeit hat ~/tontechnik-audio-venv, eine frische Installation
# ~/akouo-audio-venv — beides wird akzeptiert, umstellbar ueber AUDIO_PY.
AUDIO_PY  ?= $(firstword $(wildcard $(HOME)/akouo-audio-venv/bin/python \
                                    $(HOME)/tontechnik-audio-venv/bin/python))
# AUDIO_SERVICE_URL zeigt bewusst auf den Discard-Port: die Lifespan der API schiebt
# beim Start die Erkennungskonfiguration zum Audio-Dienst. Ohne diese Umleitung würde
# ein Testlauf den PRODUKTIV laufenden Dienst umkonfigurieren.
TESTENV   := DATABASE_URL=sqlite+aiosqlite:///:memory: AUDIO_SERVICE_URL=http://127.0.0.1:9

API_TESTS := tests/test_archive.py tests/test_current_service.py tests/test_database.py \
             tests/test_segments_api.py tests/test_service_types.py tests/test_session_api.py \
             tests/test_settings_api.py tests/test_uploads_api.py tests/test_ui_phase5.py \
             tests/test_typen.py tests/test_konfiguration_api.py tests/test_nas_pfad.py \
             tests/test_sprache.py tests/test_vorlagen.py

AUDIO_TESTS := tests/test_boundaries.py tests/test_continuous_recording.py tests/test_insights.py \
               tests/test_mp3_export.py tests/test_nas_client.py tests/test_nas_push.py \
               tests/test_recording.py tests/test_segment_export.py tests/test_stream_adapter.py \
               tests/test_ui_phase5.py::test_waveform tests/test_erkennung_uebernahme.py

.PHONY: help test test-api test-audio lint css db serve

help:
	@echo "make test        beide Testgruppen"
	@echo "make test-api    nur die API-Tests"
	@echo "make test-audio  nur die Audio-Service-Tests"
	@echo "make lint        ruff"
	@echo "make css         Tailwind neu bauen"
	@echo "make db          leere Datenbank auf den aktuellen Stand bringen"
	@echo "make serve       API allein starten (ohne Audio-Dienst)"

test: test-api test-audio

test-api:
	@echo "── API ──"
	@$(TESTENV) $(PY) -m pytest -q $(API_TESTS) --deselect tests/test_ui_phase5.py::test_waveform

test-audio:
	@test -n "$(AUDIO_PY)" || { \
	  echo "Kein venv des Audio-Dienstes gefunden. Anlegen mit:"; \
	  echo "  python3.12 -m venv ~/akouo-audio-venv"; \
	  echo "  ~/akouo-audio-venv/bin/pip install -r audio-service/requirements.txt"; \
	  exit 1; }
	@echo "── Audio-Service ──"
	@$(TESTENV) TRANSCRIBE_ENABLED=0 $(AUDIO_PY) -m pytest -q $(AUDIO_TESTS)

lint:
	@$(PY) -m ruff check api audio-service tests conftest.py migrations

css:
	@test -x ./tailwindcss-macos-arm64 || { \
	  echo "tailwindcss-macos-arm64 fehlt. Standalone-Binary passend zur Architektur von"; \
	  echo "https://github.com/tailwindlabs/tailwindcss/releases holen (~76 MB, gitignored)."; \
	  exit 1; }
	./tailwindcss-macos-arm64 -i api/static/input.css -o api/static/app.css --minify

db:
	@test -z "$(wildcard data/tontechnik.db)" || { \
	  echo "data/tontechnik.db liegt schon da. 'make db' wuerde daneben eine leere"; \
	  echo "data/akouo.db anlegen, und autostart/install.sh faende dann zwei."; \
	  echo "Vorhandene migrieren:"; \
	  echo "  DATABASE_URL=\"sqlite+aiosqlite:///$$PWD/data/tontechnik.db\" $(PY) -m alembic upgrade head"; \
	  exit 1; }
	@mkdir -p data
	@$(PY) -m alembic upgrade head

serve:
	@$(PY) -m uvicorn main:app --app-dir api --host 127.0.0.1 --port 8000
