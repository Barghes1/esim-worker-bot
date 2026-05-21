"""esim-worker reporting server.

Endpoints:
  GET  /health                 — health probe.
  POST /api/handshake          — desktop app checks operator authorization.
  POST /api/events             — desktop app reports provisioning events.
  POST /telegram/{secret}      — Telegram webhook for bot commands.

Run locally:   uvicorn app:app --reload
On Render:     uvicorn app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import bot
import builds
import config
import db
import telegram


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        db.ensure_indexes()
    except Exception as exc:  # noqa: BLE001
        print(f"index setup failed: {exc}")
    try:
        telegram.set_my_commands(bot.BOT_COMMANDS)
    except Exception as exc:  # noqa: BLE001
        print(f"setMyCommands failed: {exc}")
    if config.PUBLIC_URL and config.WEBHOOK_SECRET:
        hook = f"{config.PUBLIC_URL}/telegram/{config.WEBHOOK_SECRET}"
        try:
            telegram.set_webhook(hook)
            print(f"webhook set: {hook}")
        except Exception as exc:  # noqa: BLE001
            print(f"setWebhook failed: {exc}")
    else:
        print("PUBLIC_URL or WEBHOOK_SECRET missing — webhook not registered.")
    yield


app = FastAPI(title="esim-worker reporting server", lifespan=lifespan)


# ── request models ───────────────────────────────────────────────────────────


class Handshake(BaseModel):
    operator_id: int
    app_version: str = ""


class EventIn(BaseModel):
    type: str  # "success" | "fail"
    smdp: str | None = None
    iccid: str | None = None
    imei: str | None = None
    device_model: str | None = None
    reader_label: str | None = None
    code_tail: str | None = None
    error_kind: str | None = None
    error_message: str | None = None
    timings: dict[str, float] | None = None
    client_event_id: str
    app_ts: str | None = None


class EventsIn(BaseModel):
    operator_id: int
    app_version: str = ""
    events: list[EventIn]


# ── endpoints ────────────────────────────────────────────────────────────────


@app.get("/health")
@app.get("/")
def health() -> dict:
    return {"ok": True, "service": "esim-worker reporting server"}


@app.post("/api/handshake")
def handshake(req: Handshake) -> dict:
    """Desktop app calls this on launch. 403 means the operator is not in the
    database — the app then blocks provisioning."""
    op = db.get_operator(req.operator_id)
    if not op or not op.get("active", True):
        raise HTTPException(status_code=403, detail="operator not authorized")
    return {
        "ok": True,
        "operator_name": op.get("name", ""),
        "role": op.get("role", "operator"),
    }


@app.post("/api/events")
def post_events(req: EventsIn) -> dict:
    """Store reported events and DM the operator about each new one.

    Idempotent: events are deduped by client_event_id, so the desktop app can
    safely resend its offline buffer.
    """
    op = db.get_operator(req.operator_id)
    if not op or not op.get("active", True):
        raise HTTPException(status_code=403, detail="operator not authorized")

    accepted: list[str] = []
    duplicates: list[str] = []
    for event in req.events:
        doc = event.model_dump()
        doc["operator_id"] = req.operator_id
        doc["operator_name"] = op.get("name", "")
        doc["app_version"] = req.app_version
        doc["created_at"] = datetime.now(timezone.utc)

        if db.insert_event(doc):
            accepted.append(event.client_event_id)
            message = bot.format_event_message(doc)
            for chat_id in bot.event_recipients(req.operator_id):
                try:
                    telegram.send_message(chat_id, message)
                except Exception as exc:  # noqa: BLE001
                    # Stats are saved; a failed DM (e.g. a recipient never
                    # pressed /start) must not fail the request.
                    print(f"notify failed for {chat_id}: {exc}")
        else:
            duplicates.append(event.client_event_id)

    return {"ok": True, "accepted": accepted, "duplicates": duplicates}


@app.get("/download/{operator_id}/{sig}")
def download_app(operator_id: int, sig: str):
    """Serve the desktop app personalized for one operator.

    The bot hands out a signed link, so operator_id cannot be swapped. The
    operator's operator.json is injected into a cached copy of the common build.
    """
    if not builds.valid_sig(operator_id, sig):
        raise HTTPException(status_code=404, detail="not found")
    op = db.get_operator(operator_id)
    if not op or not op.get("active", True):
        raise HTTPException(status_code=403, detail="operator not authorized")
    try:
        path = builds.personalized_zip(operator_id, op.get("name", ""))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503,
                            detail="build temporarily unavailable") from exc
    return FileResponse(path, media_type="application/zip",
                        filename="esim-worker.zip")


@app.post("/telegram/{secret}")
def telegram_webhook(secret: str, update: dict) -> dict:
    if secret != config.WEBHOOK_SECRET or not config.WEBHOOK_SECRET:
        raise HTTPException(status_code=404, detail="not found")
    try:
        bot.handle_update(update)
    except Exception as exc:  # noqa: BLE001
        print(f"update handling failed: {exc}")
    return {"ok": True}
