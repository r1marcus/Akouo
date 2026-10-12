# Akouo

**Continuous service recording, live transcription and automatic segment detection for a church
sound booth — self-hosted on a single Mac.**

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Apple%20Silicon-lightgrey)
![Python](https://img.shields.io/badge/python-3.12-blue)

> **Language note.** The user interface and the operating documentation are **German** — that is
> the language of the congregation this was built for, and the service structure it models
> (*Gebetsstunde*, *Vermeldungen*, *Erntedankfest*) does not translate cleanly. This README and
> [`ARCHITECTURE.md`](ARCHITECTURE.md) are in English; everything under [`docs/`](docs/) is German.
>
> *Akouo* — from the Greek ἀκούω, "I hear".

---

## What this is

Recording a church service the usual way means one person riding the Start and Stop buttons for
two hours: start before the choir sings, stop after, name the file, repeat — twenty times, while
also running the mix, the livestream and the checklist.

Akouo turns that around. **It records the whole service in one pass** and works out afterwards
where the individual items were. A continuous WAV is cut into per-item MP3s at the quietest point
between them. While recording, the audio is transcribed locally, and the announcements in that
transcript tell the system what is coming next. The operator watches, corrects what is wrong, and
never touches a Start button.

It runs entirely on one Mac in the building. No cloud, no API keys, no internet required.

![The dashboard during a service: the service type in the header, checklist progress, the
recording clock and a level meter that runs whether or not a recording is
going](docs/bilder/dashboard.png)

<sup>The live system, five minutes before a Sunday evening service. The level meter runs
continuously — not only while recording — because the operator needs to see signal before
pressing anything.</sup>

## Status — honest version

This is **one congregation's working system**, in weekly use since September 2026, published in
case it is useful to someone else. It is not a product.

What is proven on real services (measured, not estimated — details in
[`docs/betrieb.md`](docs/betrieb.md)):

- Speech vs. music: **26 of 27** items correct, settled within 3 seconds
- Coarse item boundaries: **15 of 15** on a harvest festival service
- Orchestra detection via spectral flatness: instrumental 0.14–0.17 vs. sung 0.27–0.37
- Items derived from spoken announcements: **17 detected for 17 programme points**, 14 in the
  right order

What the automation **cannot** do:

- Tell a choir piece from a congregational group song — acoustically they are the same thing
- Tell a poem from a prayer reliably — it counts direct address to God, which is a proxy
- Generate trustworthy titles — of six, three were right. Titles are display-only and deliberately
  never reach a marker or a filename, because a wrong type is visible in a dropdown while a wrong
  title disappears silently into the archive

And the honest operational caveats:

- **There is no authentication.** Anyone on the network can open the dashboard, stop the recording
  and delete markers. See [Security](#security).
- One deployment, one maintainer. Expect to adapt things.
- `audio-service/classifier.py` carries thresholds measured in **this** room with **this** console.
  They are a starting point, not a constant of nature.

## What it does

| | |
|---|---|
| **Continuous recording** | One open audio stream for the whole service. Separate channel pairs for the recording and the livestream mix, so the stream can carry a different balance. |
| **Live transcript** | Local whisper.cpp, roughly 10 s behind, shown in the dashboard. |
| **Automatic segment detection** | Boundaries at ≥8 s of quiet, item type from five acoustic features plus the announcements in the transcript. Everything is a suggestion the operator can override. |
| **Per-item MP3 export** | 192 kbit/s, normalised to −3 dBFS, 1 s fades, cut at the quietest point. The session folder is self-explanatory: `marker.json` and `ablauf.txt` next to the audio. |
| **Local AI summaries** | Per segment via Ollama: summary, keywords, Bible references — each reference checked back against the transcript to catch hallucinations. |
| **Checklists** | Per service type, tickable from the tablet, so the pre-service routine is not in someone's head. |
| **Livestream** | Icecast or RTMP via FFmpeg, plus a local monitor bus without the Icecast delay. |
| **NAS upload** | Segments pushed to an SMB share, never overwriting what is already there. |

## What it looks like

The interface is built for a tablet propped up next to a mixing console — large targets, no
hover, nothing that needs a second hand. It is in German, because the people using it are.

![Choosing the service: four large tiles for the regular services, the rest behind "Weitere", and
an optional free-text theme field](docs/bilder/gottesdienst-waehlen.png)

**Choosing the service.** Four tiles for the ones that happen every week, the rest folded away
under *Weitere*. The choice drives the checklist, the folder layout on the NAS, and what the
automatic detection expects to hear. `Thema` is free text — *Erntedankfest*, a baptism, an
anniversary — and ends up in the folder name and the MP3 tag.

![The settings page: export, detection and cut-edge values, each with a sentence explaining what
it does and what happens if you get it wrong](docs/bilder/einstellungen.png)

**Settings.** Nineteen values across five groups, each with a sentence saying what it changes —
and, where a number was measured rather than guessed, what the measurement was. *"At 5 s, pauses
in speech cut the sermon apart; at 15 s, consecutive items melt together."* Item types,
announcement rules and the NAS folder template are editable on the same page.

<sup>Both shots come from a fresh install with an empty database, which is why the audio service
reports as offline.</sup>

## Requirements

Be warned: the audio path has a **commercially licensed** component.

| | Required? | Note |
|---|---|---|
| Mac with Apple Silicon, always on | **yes** | Developed and running on a Mac Studio, M4 Max / 36 GB. macOS 15+. |
| **Python 3.12 exactly** | **yes** | Not 3.13+: `pydub` imports `audioop`, removed from the stdlib in 3.13. Homebrew's default is now newer — install `python@3.12` explicitly. |
| FFmpeg | **yes** | `brew install ffmpeg`. Not bundled — see [`NOTICE`](NOTICE). |
| Audio input, ≥2 channels | **yes** | Default config uses one stereo pair for both recording and stream. |
| **Dante Virtual Soundcard** | for the full setup | **A commercial licence from Audinate.** Needed only if your console feeds audio over Dante, as here (recording on channels 44/45, stream on 46/47). A multi-channel USB interface works instead — Focusrite is matched by default. |
| Mixing console with two separate mixes | recommended | Otherwise recording and stream carry the same balance. |
| whisper.cpp + a ggml model | optional | ~1.5 GB. Without it the transcript stays empty and the announcement-based detection goes quiet; recording is unaffected. |
| Ollama + a model | optional | `qwen2.5:14b-instruct` is ~9 GB and wants 16 GB+ of RAM. Without it there are no AI summaries, and nothing crashes. |
| Icecast 2 server | optional | For the livestream. |
| SMB share | optional | For the upload. Without it exports stay local, reported as `skipped`. |
| Tablet on the same network | recommended | The dashboard is built for touch. |

The system starts and stays usable when every optional component is missing — each one degrades to
a clear status message rather than an error.

**Not built, despite what older notes may suggest:** Shelly device control and SIP/DTMF dial-in.
Both appear in historical planning documents; there is no code for either.

## Quickstart — dashboard only, no audio hardware

This gets you a running dashboard to look at in about two minutes. No Dante, no interface.

```bash
git clone https://github.com/r1marcus/Akouo.git && cd Akouo
python3.12 -m venv .venv
.venv/bin/pip install -r api/requirements.txt
mkdir -p data && .venv/bin/alembic upgrade head
.venv/bin/uvicorn main:app --app-dir api --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The dashboard works; the audio card reports the service as offline,
which is correct — you have not started it.

For the real thing — audio, autostart, transcription, stream, NAS — see
**[`docs/betrieb.md`](docs/betrieb.md)** (German). Be aware that the operating documentation was
written for one specific machine; adapting it to yours is the actual work.

## How it works

```
 Console ──(Dante / USB)──┐
                          ▼
          ┌───────────────────────────────┐
          │ audio-service  :8001          │   native, launchd — not Docker
          │ one open CoreAudio stream     │
          │  ├─ recording pair → WAV      │
          │  ├─ stream pair → FFmpeg ─────┼──→ Icecast / RTMP
          │  ├─ monitor pair → FFmpeg ────┼──→ local monitor bus
          │  └─ level meter + transcript ─┼──→ whisper.cpp :8090
          └───────────────┬───────────────┘
                          │ HTTP
          ┌───────────────▼───────────────┐
          │ api  :8000                    │   FastAPI + Jinja2 + HTMX + Alpine
          │ dashboard, checklists,        │
          │ markers, export, archive      │──→ Ollama :11434
          │ SQLite + Alembic              │──→ SMB share
          └───────────────────────────────┘
                          ▲
                   tablet / browser
```

**The one thing to understand:** a single CoreAudio stream feeds recording, level meter,
livestream and transcription at once. A second process opening the same device causes dropouts —
this is why the audio service is a separate, native process that owns the device, and why it does
not run in Docker (USB audio passthrough on macOS is broken). Details and the reasoning behind the
other decisions are in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Privacy and data protection

Read this before running it anywhere.

Akouo records **entire church services**, transcribes them, and has a local language model produce
summaries, keywords and speaker attributions. Under the GDPR, religious belief is a **special
category** of personal data (Art. 9). The people being recorded are congregation members and
visitors, not employees, and they have not consented to anything by sitting down.

What the system produces per service: a full WAV, `transcript.txt` and `transcript.jsonl`, the
exported MP3s, `marker.json`, `ablauf.txt`, and database rows holding themes, summaries and Bible
references. **All of it stays on the machine** — nothing is sent anywhere, and the AI runs locally.
That is a genuine advantage, and it does not by itself make the processing lawful.

As an operator you need to settle, for your own situation: a lawful basis, how you inform the
congregation, how long recordings are kept, who can access them, and how deletion works. If you
livestream singing, GEMA licensing or your church's blanket agreement applies to the music.

This project gives you no legal advice and takes no responsibility for your deployment.

## Security

- **No authentication, no TLS.** Anyone who can reach port 8000 has full control: stop the
  recording, start the stream, delete markers.
- Built for a trusted LAN. **Do not expose it to the internet.** If you need remote access, put it
  behind a VPN or a mesh network, not a port forward.
- Continuous operation on macOS wants sleep disabled and auto-login on, which weakens the host.
  Treat the machine as part of your attack surface.
- Give the NAS service account access to exactly one share. Nothing more.

Found a vulnerability? See [`SECURITY.md`](SECURITY.md).

## Documentation

| | |
|---|---|
| [`docs/betrieb.md`](docs/betrieb.md) | **German.** Operating manual: services, configuration, operation, levels, transcription, automation, troubleshooting. The most useful file here. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | English. How the pieces fit and why these decisions. |
| [`docs/stand.md`](docs/stand.md) | German. What works, what is known-broken, what is missing. |
| [`docs/roadmap.md`](docs/roadmap.md) | German. What is next, ordered by usefulness. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to work on this, including what you can do without the hardware. |

## Contributing

Patches welcome, with one caveat worth stating up front: **you probably cannot run this locally.**
See [`CONTRIBUTING.md`](CONTRIBUTING.md) for what is possible without a Dante licence and a mixing
console — tests, documentation, the classifier rules and the UI all are.

## License

[Apache License 2.0](LICENSE). Third-party components and trademark notes: [`NOTICE`](NOTICE) and
[`THIRD-PARTY-LICENSES.md`](THIRD-PARTY-LICENSES.md).

## Acknowledgements

Built on [FastAPI](https://fastapi.tiangolo.com/), [htmx](https://htmx.org/),
[Alpine.js](https://alpinejs.dev/), [Tailwind CSS](https://tailwindcss.com/),
[whisper.cpp](https://github.com/ggerganov/whisper.cpp), [Ollama](https://ollama.com/),
[sounddevice](https://python-sounddevice.readthedocs.io/) and [FFmpeg](https://ffmpeg.org/).
