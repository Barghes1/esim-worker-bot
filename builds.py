"""On-the-fly personalization of the desktop-app download.

The app ships as one common build, published as a GitHub release asset. For
each operator the server injects a small operator.json (their Telegram id)
into a copy of that zip — so the downloaded app knows its operator without a
per-operator PyInstaller build.

The personalized zip is built once per operator and cached on the instance's
temp disk; the common base zip is fetched from the release once per instance.
A new app release ships by replacing the asset and redeploying (a redeploy
restarts the instance, which clears the cache).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path

import requests

import config

# Where the running app looks for its identity — next to esim-worker.exe,
# which sits one level inside the zip.
_OPERATOR_FILE = "esim-worker/operator.json"

_CACHE_DIR = Path(tempfile.gettempdir()) / "esim-worker-builds"
_BASE_ZIP = _CACHE_DIR / "base.zip"
_LOCK = threading.Lock()


# ── signed download links ────────────────────────────────────────────────────


def download_sig(operator_id: int) -> str:
    """Short HMAC tag so a download link cannot be re-pointed to another id."""
    return hmac.new(
        config.WEBHOOK_SECRET.encode(), str(operator_id).encode(), hashlib.sha256
    ).hexdigest()[:16]


def valid_sig(operator_id: int, sig: str) -> bool:
    return bool(config.WEBHOOK_SECRET) and hmac.compare_digest(
        sig, download_sig(operator_id)
    )


def download_url(operator_id: int) -> str | None:
    """Public URL the bot hands to an operator. None when the server is not
    yet configured (no public URL / secret)."""
    if not config.PUBLIC_URL or not config.WEBHOOK_SECRET:
        return None
    return f"{config.PUBLIC_URL}/download/{operator_id}/{download_sig(operator_id)}"


# ── build personalization ────────────────────────────────────────────────────


def _ensure_base() -> Path:
    """Fetch the common build once per instance; reuse it afterwards."""
    if _BASE_ZIP.exists() and _BASE_ZIP.stat().st_size > 0:
        return _BASE_ZIP
    with _LOCK:
        if _BASE_ZIP.exists() and _BASE_ZIP.stat().st_size > 0:
            return _BASE_ZIP
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        part = _BASE_ZIP.with_suffix(".part")
        with requests.get(config.BASE_BUILD_URL, stream=True, timeout=180) as resp:
            resp.raise_for_status()
            with part.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
        part.replace(_BASE_ZIP)
    return _BASE_ZIP


def personalized_zip(operator_id: int, operator_name: str) -> Path:
    """Path to a build zip carrying this operator's identity. Cached per
    operator for the lifetime of the server instance."""
    out = _CACHE_DIR / f"esim-worker-{operator_id}.zip"
    if out.exists() and out.stat().st_size > 0:
        return out
    base = _ensure_base()
    with _LOCK:
        if out.exists() and out.stat().st_size > 0:
            return out
        part = out.with_suffix(".part")
        shutil.copyfile(base, part)
        identity = json.dumps(
            {"operator_id": operator_id, "operator_name": operator_name},
            ensure_ascii=False,
        )
        with zipfile.ZipFile(part, "a", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(_OPERATOR_FILE, identity)
        part.replace(out)
    return out
