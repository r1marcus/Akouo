# Lizenzen Dritter

Akouo steht unter der Apache License 2.0 (siehe `LICENSE`). Diese Datei listet die
Software Dritter, auf der es aufbaut. Marken-, FFmpeg- und Modell-Hinweise stehen
in `NOTICE`.

## Im Repository mitgelieferte Dateien

| Datei | Projekt | Version | Lizenz |
|---|---|---|---|
| `api/static/alpine.min.js` | Alpine.js | 3.15.9 | MIT |
| `api/static/htmx.min.js` | htmx | 2.0.7 | 0BSD |
| `api/static/app.css` | Tailwind CSS (Build-Ergebnis) | 4.2.2 | MIT |

Alle drei tragen ihren Lizenzhinweis als Kommentar in der ersten Zeile.

### MIT License — Alpine.js

```
Copyright (c) 2019-2025 Caleb Porzio and contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in the
Software without restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the
Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

Tailwind CSS steht unter derselben MIT-Lizenz (Copyright (c) Tailwind Labs, Inc.);
Teile des Preflight leiten sich von `modern-normalize` ab (MIT, Sindre Sorhus).

### Zero-Clause BSD — htmx

```
Copyright (c) Big Sky Software

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.
```

0BSD verzichtet ausdrücklich auf die Pflicht, den Lizenzhinweis weiterzugeben. Der
Hinweis steht hier trotzdem.

## Python-Abhängigkeiten

Diese Pakete werden **nicht** mitgeliefert, sondern beim Einrichten von PyPI
installiert. Die Liste ist aus den installierten Paketen erzeugt und umfasst
Laufzeit- und Entwicklungsabhängigkeiten beider Dienste.

Nennenswert: **certifi** steht unter der MPL-2.0 und ist damit die einzige
Abhängigkeit mit (schwachem, dateibasiertem) Copyleft. Sie wird unverändert
verwendet, wodurch keine weiteren Pflichten entstehen. Es gibt **keine** GPL-,
AGPL- oder LGPL-Abhängigkeit.

| Paket | Version | Lizenz |
|---|---|---|
| agent-detector | 2.0.0 | MIT |
| aiosqlite | 0.22.1 | MIT License |
| alembic | 1.16.5 | MIT |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| certifi | 2026.7.22 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| click | 8.5.0 | BSD-3-Clause |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause |
| detect-installer | 0.2.1 | 0BSD |
| dnspython | 2.8.0 | ISC |
| email-validator | 2.3.0 | Unlicense |
| fastapi | 0.128.8 | MIT |
| fastapi-cli | 0.0.32 | MIT |
| fastapi-cloud-cli | 0.26.0 | MIT |
| fastar | 0.12.0 | MIT |
| greenlet | 3.5.5 | MIT AND PSF-2.0 |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httptools | 0.8.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.19 | BSD-3-Clause |
| iniconfig | 2.3.0 | MIT |
| Jinja2 | 3.1.6 | BSD License |
| Mako | 1.4.1 | MIT |
| markdown-it-py | 4.2.0 | MIT License |
| MarkupSafe | 3.0.3 | BSD-3-Clause |
| mdurl | 0.1.2 | MIT License |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.6.0 | MIT |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.5 | MIT |
| pydantic-extra-types | 2.11.1 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| pydantic_core | 2.46.5 | MIT |
| pydub | 0.25.1 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pyspnego | 0.12.2 | MIT |
| pytest | 9.0.2 | MIT |
| pytest-asyncio | 1.3.0 | Apache-2.0 |
| python-dotenv | 1.0.1 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| PyYAML | 6.0.3 | MIT |
| rich | 15.0.0 | MIT |
| rich-toolkit | 0.20.5 | MIT |
| rignore | 0.8.1 | MIT |
| sentry-sdk | 2.69.2 | MIT |
| shellingham | 1.5.4 | ISC License |
| smbprotocol | 1.16.1 | MIT |
| sounddevice | 0.5.5 | MIT |
| SQLAlchemy | 2.0.48 | MIT |
| sse-starlette | 2.1.3 | BSD-3-Clause |
| starlette | 0.52.1 | BSD-3-Clause |
| typer | 0.27.2 | MIT |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| urllib3 | 2.7.0 | MIT |
| uvicorn | 0.53.0 | BSD-3-Clause |
| uvloop | 0.22.1 | MIT License |
| watchfiles | 1.2.0 | MIT |
| websockets | 17.1 | BSD-3-Clause |