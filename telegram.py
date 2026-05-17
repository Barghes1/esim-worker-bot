"""Thin Telegram Bot API client.

Covers what the bot needs: sending messages, editing them in place (for inline
menu navigation), answering callback queries, and registering the webhook and
command list.
"""

from __future__ import annotations

from typing import Optional

import requests

import config

_API = f"https://api.telegram.org/bot{config.BOT_TOKEN}"


def send_message(
    chat_id: int,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: str = "HTML",
) -> dict:
    """Send a message, optionally with an inline keyboard.

    Note: Telegram forbids a bot from messaging a user who has never started a
    chat with it — operators must press /start once before they get notified.
    """
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    resp = requests.post(f"{_API}/sendMessage", json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def edit_message(
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: str = "HTML",
) -> dict:
    """Edit an existing message in place — used for inline-menu navigation."""
    payload: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    resp = requests.post(f"{_API}/editMessageText", json=payload, timeout=15)
    # Re-tapping the same button yields identical content — Telegram rejects
    # that with 400 "message is not modified", which is harmless here.
    if resp.status_code == 400 and "not modified" in resp.text.lower():
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def answer_callback(callback_query_id: str, text: str = "") -> None:
    """Acknowledge a button press so the client stops showing a spinner.

    Best-effort: a failure here must never break update handling.
    """
    try:
        requests.post(
            f"{_API}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=15,
        )
    except requests.RequestException:
        pass


def set_webhook(url: str) -> dict:
    resp = requests.post(
        f"{_API}/setWebhook",
        json={"url": url, "allowed_updates": ["message", "callback_query"]},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def set_my_commands(commands: list[dict]) -> dict:
    """Register the bot's command list (the native menu next to the input box)."""
    resp = requests.post(
        f"{_API}/setMyCommands", json={"commands": commands}, timeout=15
    )
    resp.raise_for_status()
    return resp.json()
