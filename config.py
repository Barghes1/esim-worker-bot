"""Server configuration, read from environment variables.

Secrets (bot token, MongoDB URI) live ONLY here on the server — never in the
desktop app or its .exe. For local runs we load a sibling .env file; on Render
the same keys are set as service environment variables.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader — avoids a python-dotenv dependency."""
    path = Path(__file__).resolve().parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
DB_NAME = os.environ.get("DB_NAME", "esimworker")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# Admin Telegram IDs — comma-separated. Admins can run /stats, /addoperator, etc.
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",")
    if x.strip().lstrip("-").isdigit()
}

# Public https URL of this service. Render injects RENDER_EXTERNAL_URL
# automatically; PUBLIC_URL is the manual fallback for other hosts.
PUBLIC_URL = (os.environ.get("RENDER_EXTERNAL_URL")
              or os.environ.get("PUBLIC_URL", "")).rstrip("/")

# Hours to add to UTC when formatting timestamps and computing "today"
# (3 = Moscow time).
TZ_OFFSET_HOURS = int(os.environ.get("TZ_OFFSET_HOURS", "3") or "3")

if not BOT_TOKEN or not MONGO_URI:
    print("WARNING: BOT_TOKEN and/or MONGO_URI are not set — "
          "set them in server/.env or as environment variables.",
          file=sys.stderr)
