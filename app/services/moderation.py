"""Core moderation engine: ban / kick / mute / warn and the shared action engine.

Every action writes a :class:`ModerationAction` record, an :class:`AuditLog`
entry and (optionally) mirrors itself to the group log channel.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.types import ChatPermissions
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.duration import format_duration
from ..core.errors import safe_ban, safe_delete, safe_restrict, safe_unban
from ..core.timeutils import persian_datetime
from ..db.models import (
    BanRecord,
    ModerationAction,
    User,
    Warning,
)
from ..db.models import ChatMemberState
from .audit import audit_and_log, html_user
from .chat_state import get_member_state, get_settings, invalidate_member
from .permissions import Actor

logger = logging.getLogger("armando.moderation")

MUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
    can_manage_topics=False,
)

UNMUTE_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_change_info=False,
    can_invite_users=True,
    can_pin_messages=False,
    can_manage_topics=False,
)

ACTION_LABELS_FA = {
    "ban": "بن",
    "temp_ban": "بن موقت",
    "unban": "رفع بن",
    "kick": "اخراج",
    "mute": "سکوت",
    "temp_mute": "سکوت موقت",
    "unmute": "لغو سکوت",
    "warn": "اخطار",
    "delete": "حذف پیام",
    "none": "بدون اقدام",
}

PUNISHMENT_OPTIONS_FA = {
    "none": "بدون اقدام",
    "delete": "حذف پیام",
    "warn": "اخطار",
    "mute": "سکوت",
    "temp_mute": "سکوت موقت",
    "kick": "اخراج",
    "ban": "بن",
    "temp_ban": "بن موقت",
    "delete_warn": "حذف + اخطار",
    "delete_mute": "حذف + سکوت",
    "delete_ban": "حذف + بن",
    "delete_kick": "حذف + اخراج",
}


def action_label_fa(action: str) -> str:
    return ACTION_LABELS_FA.get(action, action)


async def _display_name(session: AsyncSession, user_id: int | None) -> str:
    if not user_id:
        return "—"
    user = await session.get(User, user_id)
    if user:
        return f"{user.first_name or ''} {user.last_name or ''}".strip() or f"@{user.username or user_id}"
    return str(user_id)


def mention(user_id: int | None, name: str) -> str:
    return html_user(user_id, name)


# --------------------------------------------------------------------------- #
# Record keeping
# --------------------------------------------------------------------------- #
async def record_action(session: AsyncSession, *, chat_id: int, action: str, target_id: int,
                        actor_id: int | None, reason: str = "", duration: int | None = None,
                        source: str = "command", message_id: int | None = None,
                        payload: dict | None = None) -> ModerationAction:
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=duration) if duration else None
    entry = ModerationAction(
        chat_id=chat_id,
        target_id=target_id,
        actor_id=actor_id,
        action=action,
        reason=(reason or "")[:500],
        duration=duration,
        expires_at=expires_at,
        active=True,
        source=source,
        message_id=message_id,
        payload=payload or {},
        created_at=now,
    )
    session.add(entry)
    await session.flush()
    return entry


async def close_active_actions(session: AsyncSession, chat_id: int, target_id: int,
                               action: str, undone_by: int | None = None) -> int:
    result = await session.execute(
        update(ModerationAction)
        .where(ModerationAction.chat_id == chat_id,
               ModerationAction.target_id == target_id,
               ModerationAction.action == action,
               ModerationAction.active.is_(True))
        .values(active=False, undone_at=datetime.utcnow(), undone_by=undone_by)
    )
    return int(result.rowcount or 0)


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
async def ban_user(bot: Bot, session: AsyncSession, *, chat_id: int, actor: Actor,
                  target_id: int, target_name: str = "", duration: int | None = None,
                  reason: str = "", delete_message_id: int | None = None,
                  chat_title: str = "", source: str = "command") -> str:
    until = datetime.utcnow() + timedelta(seconds=duration) if duration else None
    ok = await safe_ban(bot, chat_id, target_id, until_date=until, context="ban")

    action = "temp_ban" if duration else "ban"
    await record_action(session, chat_id=chat_id, action=action, target_id=target_id,
                        actor_id=actor.user_id, reason=reason, duration=duration, source=source)
    state = await get_member_state(session, chat_id, target_id)
    state.status = "kicked"
    state.restricted_until = until
    state.updated_at = datetime.utcnow()
    session.add(BanRecord(chat_id=chat_id, user_id=target_id, banned_by=actor.user_id,
                          reason=(reason or "")[:500], scope="chat", active=True, until=until,
                          created_at=datetime.utcnow()))
    invalidate_member(chat_id, target_id)

    if delete_message_id:
        await safe_delete(bot, chat_id, delete_message_id, context="ban_delete")

    await audit_and_log(
        session, bot, chat_id=chat_id, action=action, actor_id=actor.user_id, target_id=target_id,
        actor_name=mention(actor.user_id, await _display_name(session, actor.user_id)),
        target_name=mention(target_id, target_name or await _display_name(session, target_id)),
        chat_title=chat_title, reason=reason,
        duration=format_duration(duration) if duration else "",
    )
    if not ok:
        return "⚠️ عملیات در تلگرام انجام نشد (احتمالاً دسترسی ربات ناکافی است)."
    text = (f"🔨 کاربر {mention(target_id, target_name or await _display_name(session, target_id))} "
            f"{'به‌مدت ' + format_duration(duration) + ' ' if duration else ''}بن شد.")
    if reason:
        text += f"\n📌 دلیل: {reason}"
    return text


async def unban_user(bot: Bot, session: AsyncSession, *, chat_id: int, actor: Actor,
                     target_id: int, target_name: str = "", chat_title: str = "",
                     reason: str = "", source: str = "command") -> str:
    ok = await safe_unban(bot, chat_id, target_id, only_if_banned=True, context="unban")
    await close_active_actions(session, chat_id, target_id, "ban", undone_by=actor.user_id)
    await close_active_actions(session, chat_id, target_id, "temp_ban", undone_by=actor.user_id)
    await record_action(session, chat_id=chat_id, action="unban", target_id=target_id,
                        actor_id=actor.user_id, reason=reason, source=source)
    state = await get_member_state(session, chat_id, target_id)
    state.status = "member"
    state.restricted_until = None
    state.updated_at = datetime.utcnow()
    await session.execute(
        update(BanRecord).where(BanRecord.chat_id == chat_id, BanRecord.user_id == target_id)
        .values(active=False)
    )
    invalidate_member(chat_id, target_id)
    await audit_and_log(
        session, bot, chat_id=chat_id, action="unban", actor_id=actor.user_id, target_id=target_id,
        actor_name=mention(actor.user_id, await _display_name(session, actor.user_id)),
        target_name=mention(target_id, target_name or await _display_name(session, target_id)),
        chat_title=chat_title, reason=reason,
    )
    if not ok:
        return "⚠️ کاربر در حال حاضر بن نبود یا تلگرام اجازه این کار را نداد."
    return (f"🕊 بن کاربر {mention(target_id, target_name or await _display_name(session, target_id))} "
            f"برداشته شد.")


async def kick_user(bot: Bot, session: AsyncSession, *, chat_id: int, actor: Actor,
                    target_id: int, target_name: str = "", reason: str = "",
                    delete_message_id: int | None = None, chat_title: str = "",
                    source: str = "command", sticky: bool = True) -> str:
    """Remove a member from the chat.

    ``sticky=True`` (the default, driven by the ``sticky_ban`` setting) keeps
    the ban in place, so the member can **never** come back through an invite
    link until an admin unbans them.  ``sticky=False`` performs the classic
    kick - ban followed by an immediate unban - which lets the member re-join.
    """
    ok = await safe_ban(bot, chat_id, target_id, context="kick")
    if sticky:
        # The ban stays: Telegram then blocks every re-entry attempt.
        session.add(BanRecord(chat_id=chat_id, user_id=target_id, banned_by=actor.user_id,
                              reason=(reason or "")[:500], scope="chat", active=True,
                              until=None, created_at=datetime.utcnow()))
    else:
        await safe_unban(bot, chat_id, target_id, only_if_banned=False, context="kick_unban")
    await record_action(session, chat_id=chat_id, action="kick", target_id=target_id,
                        actor_id=actor.user_id, reason=reason, source=source)
    state = await get_member_state(session, chat_id, target_id)
    state.status = "left"
    state.updated_at = datetime.utcnow()
    invalidate_member(chat_id, target_id)
    if delete_message_id:
        await safe_delete(bot, chat_id, delete_message_id, context="kick_delete")
    await audit_and_log(
        session, bot, chat_id=chat_id, action="kick", actor_id=actor.user_id, target_id=target_id,
        actor_name=mention(actor.user_id, await _display_name(session, actor.user_id)),
        target_name=mention(target_id, target_name or await _display_name(session, target_id)),
        chat_title=chat_title, reason=reason,
    )
    if not ok:
        return "⚠️ عملیات در تلگرام انجام نشد (احتمالاً دسترسی ربات ناکافی است)."
    who = mention(target_id, target_name or await _display_name(session, target_id))
    if sticky:
        return (f"👢 کاربر {who} از گروه اخراج شد و بن ماندگار شد.\n"
                "🔒 در سوپرگروه، تلگرام تا رفع‌بن توسط مدیر جلوی ورود دوباره با لینک را می‌گیرد؛ "
                "در گروه معمولی این تضمین وجود ندارد.")
    return (f"👢 کاربر {who} از گروه اخراج شد.")


async def enforce_sticky_ban(bot: Bot, session: AsyncSession, chat_id: int,
                             user_id: int) -> bool:
    """Re-ban a member who is still banned but somehow got back in.

    Telegram normally blocks a banned user from re-entering a supergroup, but
    basic groups, admin re-adds and invite-link edge cases can slip through.
    Returns True when the member was removed again.
    """
    from sqlalchemy import select as _select

    record = (await session.execute(
        _select(BanRecord).where(BanRecord.chat_id == chat_id,
                                 BanRecord.user_id == user_id,
                                 BanRecord.active.is_(True)))).scalars().first()
    if record is None:
        return False
    if record.until is not None and record.until <= datetime.utcnow():
        record.active = False
        return False
    removed = await safe_ban(bot, chat_id, user_id, context="sticky_reban")
    if removed:
        logger.info("sticky ban re-applied user=%s chat=%s", user_id, chat_id)
    return bool(removed)


async def has_active_ban(session: AsyncSession, chat_id: int, user_id: int) -> bool:
    """True when ``user_id`` is still banned in ``chat_id``.

    Used to close the *join request* entry path: a banned member must not be
    waved back in by an admin (or by the auto-approve rule) while the ban
    stands.  An expired temporary ban is treated as no ban at all.
    """
    from sqlalchemy import select as _select

    record = (await session.execute(
        _select(BanRecord).where(BanRecord.chat_id == chat_id,
                                 BanRecord.user_id == user_id,
                                 BanRecord.active.is_(True)))).scalars().first()
    if record is None:
        return False
    if record.until is not None and record.until <= datetime.utcnow():
        record.active = False
        return False
    return True


async def mute_user(bot: Bot, session: AsyncSession, *, chat_id: int, actor: Actor,
                    target_id: int, target_name: str = "", duration: int | None = None,
                    reason: str = "", delete_message_id: int | None = None,
                    chat_title: str = "", source: str = "command") -> str:
    until = datetime.utcnow() + timedelta(seconds=duration) if duration else None
    ok = await safe_restrict(bot, chat_id, target_id, MUTE_PERMISSIONS, until_date=until,
                             context="mute")
    action = "temp_mute" if duration else "mute"
    await close_active_actions(session, chat_id, target_id, "mute", undone_by=actor.user_id)
    await close_active_actions(session, chat_id, target_id, "temp_mute", undone_by=actor.user_id)
    await record_action(session, chat_id=chat_id, action=action, target_id=target_id,
                        actor_id=actor.user_id, reason=reason, duration=duration, source=source)
    state = await get_member_state(session, chat_id, target_id)
    state.muted_until = until
    state.status = "restricted"
    state.updated_at = datetime.utcnow()
    invalidate_member(chat_id, target_id)
    if delete_message_id:
        await safe_delete(bot, chat_id, delete_message_id, context="mute_delete")
    await audit_and_log(
        session, bot, chat_id=chat_id, action=action, actor_id=actor.user_id, target_id=target_id,
        actor_name=mention(actor.user_id, await _display_name(session, actor.user_id)),
        target_name=mention(target_id, target_name or await _display_name(session, target_id)),
        chat_title=chat_title, reason=reason,
        duration=format_duration(duration) if duration else "",
    )
    if not ok:
        return "⚠️ عملیات در تلگرام انجام نشد (احتمالاً دسترسی ربات ناکافی است)."
    return (f"🔇 کاربر {mention(target_id, target_name or await _display_name(session, target_id))} "
            f"{'به‌مدت ' + format_duration(duration) + ' ' if duration else ''}بی‌صدا شد.")


async def unmute_user(bot: Bot, session: AsyncSession, *, chat_id: int, actor: Actor,
                      target_id: int, target_name: str = "", chat_title: str = "",
                      reason: str = "", source: str = "command") -> str:
    ok = await safe_restrict(bot, chat_id, target_id, UNMUTE_PERMISSIONS, context="unmute")
    await close_active_actions(session, chat_id, target_id, "mute", undone_by=actor.user_id)
    await close_active_actions(session, chat_id, target_id, "temp_mute", undone_by=actor.user_id)
    await record_action(session, chat_id=chat_id, action="unmute", target_id=target_id,
                        actor_id=actor.user_id, reason=reason, source=source)
    state = await get_member_state(session, chat_id, target_id)
    state.muted_until = None
    state.status = "member"
    state.updated_at = datetime.utcnow()
    invalidate_member(chat_id, target_id)
    await audit_and_log(
        session, bot, chat_id=chat_id, action="unmute", actor_id=actor.user_id, target_id=target_id,
        actor_name=mention(actor.user_id, await _display_name(session, actor.user_id)),
        target_name=mention(target_id, target_name or await _display_name(session, target_id)),
        chat_title=chat_title, reason=reason,
    )
    if not ok:
        return "⚠️ عملیات در تلگرام انجام نشد (احتمالاً دسترسی ربات ناکافی است)."
    return (f"🔊 سکوت کاربر {mention(target_id, target_name or await _display_name(session, target_id))} "
            f"برداشته شد.")


# --------------------------------------------------------------------------- #
# Warnings
# --------------------------------------------------------------------------- #
async def warn_user(bot: Bot, session: AsyncSession, *, chat_id: int, actor: Actor,
                    target_id: int, target_name: str = "", reason: str = "",
                    delete_message_id: int | None = None, chat_title: str = "",
                    source: str = "command") -> tuple[str, dict[str, Any]]:
    """Add a warning and execute the threshold action when the limit is hit."""
    settings_obj = await get_settings(session, chat_id)
    warn_limit = int(settings_obj.warn_limit or 4)
    expires_at = None
    if int(settings_obj.warn_expire_days or 0) > 0:
        expires_at = datetime.utcnow() + timedelta(days=int(settings_obj.warn_expire_days))

    session.add(Warning(chat_id=chat_id, user_id=target_id, moderator_id=actor.user_id,
                        reason=(reason or "بدون دلیل")[:500], created_at=datetime.utcnow(),
                        expires_at=expires_at))
    await record_action(session, chat_id=chat_id, action="warn", target_id=target_id,
                        actor_id=actor.user_id, reason=reason, source=source)

    state = await get_member_state(session, chat_id, target_id)
    state.warn_count = int(state.warn_count or 0) + 1
    state.updated_at = datetime.utcnow()
    invalidate_member(chat_id, target_id)

    count = int(state.warn_count or 0)
    threshold_reached = count >= warn_limit

    if delete_message_id:
        await safe_delete(bot, chat_id, delete_message_id, context="warn_delete")

    await audit_and_log(
        session, bot, chat_id=chat_id, action="warn", actor_id=actor.user_id, target_id=target_id,
        actor_name=mention(actor.user_id, await _display_name(session, actor.user_id)),
        target_name=mention(target_id, target_name or await _display_name(session, target_id)),
        chat_title=chat_title, reason=reason, extra=f"🔢 تعداد اخطار: {count} از {warn_limit}",
    )

    result: dict[str, Any] = {"count": count, "limit": warn_limit, "threshold": threshold_reached,
                              "action": "none"}
    if not threshold_reached:
        text = (f"⚠️ به {mention(target_id, target_name or await _display_name(session, target_id))} "
                f"اخطار داده شد.\n🔢 تعداد اخطار: {count} از {warn_limit}")
        if reason:
            text += f"\n📌 دلیل: {reason}"
        return text, result

    # Threshold reached -> apply the configured action.
    from .warnings import threshold_action

    action = settings_obj.warn_action or "mute"
    duration = settings_obj.warn_action_duration
    text, applied = await threshold_action(
        bot, session, chat_id=chat_id, actor=actor, target_id=target_id,
        target_name=target_name, action=action, duration=duration,
        reason=f"رسیدن به سقف اخطار ({count}/{warn_limit})",
        chat_title=chat_title,
    )
    result["action"] = applied
    await reset_warnings(session, chat_id, target_id, by_id=actor.user_id, auto=True)
    return text, result


async def unwarn_user(bot: Bot, session: AsyncSession, *, chat_id: int, actor: Actor,
                      target_id: int, target_name: str = "", chat_title: str = "") -> str:
    result = await session.execute(
        select(Warning)
        .where(Warning.chat_id == chat_id, Warning.user_id == target_id, Warning.active.is_(True))
        .order_by(Warning.created_at.desc())
        .limit(1)
    )
    warning = result.scalars().first()
    if warning is None:
        return "ℹ️ این کاربر اخطار فعالی ندارد."
    warning.active = False
    warning.removed_at = datetime.utcnow()
    warning.removed_by = actor.user_id
    state = await get_member_state(session, chat_id, target_id)
    state.warn_count = max(0, int(state.warn_count or 0) - 1)
    state.updated_at = datetime.utcnow()
    await record_action(session, chat_id=chat_id, action="unwarn", target_id=target_id,
                        actor_id=actor.user_id, reason="", source="command")
    invalidate_member(chat_id, target_id)
    await audit_and_log(
        session, bot, chat_id=chat_id, action="unwarn", actor_id=actor.user_id, target_id=target_id,
        actor_name=mention(actor.user_id, await _display_name(session, actor.user_id)),
        target_name=mention(target_id, target_name or await _display_name(session, target_id)),
        chat_title=chat_title,
    )
    return (f"🗑 یک اخطار از {mention(target_id, target_name or await _display_name(session, target_id))} "
            f"کم شد.\n🔢 تعداد فعلی: {state.warn_count}")


async def reset_warnings(session: AsyncSession, chat_id: int, user_id: int, *,
                         by_id: int | None = None, auto: bool = False) -> int:
    result = await session.execute(
        select(Warning).where(Warning.chat_id == chat_id, Warning.user_id == user_id,
                              Warning.active.is_(True))
    )
    count = 0
    for warning in result.scalars().all():
        warning.active = False
        warning.removed_at = datetime.utcnow()
        warning.removed_by = by_id
        count += 1
    state = await get_member_state(session, chat_id, user_id)
    state.warn_count = 0
    state.updated_at = datetime.utcnow()
    await session.flush()
    invalidate_member(chat_id, user_id)
    if not auto and count:
        session.add(ModerationAction(
            chat_id=chat_id, target_id=user_id, actor_id=by_id, action="reset_warn",
            reason="", created_at=datetime.utcnow(), active=False, source="command"))
    return count


async def warn_status(session: AsyncSession, chat_id: int, user_id: int) -> tuple[int, int, list[Warning]]:
    settings_obj = await get_settings(session, chat_id)
    limit = int(settings_obj.warn_limit or 4)
    result = await session.execute(
        select(Warning).where(Warning.chat_id == chat_id, Warning.user_id == user_id)
        .order_by(Warning.created_at.desc()).limit(20)
    )
    records = list(result.scalars().all())
    state = await get_member_state(session, chat_id, user_id, create=False)
    count = int(state.warn_count or 0) if state else sum(1 for w in records if w.active)
    return count, limit, records


async def active_warn_count(session: AsyncSession, chat_id: int, user_id: int) -> int:
    result = await session.execute(
        select(func.count(Warning.id)).where(Warning.chat_id == chat_id, Warning.user_id == user_id,
                                             Warning.active.is_(True))
    )
    return int(result.scalar() or 0)


# --------------------------------------------------------------------------- #
# Shared action engine (used by locks / filters / antiflood / warnings)
# --------------------------------------------------------------------------- #
async def apply_action(bot: Bot, session: AsyncSession, *, chat_id: int, target_id: int,
                       actor: Actor | None, action: str, reason: str = "",
                       duration: int | None = None, target_name: str = "",
                       message_id: int | None = None, chat_title: str = "",
                       source: str = "auto") -> str:
    """Execute a punishment action by name (``delete``/``warn``/``mute``/...)."""
    from .permissions import Actor as ActorType  # local import to avoid cycles

    actor = actor or ActorType(user_id=bot.id, chat_id=chat_id)
    action = (action or "none").strip().lower()
    delete_first = action.startswith("delete_")

    if delete_first and message_id:
        await safe_delete(bot, chat_id, message_id, context="auto_delete")
        action = action.replace("delete_", "", 1) or "delete"

    if action == "none":
        return ""
    if action == "delete":
        await safe_delete(bot, chat_id, message_id, context="auto_delete")
        await record_action(session, chat_id=chat_id, action="delete", target_id=target_id,
                            actor_id=actor.user_id, reason=reason, source=source)
        return "🗑 پیام حذف شد."
    if action == "warn":
        text, _ = await warn_user(bot, session, chat_id=chat_id, actor=actor, target_id=target_id,
                                  target_name=target_name, reason=reason, chat_title=chat_title,
                                  source=source)
        return text
    if action in {"mute", "temp_mute"}:
        return await mute_user(bot, session, chat_id=chat_id, actor=actor, target_id=target_id,
                               target_name=target_name,
                               duration=duration if action == "temp_mute" else None,
                               reason=reason, chat_title=chat_title, source=source)
    if action == "kick":
        return await kick_user(bot, session, chat_id=chat_id, actor=actor, target_id=target_id,
                               target_name=target_name, reason=reason, chat_title=chat_title,
                               source=source)
    if action in {"ban", "temp_ban"}:
        return await ban_user(bot, session, chat_id=chat_id, actor=actor, target_id=target_id,
                              target_name=target_name,
                              duration=duration if action == "temp_ban" else None,
                              reason=reason, chat_title=chat_title, source=source)
    return ""


async def user_history(session: AsyncSession, chat_id: int, user_id: int, limit: int = 15) -> list[ModerationAction]:
    result = await session.execute(
        select(ModerationAction)
        .where(ModerationAction.chat_id == chat_id, ModerationAction.target_id == user_id)
        .order_by(ModerationAction.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def clear_history(session: AsyncSession, chat_id: int, user_id: int) -> int:
    result = await session.execute(
        delete(ModerationAction).where(ModerationAction.chat_id == chat_id,
                                       ModerationAction.target_id == user_id)
    )
    await session.execute(
        delete(ChatMemberState).where(ChatMemberState.chat_id == chat_id,
                                      ChatMemberState.user_id == user_id)
    )
    return int(result.rowcount or 0)


def history_text(actions: list[ModerationAction], title: str = "📜 تاریخچه کاربر") -> str:
    if not actions:
        return f"{title}\n\nهیچ سابقه‌ای ثبت نشده است."
    lines = [title, ""]
    for item in actions:
        label = ACTION_LABELS_FA.get(item.action, item.action)
        extra = format_duration(item.duration) if item.duration else ""
        lines.append(
            f"• {label} | {persian_datetime(item.created_at)}"
            + (f" | ⏱ {extra}" if extra else "")
            + (f" | 📌 {item.reason}" if item.reason else "")
        )
    return "\n".join(lines)
