# Contributing to Akouo

Thanks for looking. One thing up front, because it saves everyone time.

## You probably cannot run this locally

The full system needs a Mac, a mixing console with two separate mixes, and either a Dante Virtual
Soundcard licence or a multi-channel USB interface. That is a real wall, and it is not going away.

**What you can work on without any of that:**

- **The test suite.** 112 tests, all hardware mocked. No audio interface, no NAS, no Whisper, no
  Ollama needed.
- **The dashboard, API and templates.** The API starts on its own — see the Quickstart in the
  README. You get a working dashboard with the audio service reported as offline, which is the
  correct behaviour.
- **The classifier rules** in `audio-service/classifier.py`. Pure functions over level envelopes
  and transcript text. This is where the detection quality actually lives.
- **Documentation**, including the parts of `docs/` that are written for one specific machine and
  should not be.
- **Migrations and the data model.** `alembic upgrade head` builds a full schema from nothing.

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt    # both services plus pytest and ruff
mkdir -p data && .venv/bin/alembic upgrade head
```

**Python 3.12 exactly.** `pydub` imports `audioop`, which was removed from the standard library in
3.13. Newer versions fail immediately on importing the audio service.

## Running the tests

```bash
make test
```

Or the two groups by hand. They are run **separately on purpose**: `api/main.py` and
`audio-service/main.py` are both top-level modules named `main`, so in a single process they
shadow each other and the API tests end up talking to the audio app. Until the directories are
renamed to proper Python packages, plain `pytest` with no arguments does not work.

```bash
make test-api
make test-audio
```

Tests write only to temporary directories. If you find one touching `~/Recordings`, that is a bug
— one used to, and it was fixed by setting `RECORDINGS_DIR` in the fixture.

## Linting

```bash
make lint
```

The ruff configuration in `pyproject.toml` is deliberately narrow — real errors only. The full
default rule set reports several hundred findings in the existing code; cleaning those up is a
separate piece of work and not something a feature PR should carry.

## Rebuilding the CSS

`api/static/app.css` is a committed build artefact. **Templates are hot** — Jinja re-reads them per
request — but new Tailwind classes only take effect after a rebuild:

```bash
# one-off: fetch the standalone binary matching your architecture from
# https://github.com/tailwindlabs/tailwindcss/releases  (it is gitignored, ~76 MB)
./tailwindcss-macos-arm64 -i api/static/input.css -o api/static/app.css --minify
```

Forget this and a new class silently does nothing.

## Conventions

**Language.** The split is not arbitrary:

- **Domain terms stay German, in code too.** A *Gottesdienst* is not a "service", an *Ansage* is
  not an "announcement". German identifiers are wanted where the term comes from church life:
  `Programm`, `Erwartung`, `klassifiziere_bereich()`, `lies_ansage()`, `thema`.
- **Technical infrastructure stays English:** `recorder`, `transcriber`, `exporter`, `nas_client`,
  `boundaries`, HTTP routes, framework hooks.
- Rule of thumb: would the sound engineer use the word? German. Is it in the FastAPI or PortAudio
  manual? English.
- **Comments and docstrings: German.** They explain *why*, and the why comes from the service.
- **Anything a user reads is German** — UI text, toasts, and explicitly `HTTPException(detail=...)`
  and `{"message": ...}`, because those strings go straight into a toast via
  `api/templates/dashboard.html`.
- **Issues and pull requests: German or English, whichever you prefer.**

**Commits.** Conventional Commits with an English prefix and a German subject:

```
feat(automodus): Typ live aus Ansagen und Klang ableiten
fix(export): Segment bis zum Dateiende schneiden statt abzulehnen
```

**Error handling.** No bare `except:`, and no `except Exception` for control flow — catch
specifically (`httpx.ConnectError`, `OSError`, `queue.Empty`). `except ...: pass` only in cleanup
paths such as closing a stream or deleting a temp file.

**Logging.** `logging`, not `print()` — except in `audio-service/scripts/`, which are CLI tools
where `print` is correct.

**Machine-specific values** belong in `.env` or in a `*_PLACEHOLDER` in a plist, never in code. No
absolute paths, no IP addresses, no hardcoded device names.

## Before you open a pull request

- `make test` passes
- `make lint` passes
- No secrets, no internal hostnames or IP addresses, no real names in code, tests or docs. This
  repository has had all of those; please do not add more.
- `data/*.db` stays out of version control. It holds real service recordings and personal data.
