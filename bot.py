"""Telegram bot: inline-menu UI, command handling, event-message formatting.

The bot is navigated mostly through inline buttons. Every screen is one message
that gets edited in place as the user taps around, so the chat stays clean.

Entry points (also in the native command menu):
    /start, /menu    — open the main menu.
    /me              — show your Telegram id and registration status.

Admin-only screens (buttons): statistics, today's events, operator management.
Text shortcuts kept for admins: /addoperator <id>, /deloperator <id>.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

import config
import db
import telegram

# error_kind values that mean the activation code was one-time and is spent
# ("палёный" QR) — worth calling out explicitly to the operator.
_BURNT_CODE_KINDS = {"code_consumed", "smdp", "invalid_code"}

# Commands shown in Telegram's native command menu (set on server startup).
BOT_COMMANDS = [
    {"command": "start", "description": "Открыть меню"},
    {"command": "menu", "description": "Главное меню"},
    {"command": "me", "description": "Показать мой Telegram ID"},
]

# admin_id -> pending conversational action. Currently only "addop": the bot is
# waiting for the admin to type the operator's <telegram_id>. In-memory is fine —
# on the rare server restart the admin just taps the button again.
_pending: dict[int, str] = {}


# ── small helpers ────────────────────────────────────────────────────────────


def _esc(value: object) -> str:
    return html.escape(str(value)) if value not in (None, "") else "—"


def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def _local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt + timedelta(hours=config.TZ_OFFSET_HOURS)


def _day_start_utc() -> datetime:
    """UTC instant of the most recent local midnight."""
    now_local = _local(datetime.now(timezone.utc))
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_local - timedelta(hours=config.TZ_OFFSET_HOURS)


def _is_admin(telegram_id: int) -> bool:
    if telegram_id in config.ADMIN_IDS:
        return True
    op = db.get_operator(telegram_id)
    return bool(op and op.get("role") == "admin")


# ── event → operator message ─────────────────────────────────────────────────


def format_event_message(doc: dict) -> str:
    """Render a stored event document as the HTML message the operator sees."""
    created = doc.get("created_at")
    when = _local(created).strftime("%d.%m.%Y %H:%M") if isinstance(created, datetime) else "—"

    if doc.get("type") == "success":
        return (
            "✅ <b>Профиль установлен на eSIM</b>\n"
            f"Оператор связи: {_esc(doc.get('smdp'))}\n"
            f"ICCID: <code>{_esc(doc.get('iccid'))}</code>\n"
            f"Устройство: {_esc(doc.get('device_model'))} · "
            f"IMEI <code>{_esc(doc.get('imei'))}</code>\n"
            f"Ридер: {_esc(doc.get('reader_label'))}\n"
            f"🕒 {when}"
        )

    lines = [
        "❌ <b>Накатка профиля не удалась</b>",
        f"Причина: {_esc(doc.get('error_message'))}",
    ]
    if str(doc.get("error_kind") or "") in _BURNT_CODE_KINDS:
        lines.append("⚠️ Похоже, QR-код одноразовый и уже использован (палёный).")
    lines.append(f"Оператор связи: {_esc(doc.get('smdp'))}")
    if doc.get("code_tail"):
        lines.append(f"QR: …{_esc(doc.get('code_tail'))}")
    lines.append(f"🕒 {when}")
    return "\n".join(lines)


# ── keyboards ────────────────────────────────────────────────────────────────


def _main_menu_kb(is_admin: bool) -> dict:
    rows: list[list[dict]] = []
    if is_admin:
        rows.append([_btn("📊 Статистика", "stats"), _btn("🗓 Сегодня", "today")])
        rows.append([_btn("👥 Операторы", "operators")])
    rows.append([_btn("🆔 Мой ID", "me"), _btn("ℹ️ Помощь", "help")])
    return {"inline_keyboard": rows}


def _back_kb() -> dict:
    return {"inline_keyboard": [[_btn("⬅️ Главное меню", "menu")]]}


def _operators_kb() -> dict:
    rows: list[list[dict]] = []
    for op in db.list_operators():
        label = op.get("name") or str(op.get("telegram_id"))
        rows.append([_btn(f"🗑 {label}", f"delop:{op.get('telegram_id')}")])
    rows.append([_btn("➕ Добавить оператора", "addop")])
    rows.append([_btn("⬅️ Главное меню", "menu")])
    return {"inline_keyboard": rows}


def _del_confirm_kb(op_id: int) -> dict:
    return {"inline_keyboard": [
        [_btn("✅ Да, удалить", f"delyes:{op_id}")],
        [_btn("⬅️ Отмена", "operators")],
    ]}


def _cancel_kb() -> dict:
    return {"inline_keyboard": [[_btn("⬅️ Отмена", "operators")]]}


# ── screen texts ─────────────────────────────────────────────────────────────


def _main_menu_text(telegram_id: int) -> str:
    op = db.get_operator(telegram_id)
    if op and op.get("active", True):
        role = " · администратор" if _is_admin(telegram_id) else ""
        status = f"Вы вошли как <b>{_esc(op.get('name'))}</b>{role}."
    else:
        status = "⚠️ Вы не зарегистрированы в системе учёта."
    return f"📋 <b>Главное меню</b>\n{status}\n\nВыберите раздел:"


def _identity_text(telegram_id: int) -> str:
    op = db.get_operator(telegram_id)
    status = (
        f"✅ Вы зарегистрированы как <b>{_esc(op.get('name'))}</b>."
        if op and op.get("active", True)
        else "⚠️ Вы пока не зарегистрированы. Передайте этот ID администратору."
    )
    return f"🆔 Ваш Telegram ID: <code>{telegram_id}</code>\n{status}"


def _help_text(is_admin: bool) -> str:
    if is_admin:
        return (
            "ℹ️ <b>Справка</b>\n\n"
            "Бот ведёт учёт накаток eSIM.\n\n"
            "📊 <b>Статистика</b> — сводка успехов и ошибок.\n"
            "🗓 <b>Сегодня</b> — события за текущий день.\n"
            "👥 <b>Операторы</b> — список доступа: добавить или удалить.\n\n"
            "Операторам уведомления приходят сюда автоматически после каждой накатки."
        )
    return (
        "ℹ️ <b>Справка</b>\n\n"
        "Сюда автоматически приходят уведомления об установке eSIM-профилей — "
        "после каждой накатки в приложении.\n\n"
        "🆔 <b>Мой ID</b> — ваш Telegram ID, его нужно передать администратору "
        "для регистрации."
    )


def _stats_text() -> str:
    since = _day_start_utc()
    today_ok = db.count_events(since=since, event_type="success")
    today_fail = db.count_events(since=since, event_type="fail")
    all_ok = db.count_events(event_type="success")
    all_fail = db.count_events(event_type="fail")
    operators = len(db.list_operators())
    return (
        "📊 <b>Статистика накаток</b>\n\n"
        f"Сегодня:  ✅ {today_ok}   ❌ {today_fail}\n"
        f"Всего:    ✅ {all_ok}   ❌ {all_fail}\n\n"
        f"Операторов в базе: {operators}"
    )


def _today_text() -> str:
    rows = db.recent_events(since=_day_start_utc(), limit=20)
    if not rows:
        return "🗓 <b>События за сегодня</b>\n\nСобытий ещё нет."
    out = ["🗓 <b>События за сегодня</b> (последние 20):", ""]
    for ev in rows:
        when = _local(ev["created_at"]).strftime("%H:%M") if ev.get("created_at") else "--:--"
        mark = "✅" if ev.get("type") == "success" else "❌"
        out.append(f"{mark} {when} · {_esc(ev.get('operator_name'))} · {_esc(ev.get('smdp'))}")
    return "\n".join(out)


def _operators_text() -> str:
    ops = db.list_operators()
    if not ops:
        return ("👥 <b>Операторы</b>\n\nСписок пуст. Нажмите «➕ Добавить оператора».")
    out = ["👥 <b>Операторы</b>\n"]
    for op in ops:
        flag = "" if op.get("active", True) else " (выключен)"
        role = " · админ" if op.get("role") == "admin" else ""
        out.append(f"• {_esc(op.get('name'))} — <code>{op.get('telegram_id')}</code>{role}{flag}")
    out.append("\nКнопка 🗑 — удалить оператора.")
    return "\n".join(out)


def _addop_prompt_text() -> str:
    return (
        "➕ <b>Добавление оператора</b>\n\n"
        "Пришлите <b>числовой Telegram ID</b> оператора одним сообщением.\n"
        "Например: <code>123456789</code>\n\n"
        "Имя подтянется из Telegram автоматически. Оператор должен заранее "
        "нажать /start у бота — там же командой /me он узнаёт свой ID."
    )


def _del_confirm_text(op_id: int) -> str:
    op = db.get_operator(op_id)
    name = op.get("name") if op else op_id
    return (
        f"Удалить оператора <b>{_esc(name)}</b> (<code>{op_id}</code>)?\n"
        "Он потеряет доступ к приложению."
    )


# ── operator add/remove ──────────────────────────────────────────────────────


def _resolve_operator_name(telegram_id: int) -> str | None:
    """Best-effort display name from Telegram. None when the bot cannot see the
    user — i.e. they have not pressed /start yet."""
    try:
        chat = telegram.get_chat(telegram_id)
    except Exception:  # noqa: BLE001
        return None
    full = " ".join(p for p in (chat.get("first_name"), chat.get("last_name")) if p)
    if full.strip():
        return full.strip()
    username = chat.get("username")
    return f"@{username}" if username else None


def _try_add_operator(raw: str) -> str:
    """Register an operator by Telegram id alone — the display name is pulled
    from Telegram. If the user has not started the bot yet, the id stands in
    until they are added again."""
    parts = raw.split()
    if not parts or not parts[0].lstrip("-").isdigit():
        return ("⚠️ Пришлите числовой Telegram ID оператора.\n"
                "Например: <code>123456789</code>")
    telegram_id = int(parts[0])
    name = _resolve_operator_name(telegram_id)
    if name:
        db.upsert_operator(telegram_id, name)
        return f"✅ Оператор <b>{_esc(name)}</b> (<code>{telegram_id}</code>) добавлен."
    db.upsert_operator(telegram_id, str(telegram_id))
    return (
        f"✅ Оператор <code>{telegram_id}</code> добавлен.\n"
        "⚠️ Имя из Telegram подтянуть не удалось — скорее всего оператор ещё "
        "не нажимал /start у бота. Когда нажмёт, добавьте его этим же ID "
        "повторно — имя обновится."
    )


# ── update routing ───────────────────────────────────────────────────────────


def handle_update(update: dict) -> None:
    """Process one Telegram update. Exceptions are caught by the caller."""
    if "callback_query" in update:
        _handle_callback(update["callback_query"])
    else:
        _handle_message(update.get("message") or update.get("edited_message"))


def _handle_message(msg: dict | None) -> None:
    if not msg:
        return
    chat_id = (msg.get("chat") or {}).get("id")
    from_id = (msg.get("from") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if chat_id is None or from_id is None or not text:
        return

    is_admin = _is_admin(from_id)

    # Admin typed the operator details we were waiting for.
    if not text.startswith("/") and _pending.get(from_id) == "addop":
        _pending.pop(from_id, None)
        if not is_admin:
            return
        telegram.send_message(chat_id, _try_add_operator(text), reply_markup=_operators_kb())
        return

    if not text.startswith("/"):
        return  # ignore plain chatter

    # Any command cancels a pending conversational action.
    _pending.pop(from_id, None)
    parts = text.split()
    cmd = parts[0].split("@", 1)[0].lower()
    args = parts[1:]

    if cmd in ("/start", "/menu"):
        telegram.send_message(chat_id, _main_menu_text(from_id),
                              reply_markup=_main_menu_kb(is_admin))
    elif cmd == "/me":
        telegram.send_message(chat_id, _identity_text(from_id), reply_markup=_back_kb())
    elif cmd in ("/addoperator", "/deloperator"):
        if not is_admin:
            telegram.send_message(chat_id, "⛔ Команда доступна только администраторам.")
            return
        if cmd == "/addoperator":
            reply = _try_add_operator(" ".join(args))
        else:
            reply = _del_operator_cmd(args)
        telegram.send_message(chat_id, reply, reply_markup=_operators_kb())
    else:
        telegram.send_message(chat_id, "Не знаю такой команды. Откройте /menu.")


def _del_operator_cmd(args: list[str]) -> str:
    if len(args) != 1 or not args[0].lstrip("-").isdigit():
        return "Использование: /deloperator &lt;telegram_id&gt;"
    telegram_id = int(args[0])
    if db.delete_operator(telegram_id):
        return f"🗑 Оператор <code>{telegram_id}</code> удалён."
    return f"Оператор <code>{telegram_id}</code> в базе не найден."


def _handle_callback(cq: dict) -> None:
    cq_id = cq.get("id", "")
    from_id = (cq.get("from") or {}).get("id")
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    data = cq.get("data") or ""
    if from_id is None:
        telegram.answer_callback(cq_id)
        return

    is_admin = _is_admin(from_id)
    # Navigating anywhere other than "continue adding" cancels a pending input.
    if data != "addop":
        _pending.pop(from_id, None)

    def show(text: str, keyboard: dict) -> None:
        if chat_id is not None and message_id is not None:
            telegram.edit_message(chat_id, message_id, text, reply_markup=keyboard)

    notice = ""
    admin_only = (data in ("stats", "today", "operators", "addop")
                  or data.startswith("delop:") or data.startswith("delyes:"))

    if admin_only and not is_admin:
        notice = "Только для администраторов"
    elif data == "menu":
        show(_main_menu_text(from_id), _main_menu_kb(is_admin))
    elif data == "me":
        show(_identity_text(from_id), _back_kb())
    elif data == "help":
        show(_help_text(is_admin), _back_kb())
    elif data == "stats":
        show(_stats_text(), _back_kb())
    elif data == "today":
        show(_today_text(), _back_kb())
    elif data == "operators":
        show(_operators_text(), _operators_kb())
    elif data == "addop":
        _pending[from_id] = "addop"
        show(_addop_prompt_text(), _cancel_kb())
    elif data.startswith("delop:"):
        op_id = data.split(":", 1)[1]
        if op_id.lstrip("-").isdigit():
            show(_del_confirm_text(int(op_id)), _del_confirm_kb(int(op_id)))
    elif data.startswith("delyes:"):
        op_id = data.split(":", 1)[1]
        if op_id.lstrip("-").isdigit():
            notice = ("Оператор удалён" if db.delete_operator(int(op_id))
                      else "Оператор не найден")
        show(_operators_text(), _operators_kb())

    telegram.answer_callback(cq_id, notice)
