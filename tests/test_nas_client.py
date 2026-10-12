"""Tests for NASClient SMB upload (Phase 7, EXP-02).

smbprotocol is mocked at module level so these tests run even when the library
is not installed in the test environment. The importorskip guard ensures clean
skip output if the library is absent rather than collection errors.
"""
import pathlib
import sys
import types
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

smbprotocol = pytest.importorskip("smbprotocol")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(nas_server="srv", nas_share="Audio", nas_user="u", nas_password="p", nas_base_path="GD"):
    return types.SimpleNamespace(
        nas_server=nas_server,
        nas_share=nas_share,
        nas_user=nas_user,
        nas_password=nas_password,
        nas_base_path=nas_base_path,
    )


LOCAL = pathlib.Path("/tmp/sermon.mp3")
REMOTE_SUBPATH = "2026/04/sermon.mp3"


# ---------------------------------------------------------------------------
# Test 1: successful upload returns uploaded status + nas_path + uploaded_at
# ---------------------------------------------------------------------------

def test_upload_file_success():
    """upload_file returns status='uploaded' with nas_path and uploaded_at when SMB calls succeed."""
    cfg = _make_cfg()

    with patch("smbclient.register_session"), \
         patch("smbclient.makedirs"), \
         patch("smbclient.shutil.copyfile"):

        # Force reimport so patches are picked up fresh
        if "nas_client" in sys.modules:
            del sys.modules["nas_client"]
        from nas_client import NASClient
        client = NASClient(cfg)
        result = client.upload_file(LOCAL, REMOTE_SUBPATH)

    assert result["status"] == "uploaded"
    assert result["nas_path"] is not None
    assert isinstance(result["uploaded_at"], datetime)
    assert result["error"] is None


# ---------------------------------------------------------------------------
# Test 2: OSError from copyfile → status='failed', error contains message, no raise
# ---------------------------------------------------------------------------

def test_upload_file_auth_failure_returns_failed():
    """When copyfile raises OSError, upload_file returns status='failed' with error text (never raises)."""
    cfg = _make_cfg()

    with patch("smbclient.register_session"), \
         patch("smbclient.makedirs"), \
         patch("smbclient.shutil.copyfile", side_effect=OSError("auth failed")):

        if "nas_client" in sys.modules:
            del sys.modules["nas_client"]
        from nas_client import NASClient
        client = NASClient(cfg)
        result = client.upload_file(LOCAL, REMOTE_SUBPATH)

    assert result["status"] == "failed"
    assert "auth failed" in result["error"]
    assert result["nas_path"] is None


# ---------------------------------------------------------------------------
# Test 3: empty nas_server → immediate failed, no smbclient calls
# ---------------------------------------------------------------------------

def test_upload_file_not_configured():
    """When nas_server is empty, upload_file returns status='failed' with 'NAS not configured' error."""
    cfg = _make_cfg(nas_server="")

    with patch("smbclient.register_session") as mock_reg, \
         patch("smbclient.makedirs") as mock_mkdirs, \
         patch("smbclient.shutil.copyfile") as mock_copy:

        if "nas_client" in sys.modules:
            del sys.modules["nas_client"]
        from nas_client import NASClient
        client = NASClient(cfg)
        result = client.upload_file(LOCAL, REMOTE_SUBPATH)

    assert result["status"] == "failed"
    assert "NAS not configured" in result["error"]
    # No SMB calls should have been made
    mock_reg.assert_not_called()
    mock_mkdirs.assert_not_called()
    mock_copy.assert_not_called()


# ---------------------------------------------------------------------------
# Test 4: makedirs called for parent directory with exist_ok=True
# ---------------------------------------------------------------------------

def test_upload_file_calls_makedirs_for_parent():
    """upload_file calls smbclient.makedirs on the parent dir of the remote path with exist_ok=True."""
    cfg = _make_cfg()

    with patch("smbclient.register_session"), \
         patch("smbclient.makedirs") as mock_mkdirs, \
         patch("smbclient.shutil.copyfile"):

        if "nas_client" in sys.modules:
            del sys.modules["nas_client"]
        from nas_client import NASClient
        client = NASClient(cfg)
        client.upload_file(LOCAL, REMOTE_SUBPATH)

    mock_mkdirs.assert_called_once()
    call_args = mock_mkdirs.call_args
    # exist_ok must be True — check keyword or positional arg
    assert call_args.kwargs.get("exist_ok") is True or (len(call_args.args) > 1 and call_args.args[1] is True)


# ---------------------------------------------------------------------------
# Test 5: remote path uses UNC format \\server\share\base\subpath with backslashes
# ---------------------------------------------------------------------------

def test_upload_file_unc_remote_path():
    """upload_file constructs UNC path of form \\\\server\\share\\base\\subpath."""
    cfg = _make_cfg(nas_server="synology", nas_share="Audio", nas_base_path="Gottesdienste")

    with patch("smbclient.register_session"), \
         patch("smbclient.makedirs"), \
         patch("smbclient.shutil.copyfile"):

        if "nas_client" in sys.modules:
            del sys.modules["nas_client"]
        from nas_client import NASClient
        client = NASClient(cfg)
        result = client.upload_file(LOCAL, "2026/04/sermon.mp3")

    assert result["status"] == "uploaded"
    expected_prefix = r"\\synology\Audio\Gottesdienste"
    assert result["nas_path"].startswith(expected_prefix), (
        f"Expected UNC path starting with {expected_prefix!r}, got {result['nas_path']!r}"
    )
    assert "2026" in result["nas_path"]
    assert "sermon.mp3" in result["nas_path"]
