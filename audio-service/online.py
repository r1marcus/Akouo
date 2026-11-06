"""Online stellen: einen exportierten Track in die Gemeinde-App bringen.

Drei Schritte, in dieser Reihenfolge — erst die Dateien, dann der Eintrag, sonst zeigt
die App einen Beitrag, dessen Ton noch fehlt:

  1. MP3 in den Speicher der App (S3-kompatibel, Hetzner), unter dem `file_key`, den die
     API gebaut hat (api/online.py).
  2. Untertitel (WebVTT) daneben, aus dem Live-Transkript dieser Aufnahme — nicht neu
     transkribiert. Whisper hat ohnehin schon mitgeschrieben.
  3. Eintrag über den Sammelimport der App. Der ist über `file_key` idempotent: ein
     zweites Online-Stellen ergänzt nur, was fehlte.

Vorhandene Dateien im Speicher werden nie überschrieben, außer die API erlaubt es
ausdrücklich (`ersetzen`), weil sie den Track selbst schon einmal hochgeladen hat.
Zugangsdaten stehen in der .env (ONLINE_*) und erscheinen nie im Log.
"""
from __future__ import annotations

import json
import logging
import pathlib
from typing import Optional

import httpx

from config import config

log = logging.getLogger(__name__)

ZEITLIMIT_S = 60.0


def eingerichtet() -> bool:
    return all([config.online_s3_endpoint, config.online_s3_bucket, config.online_s3_key,
                config.online_s3_secret, config.online_api_url, config.online_api_email,
                config.online_api_passwort])


def _zeit(sekunden: float) -> str:
    sekunden = max(0.0, sekunden)
    h, rest = divmod(sekunden, 3600)
    m, s = divmod(rest, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def vtt_aus_transkript(transkript: pathlib.Path, von_s: float, bis_s: float) -> Optional[str]:
    """Die Zeilen des Live-Transkripts zwischen von und bis, auf den Track-Anfang verschoben."""
    if not transkript.exists():
        return None
    teile = ["WEBVTT", ""]
    try:
        with open(transkript, encoding="utf-8") as f:
            for roh in f:
                try:
                    z = json.loads(roh)
                except ValueError:
                    continue
                start, ende = float(z.get("start", 0)), float(z.get("end", z.get("start", 0)))
                text = (z.get("text") or "").strip()
                if not text or ende <= von_s or start >= bis_s:
                    continue
                a, e = max(start, von_s) - von_s, min(ende, bis_s) - von_s
                if e <= a:
                    continue
                teile += [f"{_zeit(a)} --> {_zeit(e)}", text, ""]
    except OSError:
        return None
    return "\n".join(teile) if len(teile) > 2 else None


def _s3():
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", endpoint_url=config.online_s3_endpoint,
        aws_access_key_id=config.online_s3_key, aws_secret_access_key=config.online_s3_secret,
        config=Config(signature_version="s3v4", retries={"max_attempts": 4}),
    )


def _vorhanden(s3, schluessel: str) -> Optional[int]:
    from botocore.exceptions import ClientError
    try:
        return s3.head_object(Bucket=config.online_s3_bucket, Key=schluessel)["ContentLength"]
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def _anmelden(client: httpx.Client) -> None:
    r = client.post("/api/auth/login/", json={"email": config.online_api_email,
                                              "password": config.online_api_passwort})
    r.raise_for_status()
    token = r.json().get("access")
    if not token:
        raise RuntimeError("Anmeldung an der Gemeinde-App ohne Token")
    client.headers["Authorization"] = f"Bearer {token}"


def stelle_online(mp3_path: pathlib.Path, eintrag: dict, wav_path: pathlib.Path,
                  von_s: float, bis_s: float, dauer_ms: Optional[int], ersetzen: bool) -> dict:
    """Synchron — der Aufrufer schiebt es in einen Thread. Gibt status/file_key/error zurück."""
    schluessel = eintrag["recording"]["file_key"]
    if not eingerichtet():
        return {"status": "skipped", "file_key": schluessel, "error": None}
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        s3 = _s3()
        groesse = mp3_path.stat().st_size
        vorher = _vorhanden(s3, schluessel)
        if vorher is not None and not ersetzen:
            if vorher != groesse:
                return {"status": "failed", "file_key": schluessel,
                        "error": "Im Archiv der App liegt unter diesem Namen schon eine andere Datei"}
        else:
            s3.upload_file(str(mp3_path), config.online_s3_bucket, schluessel,
                           ExtraArgs={"ContentType": "audio/mpeg"})
        vtt = vtt_aus_transkript(wav_path.parent / "transcript.jsonl", von_s, bis_s)
        if vtt:
            vtt_schluessel = schluessel[:-4] + ".vtt"
            if ersetzen or _vorhanden(s3, vtt_schluessel) is None:
                s3.put_object(Bucket=config.online_s3_bucket, Key=vtt_schluessel,
                              Body=vtt.encode("utf-8"), ContentType="text/vtt; charset=utf-8")
    except (BotoCoreError, ClientError, OSError) as e:
        log.warning("Online stellen: Upload von %s fehlgeschlagen: %s", schluessel, type(e).__name__)
        return {"status": "failed", "file_key": schluessel, "error": f"Upload: {type(e).__name__}"}

    eintrag = json.loads(json.dumps(eintrag))       # nicht das Original verändern
    eintrag["recording"]["file_size_bytes"] = groesse
    if dauer_ms:
        eintrag["recording"]["duration_seconds"] = round(dauer_ms / 1000)
    try:
        with httpx.Client(base_url=config.online_api_url.rstrip("/"), timeout=ZEITLIMIT_S) as client:
            _anmelden(client)
            r = client.post("/api/archive/recordings/bulk-import/", json={"items": [eintrag]})
            r.raise_for_status()
            antwort = r.json()
    except (httpx.HTTPError, RuntimeError, ValueError) as e:
        log.warning("Online stellen: Eintrag von %s fehlgeschlagen: %s", schluessel, e)
        return {"status": "failed", "file_key": schluessel, "error": f"Eintrag: {e}"[:200]}
    if antwort.get("errors"):
        return {"status": "failed", "file_key": schluessel,
                "error": f"Eintrag: {str(antwort['errors'][0])[:180]}"}
    return {"status": "uploaded", "file_key": schluessel, "error": None}
