"""MongoDB access layer.

Two collections:
  operators — who is allowed to use the app; one doc per Telegram id.
  events    — one doc per provisioning attempt (success/fail), the stats source.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError

import config

_client: MongoClient = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=8000)
_db = _client[config.DB_NAME]

operators = _db["operators"]
events = _db["events"]


def ensure_indexes() -> None:
    operators.create_index([("telegram_id", ASCENDING)], unique=True)
    events.create_index([("client_event_id", ASCENDING)], unique=True)
    events.create_index([("created_at", DESCENDING)])


# ── operators ────────────────────────────────────────────────────────────────


def get_operator(telegram_id: int) -> Optional[dict]:
    return operators.find_one({"telegram_id": int(telegram_id)})


def is_authorized(telegram_id: int) -> bool:
    op = get_operator(telegram_id)
    return bool(op and op.get("active", True))


def upsert_operator(telegram_id: int, name: str, role: str = "operator") -> None:
    operators.update_one(
        {"telegram_id": int(telegram_id)},
        {
            "$set": {"name": name, "active": True, "role": role},
            "$setOnInsert": {
                "telegram_id": int(telegram_id),
                "created_at": datetime.now(timezone.utc),
            },
        },
        upsert=True,
    )


def delete_operator(telegram_id: int) -> bool:
    return operators.delete_one({"telegram_id": int(telegram_id)}).deleted_count > 0


def set_download_url(telegram_id: int, url: str) -> bool:
    """Attach a per-operator app download URL. False if no such operator."""
    result = operators.update_one(
        {"telegram_id": int(telegram_id)},
        {"$set": {"download_url": url}},
    )
    return result.matched_count > 0


def list_operators() -> list[dict]:
    return list(operators.find().sort("name", ASCENDING))


# ── events ───────────────────────────────────────────────────────────────────


def insert_event(doc: dict) -> bool:
    """Insert one event. Returns False if the client_event_id already exists
    (idempotent — the desktop app may resend buffered events)."""
    try:
        events.insert_one(doc)
        return True
    except DuplicateKeyError:
        return False


def count_events(since: Optional[datetime] = None,
                  event_type: Optional[str] = None) -> int:
    query: dict[str, Any] = {}
    if since is not None:
        query["created_at"] = {"$gte": since}
    if event_type is not None:
        query["type"] = event_type
    return events.count_documents(query)


def recent_events(since: Optional[datetime] = None, limit: int = 20) -> list[dict]:
    query: dict[str, Any] = {}
    if since is not None:
        query["created_at"] = {"$gte": since}
    return list(events.find(query).sort("created_at", DESCENDING).limit(limit))
