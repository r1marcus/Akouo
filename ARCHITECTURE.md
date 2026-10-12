# Architecture

How Akouo is put together, and — more usefully — **why**. Several of these decisions look odd
until you know what went wrong without them.

The operating manual [`docs/betrieb.md`](docs/betrieb.md) (German) describes running the system.
This document is about the shape of it.

## Two processes, deliberately

```
 Console ──(Dante / USB)──┐
                          ▼
          ┌───────────────────────────────┐
          │ audio-service  :8001          │   native process, launchd
          │                               │
          │ ONE open CoreAudio stream     │
          │  ├─ recording pair → WAV      │
          │  ├─ stream pair → FFmpeg ─────┼──→ Icecast / RTMP
          │  ├─ monitor pair → FFmpeg ────┼──→ local monitor bus
          │  └─ RMS level + transcript ───┼──→ whisper.cpp :8090
          └───────────────┬───────────────┘
                          │ HTTP (AUDIO_SERVICE_URL)
          ┌───────────────▼───────────────┐
          │ api  :8000                    │   FastAPI, Jinja2, HTMX, Alpine
          │ dashboard, checklists,        │
          │ markers, export, archive      │──→ Ollama :11434
          │ SQLite (async) + Alembic      │──→ SMB share (smbprotocol)
          └───────────────────────────────┘
                          ▲
                   tablet / browser
```

The browser only ever talks to port 8000. The API proxies what it needs from the audio service —
including the live transcript — so there is one origin and one place to reason about.

### Why not one process

The audio service owns the audio device and must keep owning it for two hours without interruption.
The web API gets restarted when a template changes, a migration runs, or someone deploys. Coupling
them would mean every deployment interrupts a recording.

### Why not Docker

USB and CoreAudio device passthrough to containers on macOS is broken — a known, unresolved Docker
Desktop limitation. The audio service therefore runs natively under `launchd`.

The repository used to carry a `Dockerfile` and a `docker-compose.yml` describing a container
setup for the non-audio services. It was never used in production, drifted out of date, and
pointed newcomers at a second database location. Both files were removed; the history of the
private predecessor repository still has them.

## The single stream, and why it matters

**This is the one thing to understand about the system.** `audio-service/recorder.py` opens exactly
one `sounddevice.InputStream` for the entire service and fans the audio out from there:

- the configured recording channel pair is written to `aufnahme.wav`
- the stream channel pair is piped to FFmpeg for Icecast or RTMP
- the same pair feeds a second FFmpeg for the local monitor bus, which has none of Icecast's delay
- RMS values drive the dashboard level meter
- ten-second chunks go to whisper.cpp for the live transcript

A second process opening the same device desynchronises the streams and causes dropouts. This was
found the hard way: an earlier design had a separate streaming service with its own capture, and
two concurrent CoreAudio streams on one device drifted apart. That service was removed and its
job folded into the audio service.

### The callback is a realtime thread

`_audio_callback` runs on a PortAudio realtime thread with no event loop. The queues it feeds are
`queue.Queue`, **not** `asyncio.Queue` — the latter is not thread-safe from a foreign thread, and
`asyncio.Queue.put()` would raise outright. Working around that with `loop.call_soon_threadsafe()`
adds enough overhead to the callback to cause buffer overruns, which in a service recording means
audible clicks and gaps.

The rule in the callback is therefore: cheap NumPy slicing and `put_nowait()`, nothing else. No
I/O, no locks, no async, no logging. An `asyncio` drain loop on the main thread picks the chunks up.

A related detail: the input stream stays open even when nobody is streaming, because the level
meter needs it. Without a gate the stream queue would grow without bound, so
`threading.Event`s (`stream_enabled`, `monitor_enabled`) decide whether chunks are enqueued at all.

### Channel configuration

Channels are **1-based device channels**, set via `AUDIO_RECORD_CHANNELS` and
`AUDIO_STREAM_CHANNELS`. PortAudio always starts at channel 1, so reaching Dante channel 45 means
opening 45 channels and slicing — which is what `Config.continuous_channels` computes.

The default is the same pair twice (`1,2` / `1,2`) so that the system works with any stereo input.
Separate pairs are the interesting configuration — they let the livestream carry a different mix
from the archive recording — but requiring four channels by default locked out everyone who just
wanted to try it.

## Recording continuously, deciding later

The operator never presses Start per item. The recording runs for the whole service and **markers**
mark the boundaries between items.

This is what makes the automation tractable: because the audio is already captured, nothing has to
be decided in realtime. If the detector works out twenty seconds late that an item began, it still
records the correct start timestamp. Accuracy is not traded against latency.

Memory was the constraint that shaped this. Accumulating raw PCM for a 90-minute service in a
Python list is roughly 345 MB/hour at 48 kHz stereo int16, in a process that shares memory with a
Whisper model. Audio is therefore streamed to disk as it arrives rather than buffered to the end,
and segments are cut from the finished WAV afterwards.

