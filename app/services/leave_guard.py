"""«قفل خروج» - automatically ban members who leave the group.

Two independent rules, both off by default (a public bot must never surprise a
group with bans nobody asked for):

``ban_on_leave``
    every member who leaves is banned right away,
``quick_leave_ban`` + ``quick_leave_seconds``
    only members who leave again within a few seconds of joining are banned
    (the classic "join & leave" spam pattern).

Both rules ignore administrators and the bot itself, and both need the bot to
hold the «محدود کردن اعضا» right.
"""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot

from ..core.errors import safe_call
from ..core.normalization import to_persian_digits
from .permissions import bot_has_right, fetch_chat_member

logger = logging.getLogger("armando.leave_guard")


def is_enabled(settings: dict) -> bool:
    return bool(settings.get("ban_on_leave")) or bool(settings.get("quick_leave_ban"))


def status_text(settings: dict) -> str:
    """A one-line Persian summary of the leave guard configuration."""
    if not is_enabled(settings):
        return "🔓 قفل خروج خاموش است"
    parts = []
    if settings.get("ban_on_leave"):
        parts.append("هر خروج → بن")
    if settings.get("quick_leave_ban"):
        seconds = int(settings.get("quick_leave_seconds") or 10)
        parts.append(f"خروج تا {to_persian_digits(str(seconds))} ثانیه پس از ورود → بن")
    return "🚪 " + " • ".join(parts)


async def check_leave(bot: Bot, chat_id: int, user_id: int, settings: dict,
                      joined_at: datetime | None = None) -> str | None:
    """Ban the member if a leave rule fires; return the Persian reason or None."""
    if not is_enabled(settings):
        return None
    if not await bot_has_right(bot, chat_id, "can_restrict_members"):
        logger.debug("leave guard skipped: bot may not restrict members in %s", chat_id)
        return None

    member = await fetch_chat_member(bot, chat_id, user_id, refresh=True)
    status = getattr(member, "status", "")
    if status in {"administrator", "creator", "kicked"}:
        return None  # never touch admins, and a banned user is already gone

    reason: str | None = None
    if settings.get("ban_on_leave"):
        reason = "خروج از گروه (قفل خروج)"
    elif settings.get("quick_leave_ban"):
        seconds = int(settings.get("quick_leave_seconds") or 10)
        if joined_at is not None:
            elapsed = (datetime.utcnow() - joined_at).total_seconds()
            if 0 <= elapsed <= seconds:
                reason = (f"خروج سریع: {to_persian_digits(str(int(elapsed)))} ثانیه "
                          f"پس از ورود")
        else:
            reason = "خروج بلافاصله پس از ورود"

    if reason is None:
        return None

    banned = await safe_call(lambda: bot.ban_chat_member(chat_id=chat_id, user_id=user_id),
                             default=None, context="leave_guard_ban", log=True)
    if banned is None:
        return None
    logger.info("leave guard banned user=%s in chat=%s (%s)", user_id, chat_id, reason)
    return reason
