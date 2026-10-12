"""SMB upload client for Synology NAS (EXP-02).

Uses smbprotocol's high-level smbclient API. Synchronous calls — wrap with
asyncio.to_thread when calling from FastAPI handlers.

Why smbprotocol over OS-level SMB mount: STATE.md decision —
"OS-level SMB mounts disconnect on sleep/wake; programmatic client handles reconnection".

Security (T-07-01): never log nas_password — only log local path + error message.
Security (T-07-02): _build_remote_path strips leading /\\ from each component;
  callers MUST construct remote_subpath from sanitised filenames only.
"""
from __future__ import annotations

import logging
import pathlib
from datetime import datetime
from typing import Optional

import smbclient
import smbclient.shutil

log = logging.getLogger(__name__)


class NASClient:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._registered = False

    def _ensure_session(self) -> None:
        if self._registered:
            return
        smbclient.register_session(
            self.cfg.nas_server,
            username=self.cfg.nas_user,
            password=self.cfg.nas_password,
        )
        self._registered = True

    def _build_remote_path(self, remote_subpath: str) -> str:
        """Build UNC path: \\\\server\\share\\base\\subpath (backslashes)."""
        parts = [self.cfg.nas_server, self.cfg.nas_share]
        if self.cfg.nas_base_path:
            parts.append(self.cfg.nas_base_path.strip("/\\"))
        parts.append(remote_subpath.strip("/\\"))
        return "\\\\" + "\\".join(p.replace("/", "\\") for p in parts)

    def exists(self, remote_subpath: str) -> bool:
        """True when something already lies at that path inside the share."""
        if not self.cfg.nas_server:
            return False
        try:
            self._ensure_session()
            return self._exists(self._build_remote_path(remote_subpath))
        except Exception:      # unreachable or no permission — caller must not assume "free"
            return True

    def _exists(self, remote: str) -> bool:
        try:
            smbclient.stat(remote)
            return True
        except Exception:      # not found, or no permission to look — treat as absent
            return False

    def upload_file(self, local_path: pathlib.Path, remote_subpath: str,
                    allow_overwrite: bool = False) -> dict:
        """Upload local_path to NAS at remote_subpath relative to nas_base_path.

        The share holds an archive that people filled by hand for years. Writing into it
        must never destroy that silently, so an existing file is refused unless the caller
        states it is replacing its own earlier upload (allow_overwrite).

        Never raises. Returns dict with keys:
          status   : 'uploaded' | 'failed'
          error    : str | None
          nas_path : str | None  (UNC path on NAS)
          uploaded_at : datetime | None
        """
        if not self.cfg.nas_server:
            return {
                "status": "failed",
                "error": "NAS not configured",
                "nas_path": None,
                "uploaded_at": None,
            }
        try:
            self._ensure_session()
            remote = self._build_remote_path(remote_subpath)
            if not allow_overwrite and self._exists(remote):
                return {
                    "status": "failed",
                    "error": "Datei liegt schon auf dem NAS und wurde nicht von hier "
                             "hochgeladen — zum Ersetzen erneut exportieren",
                    "nas_path": remote,
                    "uploaded_at": None,
                }
            parent = remote.rsplit("\\", 1)[0]
            smbclient.makedirs(parent, exist_ok=True)
            smbclient.shutil.copyfile(str(local_path), remote)
            return {
                "status": "uploaded",
                "error": None,
                "nas_path": remote,
                "uploaded_at": datetime.now(),
            }
        except Exception as exc:  # noqa: BLE001 — NAS is best-effort, never crash export
            log.warning("NAS upload failed for %s: %s", local_path, exc)
            return {
                "status": "failed",
                "error": str(exc),
                "nas_path": None,
                "uploaded_at": None,
            }