## Detection: two independent sources

Item type comes from two places that fail differently, which is the point:

1. **Sound** — five acoustic features with a majority vote for speech vs. music; spectral flatness
   below 0.22 for instrumental. Reliable for sermons and orchestra, blind to the difference between
   a choir piece and a congregational group song.
2. **The spoken announcements**, read out of the live transcript and parsed by the local language
   model. Catches what sound cannot, but only covers items that were actually announced.

They complement each other almost exactly. On a measured harvest-festival afternoon, nine of
sixteen programme points were announced — and the seven that were not were all sermons and
orchestral pieces, the two things sound identifies confidently.

**Sermon and orchestra therefore act as anchors.** At an anchor, everything still outstanding
counts as done; between anchors, announced items are threaded in order. Without that anchoring, one
never-announced item shifted the whole rest of the service by one position.

Boundaries need ≥8 s of quiet and ≥45 s of audio to count as an item. Those numbers are measured,
not guessed: at 5 s, speaking pauses cut the sermon apart; at 15 s, consecutive items merge.

**Titles are display-only.** Of six generated, three were right, so they deliberately never reach a
marker or a filename. A wrong type is visible in a dropdown and gets corrected; a wrong title
travels silently into the archive. Bible references get a second pass that checks each one back
against the transcript, because the model invents plausible ones.

## Data

SQLite via SQLAlchemy's async session, schema versioned with Alembic (currently 13 migrations,
strictly linear). One operator will never hit SQLite's concurrency limits, and a single file is
exactly the right shape for an offline-first appliance.

`DATABASE_URL` is the single source of truth and takes precedence over `alembic.ini`. This was not
always the case, and when the two disagreed `alembic upgrade head` migrated a different file from
the one the application used — leaving the schema and the recorded version out of step.

Two caveats worth knowing:

- `api/main.py` also calls `Base.metadata.create_all` at startup. On a genuinely empty database
  that creates the tables but **no** `alembic_version` row, so a later `alembic upgrade head`
  fails. On a fresh install, run `alembic upgrade head` first.
- Schema changes while a service is running are a bad idea. SQLite allows one writer; Alembic's
  batch mode for SQLite copies tables and drops the original, and a concurrent write during that
  window can fail or be lost. Migrate with the services stopped.

Audio does **not** live in the database. A session folder holds everything about one service and is
designed to be readable without any of this software:

```
sessions/2026-09-18_Gebetsstunde_003/
    aufnahme.wav
    transcript.txt
    transcript.jsonl
    marker.json          complete and machine-readable
    ablauf.txt           the same thing, for a human to read
    exports/2026-09-18_Gebetsstunde_01_Predigt.mp3
```

That matters for an archive meant to outlive the software that produced it.

## Frontend

HTMX for server-driven interactions, Alpine.js for local state, Tailwind for styling, Jinja2 for
templates — and **no build step for JavaScript**. All three libraries are committed as single files
in `api/static/`.

The reasoning is maintenance, not taste: a three-person volunteer church tech team should not be
maintaining a JavaScript bundler. Templates are re-read per request, so a UI fix takes effect
immediately. The one build artefact is `api/static/app.css`, which needs the Tailwind standalone
binary — committed output, so a newcomer does not need it to start.

## Degradation

Every external dependency is optional at runtime, and this is tested behaviour rather than
aspiration:

| Missing | Result |
|---|---|
| Audio device | Dashboard shows "no input", a rescan runs every 3 s, recording start returns a clear 409 |
| Audio service | API returns 503 per endpoint; dashboard, archive and checklists stay usable |
| whisper.cpp | Transcript stays empty, `last_error` is set, recording unaffected |
| Ollama | "no model available" in the result, no crash |
| NAS | Unconfigured → `skipped`; configured but unreachable → `failed` per file, export still local |

The one gap: FFmpeg is invoked without a prior existence check, so a missing binary surfaces as an
unhandled `FileNotFoundError` and an HTTP 500 rather than a useful message.

## Known rough edges

Honest list, so nobody has to discover these by reading the code:

- `api/` and `audio-service/` both contain a top-level `main.py`. Because `audio-service` contains a
  hyphen it is not a valid Python package name, so both directories sit flat on `sys.path` and the
  two modules shadow each other. The test suite works around it with explicit `importlib` loading
  and has to run in two separate processes. The real fix is renaming to `audio_service/` with
  proper package imports; it is deferred because it also means touching the installed launchd
  plists.
- Service types, marker types and the NAS folder layout are hardcoded in Python — and the marker
  type list exists in two places. Another congregation has to patch code, not configuration.
- The classifier thresholds were measured in one room with one console.
- `logging.basicConfig()` is never called, so most modules use `print(..., flush=True)` and rely on
  launchd redirecting stdout to a file.
