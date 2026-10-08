"""Interactive panel: rendering, navigation, toggles and confirmations.

Callback data is never trusted: every callback re-resolves the actor from
Telegram and checks both the internal role and the real Telegram capability.
"""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.types import (CallbackQuery, ChatMemberAdministrator, ChatMemberOwner,
                           Message)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import cache
from ..core.errors import safe_answer, safe_call, safe_delete, safe_edit
from ..core.normalization import to_persian_digits
from ..db.models import (
    BotRole,
    Chat,
    ChatMemberState,
    Federation,
    FilterRule,
    ModerationAction,
    Note,
    PersonalCommand,
    Report,
    ScheduledMessage,
    TrustedUser,
    Warning,
)
from ..keyboards.factory import cb, confirm, markup, parse_cb, primary, row
from ..keyboards.menus import (
    BOT_NAME,
    ROLE_LABELS,
    build_panel,
    confirm_text,
    help_text,
    with_creator,
)
from ..services import connection as connection_service
from ..services import disabled as disabled_service
from ..services import filters as filter_service
from ..services import locks as lock_service
from ..services import notes as note_service
from ..services import permissions
from ..services import personal as personal_service
from ..services import reports as report_service
from ..services import tags as tag_service
from ..services.chat_state import get_settings, get_settings_cached, invalidate_settings
from ..services.roles import level_of

router = Router(name="panel")
logger = logging.getLogger("armando.panel")

COMMAND_HINTS: dict[str, str] = {
    "ban": "🔨 برای بن کردن، روی پیام کاربر <b>ریپلای</b> کنید و بنویسید:\n<code>بن</code> یا <code>بن ۲روز تبلیغ</code>",
    "mute": "🔇 روی پیام کاربر ریپلای کنید و بنویسید:\n<code>سکوت</code> یا <code>سکوت ۳۰دقیقه</code>",
    "warn": "⚠️ روی پیام کاربر ریپلای کنید و بنویسید:\n<code>اخطار دلیل</code>",
    "kick": "👢 روی پیام کاربر ریپلای کنید و بنویسید:\n<code>کیک</code>",
    "unban": "🕊 بنویسید: <code>رفع بن @username</code> یا روی پیام کاربر ریپلای کنید.",
    "unmute": "🔊 روی پیام کاربر ریپلای کنید و بنویسید:\n<code>لغو سکوت</code>",
    "unwarn": "🗑 روی پیام کاربر ریپلای کنید و بنویسید:\n<code>کسر اخطار</code>",
    "resetwarn": "♻️ روی پیام کاربر ریپلای کنید و بنویسید:\n<code>صفر کردن اخطار</code>",
    "history": "📜 روی پیام کاربر ریپلای کنید و بنویسید:\n<code>تاریخچه</code>",
    "info": "ℹ️ روی پیام کاربر ریپلای کنید و بنویسید:\n<code>اطلاعات</code>",
    "me": "👤 بنویسید: <code>آمار من</code> یا <code>اطلاعات</code>",
    "myrep": "⭐️ بنویسید: <code>امتیاز من</code>",
    "afkhelp": "💤 برای رفتن به حالت AFK بنویسید: <code>برم افک دلیل</code>\nبرای بازگشت: <code>برگشتم</code>",
    "stats": "📊 بنویسید: <code>آمار</code>",
    "top": "🏆 بنویسید: <code>فعال‌ترین‌ها ۳۰</code>",
    "inactive": "💤 بنویسید: <code>غیرفعال‌ها ۳۰</code>",
    "trend": "📈 بنویسید: <code>روند گروه</code>",
    "rules": "📜 بنویسید: <code>قوانین</code>",
    "setrules": "✏️ بنویسید: <code>تنظیم قوانین ۱. احترام ۲. بدون تبلیغ</code>",
    "admins": "👮 بنویسید: <code>لیست مدیران</code>",
    "adminperm": ("🎚 روی پیام آن مدیر <b>ریپلای</b> کنید و بنویسید:\n"
                  "<code>دسترسی مدیر</code>\n"
                  "سپس هر دسترسی را با دکمه‌هایش فعال/غیرفعال کنید.\n"
                  "عزل: <code>تنزل</code> • ارتقا: <code>ارتقا</code>"),
    "stafflist": "🛡 بنویسید: <code>لیست کادر</code>",
    "trustedlist": "⭐️ بنویسید: <code>لیست ویژه‌ها</code>",
    "addtrusted": "➕ روی پیام کاربر ریپلای کنید و بنویسید:\n<code>افزودن ویژه</code>",
    "rmtrusted": "➖ روی پیام کاربر ریپلای کنید و بنویسید:\n<code>عزل ویژه</code>",
    "taglist": "🏷 بنویسید: <code>لیست تگ‌ها</code>",
    "settag": "🏷 روی پیام کاربر ریپلای کنید و بنویسید:\n<code>تگ متن تگ</code>",
    "deltag": "🗑 روی پیام کاربر ریپلای کنید و بنویسید:\n<code>حذف تگ</code>",
    "listfilters": "🚫 بنویسید: <code>لیست فیلتر</code>",
    "listnotes": "📒 بنویسید: <code>لیست یادداشت‌ها</code>",
    "listcommands": "⌨️ بنویسید: <code>لیست دستورات</code>",
    "listmagic": "🎴 بنویسید: <code>لیست استیکرها</code>",
    "pin": "📌 روی پیام ریپلای کنید و بنویسید: <code>پین</code>",
    "unpin": "📍 روی پیام پین‌شده ریپلای کنید و بنویسید: <code>حذف پین</code>",
    "getpin": "📌 بنویسید: <code>پین فعلی</code>",
    "purge100": "🧹 بنویسید: <code>پاکسازی ۱۰۰</code>",
    "setlog": "📜 در گروه/کانال مقصد بنویسید: <code>تنظیم لاگ</code>",
    "listschedule": "⏰ بنویسید: <code>لیست زمان‌بندی</code>",
    "addschedule": "➕ بنویسید: <code>زمان‌بندی روزانه ۰۹:۰۰ متن پیام</code>",
    "backup": "💾 بنویسید: <code>پشتیبان‌گیری</code>",
    "restorehelp": "♻️ فایل پشتیبان را ریپلای کنید و بنویسید: <code>بازیابی</code>",
    "openreports": "📢 بنویسید: <code>گزارش‌ها</code>",
    "joke": "😂 بنویسید: <code>جوک</code>",
    "fal": "🔮 بنویسید: <code>فال حافظ</code>",
    "coin": "🪙 بنویسید: <code>شیر یا خط</code>",
    "dice": "🎲 بنویسید: <code>تاس</code>",
    "slot": "🎰 بنویسید: <code>اسلات</code>",
    "rand": "🔢 بنویسید: <code>عدد تصادفی ۱ ۱۰۰</code>",
    "game": "🎮 بنویسید: <code>بازی</code> سپس عدد حدس خود را ارسال کنید.",
    "currency": "💱 بنویسید: <code>ارز</code> یا <code>قیمت دلار</code>",
    "crypto": "🪙 بنویسید: <code>قیمت بیت‌کوین</code> یا <code>ارز دیجیتال</code>",
    "gold": "🥇 بنویسید: <code>طلا</code> یا <code>سکه</code>",
    "bourse": "📈 بنویسید: <code>بورس</code>",
    "date": "📅 بنویسید: <code>تاریخ</code>",
    "time": "🕒 بنویسید: <code>ساعت</code>",
    "nighthelp": "🌙 تنظیم بازه:\n<code>حالت شب شروع ۲۳:۳۰</code>\n<code>حالت شب پایان ۰۷:۰۰</code>",
    "fedinfo": "🌐 بنویسید: <code>اطلاعات فدراسیون</code>",
    "fbanlist": "🚫 بنویسید: <code>لیست بن فدراسیون</code>",
    "fedcreate": "➕ بنویسید: <code>ساخت فدراسیون نام</code>",
    "fedjoin": "🔗 بنویسید: <code>اتحاد فدراسیون نام</code>",
}


# --------------------------------------------------------------------------- #
# Context building
# --------------------------------------------------------------------------- #
async def _prepare_ctx(session: AsyncSession, bot: Bot, chat_id: int, user_id: int,
                       actor, settings: dict, section: str, **extra) -> dict:
    ctx: dict = {
        "level": actor.role_level if actor else level_of("member"),
        "settings": settings,
        "chat_id": chat_id,
        "chat_title": extra.get("chat_title", ""),
        "chat_type": extra.get("chat_type", "group"),
        "role_label": ROLE_LABELS.get(_role_key(actor), "👤 عضو") if actor else "👤 عضو",
        **extra,
    }
    if section in {"locks", "lock", "security"}:
        ctx["locks"] = await lock_service.get_locks_map(session, chat_id)
    if section == "lock":
        ctx["lock_key"] = extra.get("lock_key")
        ctx["lock_page"] = extra.get("lock_page", "links")
    if section == "locks":
        ctx["lock_page"] = extra.get("lock_page", "links")
        from ..services import chatlock

        ctx["chat_lock"] = await chatlock.chat_lock_state(bot, chat_id)
    if section == "filters":
        rules = await filter_service.get_rules(session, chat_id)
        notes = await note_service.list_notes(session, chat_id)
        commands = await personal_service.list_commands(session, chat_id)
        ctx["filter_counts"] = {
            "blocklist": sum(1 for r in rules if r.is_blocklist),
            "custom": sum(1 for r in rules if not r.is_blocklist),
            "notes": len(notes),
            "commands": len(commands),
        }
    if section == "members":
        staff = await session.execute(
            select(BotRole).where(BotRole.chat_id == chat_id))
        trusted = await session.execute(
            select(TrustedUser).where(TrustedUser.chat_id == chat_id))
        tags = await tag_service.tags_of_chat(session, chat_id)
        ctx["member_counts"] = {
            "admins": extra.get("admin_count", 0),
            "staff": len(list(staff.scalars().all())),
            "trusted": len(list(trusted.scalars().all())),
            "tags": len(tags),
        }
    if section == "staff":
        result = await session.execute(select(BotRole).where(BotRole.chat_id == chat_id))
        rows = []
        for item in result.scalars().all():
            from ..db.models import User

            user = await session.get(User, item.user_id)
            name = f"{user.first_name or ''} {user.last_name or ''}".strip() if user else str(item.user_id)
            rows.append((item.user_id, item.role, name or str(item.user_id)))
        ctx["staff"] = rows
    if section == "trust":
        result = await session.execute(
            select(TrustedUser).where(TrustedUser.chat_id == chat_id))
        rows = []
        for item in result.scalars().all():
            from ..db.models import User

            user = await session.get(User, item.user_id)
            name = f"{user.first_name or ''} {user.last_name or ''}".strip() if user else str(item.user_id)
            rows.append((item.user_id, name or str(item.user_id)))
        ctx["trusted"] = rows
    if section == "tags":
        rows = await tag_service.tags_of_chat(session, chat_id)
        enriched = []
        from ..db.models import User

        for user_id_, tag in rows:
            user = await session.get(User, user_id_)
            name = f"{user.first_name or ''} {user.last_name or ''}".strip() if user else str(user_id_)
            enriched.append((user_id_, name or str(user_id_), tag))
        ctx["tags"] = enriched
    if section == "schedule":
        result = await session.execute(
            select(ScheduledMessage).where(ScheduledMessage.chat_id == chat_id).limit(15))
        ctx["scheduled"] = [f"#{r.id} - {r.text[:30]}" for r in result.scalars().all()]
    if section == "federation":
        fed_id = settings.get("federation_id")
        if fed_id:
            from ..services import federation as fed_service

            fed = await fed_service.get_fed_by_id(session, int(fed_id))
            if fed:
                ctx["federation"] = {
                    "name": fed.name,
                    "admins": len(await fed_service.fed_admins(session, fed)),
                    "chats": len(await fed_service.fed_chats(session, fed)),
                    "bans": len(await fed_service.fban_list(session, fed)),
                    "mode": "اجرای خودکار" if fed.ban_mode == "enforce" else "اشتراک‌گذاری",
                }
    if section == "reports":
        open_count = await session.scalar(
            select(func.count(Report.id)).where(Report.chat_id == chat_id,
                                                Report.status == "open")) or 0
        total = await session.scalar(
            select(func.count(Report.id)).where(Report.chat_id == chat_id)) or 0
        ctx["report_counts"] = {"open": int(open_count), "total": int(total)}
    if section == "profile":
        from ..db.models import User
        from ..services.reputation import get_score

        state_result = await session.execute(
            select(ChatMemberState).where(ChatMemberState.chat_id == chat_id,
                                          ChatMemberState.user_id == user_id))
        state = state_result.scalar_one_or_none()
        user = await session.get(User, user_id)
        warns = await session.scalar(
            select(func.count(Warning.id)).where(Warning.chat_id == chat_id,
                                                 Warning.user_id == user_id,
                                                 Warning.active.is_(True))) or 0
        positive, negative, score = await get_score(session, chat_id, user_id)
        ctx["profile"] = {
            "user_id": user_id,
            "name": f"{user.first_name or ''} {user.last_name or ''}".strip() if user else "—",
            "username": user.username if user else None,
            "role": ROLE_LABELS.get(_role_key(actor), "👤 عضو") if actor else "👤 عضو",
            "tag": state.tag if state else None,
            "messages": int(state.message_count or 0) if state else 0,
            "score": int(score),
            "warns": int(warns),
        }
    return ctx


async def send_panel(bot: Bot, chat_id: int, user_id: int, session: AsyncSession, *,
                     message: Message | None = None, source_chat_id: int | None = None,
                     section: str = "main", **extra) -> None:
    """Send (or edit) the role-aware panel."""
    source_chat_id = source_chat_id or chat_id
    target_chat_id = chat_id
    if (message is not None and message.chat.type == "private") or (
            message is None and chat_id == user_id):
        connected = await connection_service.get_active(session, user_id)
        if connected:
            target_chat_id = connected
        elif section == "main":
            await bot.send_message(
                chat_id=chat_id,
                text="🔗 اتصال فعالی وجود ندارد.\n"
                     "ابتدا در گروه دستور <code>پنل</code> را بزنید، سپس در این چت خصوصی "
                     "دستور <code>اتصال</code> را اجرا کنید.",
                parse_mode=ParseMode.HTML)
            return

    actor = await permissions.build_actor(bot, target_chat_id, user_id, session=session)
    if target_chat_id < 0 and not _can_manage(actor):
        # Ordinary members never see the management panel - they get a small
        # personal card instead.
        await _send_member_panel(bot, chat_id, user_id, session, target_chat_id=target_chat_id)
        return
    settings = await get_settings_cached(session, target_chat_id)
    chat = await session.get(Chat, target_chat_id)
    ctx = await _prepare_ctx(session, bot, target_chat_id, user_id, actor, settings, section,
                             chat_title=(chat.title if chat else ""), chat_type="group", **extra)
    text, keyboard = build_panel(section, ctx)
    sent = await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard,
                                  parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    if sent is not None:
        remember_panel_owner(chat_id, sent.message_id, user_id)


PANEL_TTL = 24 * 60 * 60  # a day is plenty for an interactive panel


def remember_panel_owner(chat_id: int, message_id: int, user_id: int) -> None:
    """Bind a panel message to the admin who opened it."""
    cache.panel_owner_cache.set((chat_id, message_id), int(user_id))


def _panel_owner(chat_id: int, message_id: int) -> int | None:
    owner = cache.panel_owner_cache.get((chat_id, message_id))
    return int(owner) if owner is not None else None


def _role_key(actor) -> str:
    """The actor's role, Telegram admin status included."""
    return (actor.effective_role if hasattr(actor, "effective_role") else actor.bot_role) or "member"


def _can_manage(actor) -> bool:
    """Group management is reserved for Telegram admins and promoted staff."""
    from ..services.roles import Role, level_of

    return actor.is_telegram_admin or actor.role_level >= level_of("helper")


async def _send_member_panel(bot: Bot, chat_id: int, user_id: int, session: AsyncSession,
                             *, target_chat_id: int) -> None:
    """A small personal card for members (no group management at all)."""
    from ..core.normalization import to_persian_digits
    from ..services.chat_state import get_member_state_snapshot

    state = await get_member_state_snapshot(session, target_chat_id, user_id)
    chat = await session.get(Chat, target_chat_id)
    warns = int(state.get("warn_count") or 0)
    reputation = int(state.get("reputation_total") or 0)
    tag = state.get("tag") or "—"
    lines = [
        f"🤖 <b>{BOT_NAME}</b>",
        "",
        f"💬 گروه: {chat.title if chat else target_chat_id}",
        f"🆔 شناسه شما: <code>{user_id}</code>",
        f"🎖 سمت شما: {ROLE_LABELS.get('member', 'عضو')}",
        f"🏷 تگ شما: <code>{tag}</code>",
        f"⚠️ اخطارها: {to_persian_digits(str(warns))}",
        f"⭐️ اعتبار: {to_persian_digits(str(reputation))}",
        "",
        "ℹ️ پنل مدیریت گروه فقط برای مدیران و مالک گروه نمایش داده می‌شود.",
        "برای دیدن دستورات آزاد بنویسید: <code>راهنما</code>",
    ]
    await bot.send_message(chat_id=chat_id, text="\n".join(lines),
                           parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def _refresh(callback: CallbackQuery, session: AsyncSession, bot: Bot, chat_id: int,
                   section: str, **extra) -> None:
    actor = await permissions.build_actor(bot, chat_id, callback.from_user.id, session=session)
    settings = await get_settings_cached(session, chat_id)
    chat = await session.get(Chat, chat_id)
    ctx = await _prepare_ctx(session, bot, chat_id, callback.from_user.id, actor, settings,
                             section, chat_title=(chat.title if chat else ""),
                             chat_type=callback.message.chat.type if callback.message else "group",
                             **extra)
    text, keyboard = build_panel(section, ctx)
    edited = await safe_edit(callback.message, text, reply_markup=keyboard)
    if edited is not None and callback.message is not None:
        remember_panel_owner(chat_id, callback.message.message_id, callback.from_user.id)


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #
@router.callback_query(F.data == "noop")
async def on_noop(callback: CallbackQuery) -> None:
    await safe_answer(callback)


@router.callback_query(F.data.startswith("nav:"))
async def on_nav(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    parts = parse_cb(callback.data or "")
    action = parts[1] if len(parts) > 1 else "home"
    if action == "close":
        await safe_answer(callback)
        if callback.message:
            await safe_delete(bot, callback.message.chat.id, callback.message.message_id,
                              context="panel_close")
        return
    if action == "home":
        await safe_answer(callback)
        await _refresh(callback, session, bot, await _panel_chat_id(callback, session), "main")
        return
    await safe_answer(callback)
    await _refresh(callback, session, bot, await _panel_chat_id(callback, session), parts[2] if len(parts) > 2 else "main")


async def _panel_chat_id(callback: CallbackQuery, session: AsyncSession | None = None) -> int:
    """Resolve the group a panel message is controlling.

    A panel opened in a private chat acts on the **connected** group, so the
    chat id must be resolved through the connection table - never taken from
    the private chat itself.
    """
    if not callback.message:
        return callback.from_user.id
    if callback.message.chat.type == "private" and session is not None:
        connected = await connection_service.get_active(session, callback.from_user.id)
        if connected:
            return connected
    return callback.message.chat.id


@router.callback_query(F.data.startswith("panel:"))
async def on_panel(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    parts = parse_cb(callback.data or "")
    section = parts[1] if len(parts) > 1 else "main"
    await safe_answer(callback)
    await _refresh(callback, session, bot, await _panel_chat_id(callback, session), section)


@router.callback_query(F.data.startswith("lockpage:"))
async def on_lock_page(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    parts = parse_cb(callback.data or "")
    page = parts[1] if len(parts) > 1 else "links"
    await safe_answer(callback)
    await _refresh(callback, session, bot, await _panel_chat_id(callback, session), "locks", lock_page=page)


@router.callback_query(F.data.startswith("lock:"))
async def on_lock(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    parts = parse_cb(callback.data or "")
    if len(parts) < 3:
        await safe_answer(callback)
        return
    action, key = parts[1], parts[2]
    page = parts[3] if len(parts) > 3 else "links"
    chat_id = await _panel_chat_id(callback, session)
    if not await _ensure_admin(callback, bot, chat_id, session):
        return
    await safe_answer(callback)
    if action == "toggle":
        await lock_service.toggle_lock(session, chat_id, key, updated_by=callback.from_user.id)
        if key in {"profanity", "porn"}:
            settings_obj = await get_settings(session, chat_id)
            locks = await lock_service.get_locks_map(session, chat_id)
            enabled = bool(locks.get(key) and locks[key].enabled)
            if key == "profanity":
                settings_obj.anti_profanity_enabled = enabled
            else:
                settings_obj.anti_porn_enabled = enabled
            invalidate_settings(chat_id)
    await _refresh(callback, session, bot, chat_id, "locks", lock_page=page)


@router.callback_query(F.data.startswith("lockact:"))
async def on_lock_action(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    parts = parse_cb(callback.data or "")
    # lockact:<key>:<action>:<page>
    if len(parts) < 3:
        await safe_answer(callback)
        return
    key, action = parts[1], parts[2]
    page = parts[3] if len(parts) > 3 else "links"
    chat_id = await _panel_chat_id(callback, session)
    if not await _ensure_admin(callback, bot, chat_id, session):
        return
    await safe_answer(callback)
    locks = await lock_service.get_locks_map(session, chat_id)
    state = locks.get(key)
    await lock_service.set_lock(session, chat_id, key, True, action=action,
                                duration=(state.duration if state else None),
                                updated_by=callback.from_user.id)
    await _refresh(callback, session, bot, chat_id, "locks", lock_page=page)


@router.callback_query(F.data.startswith("set:"))
async def on_set(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    parts = parse_cb(callback.data or "")
    # set:<field>:<value>[:<panel>]
    if len(parts) < 3:
        await safe_answer(callback)
        return
    field, raw_value = parts[1], parts[2]
    panel = parts[3] if len(parts) > 3 else "settings"
    chat_id = await _panel_chat_id(callback, session)
    if not await _ensure_admin(callback, bot, chat_id, session):
        return
    settings_obj = await get_settings(session, chat_id)
    if not hasattr(settings_obj, field):
        await safe_answer(callback, "⛔️ تنظیم نامعتبر است.", show_alert=True)
        return
    current = getattr(settings_obj, field)
    if isinstance(current, bool):
        value = raw_value.lower() in {"1", "true", "yes", "on"}
    elif isinstance(current, int):
        try:
            value = int(raw_value)
        except ValueError:
            await safe_answer(callback, "⛔️ مقدار نامعتبر.", show_alert=True)
            return
    else:
        value = raw_value
    setattr(settings_obj, field, value)
    settings_obj.updated_at = datetime.utcnow()
    invalidate_settings(chat_id)
    await session.flush()
    await safe_answer(callback, "✅ ذخیره شد.")
    await _refresh(callback, session, bot, chat_id, panel)


@router.callback_query(F.data.startswith("num:"))
async def on_number(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    parts = parse_cb(callback.data or "")
    # num:<field>:<delta>:<panel>:<min>:<max>
    if len(parts) < 4:
        await safe_answer(callback)
        return
    field, delta = parts[1], int(parts[2])
    panel = parts[3]
    minimum = int(parts[4]) if len(parts) > 4 else 0
    maximum = int(parts[5]) if len(parts) > 5 else 9999
    chat_id = await _panel_chat_id(callback, session)
    if not await _ensure_admin(callback, bot, chat_id, session):
        return
    settings_obj = await get_settings(session, chat_id)
    current = int(getattr(settings_obj, field, 0) or 0)
    value = max(minimum, min(maximum, current + delta))
    setattr(settings_obj, field, value)
    invalidate_settings(chat_id)
    await session.flush()
    await safe_answer(callback, f"✅ {to_persian_digits(str(value))}")
    await _refresh(callback, session, bot, chat_id, panel)


@router.callback_query(F.data.startswith("dis:"))
async def on_disabled_toggle(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    parts = parse_cb(callback.data or "")
    key = parts[1] if len(parts) > 1 else ""
    chat_id = await _panel_chat_id(callback, session)
    if not await _ensure_admin(callback, bot, chat_id, session):
        return
    await safe_answer(callback)
    if await disabled_service.is_disabled(session, chat_id, key):
        await disabled_service.enable(session, chat_id, key)
    else:
        await disabled_service.disable(session, chat_id, key)
    await _refresh(callback, session, bot, chat_id, "disabled")


@router.callback_query(F.data.startswith("conf:"))
async def on_confirm(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    parts = parse_cb(callback.data or "")
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else "all"
    chat_id = await _panel_chat_id(callback, session)
    if not await _ensure_admin(callback, bot, chat_id, session):
        return
    await safe_answer(callback)
    keyboard = confirm(cb("do", action, arg), cb("nav", "home"),
                       target="main")
    await safe_edit(callback.message, f"{confirm_text(action)}\n\n⚠️ این عملیات قابل بازگشت نیست.",
                    reply_markup=keyboard)


@router.callback_query(F.data.startswith("do:"))
async def on_do(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    from ..services import backup as backup_service

    parts = parse_cb(callback.data or "")
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else "all"
    chat_id = await _panel_chat_id(callback, session)
    if not await _ensure_admin(callback, bot, chat_id, session):
        return
    await safe_answer(callback)
    done = "✅ انجام شد."

    if action == "clearwarns":
        from ..services import moderation as mod

        result = await session.execute(
            select(Warning).where(Warning.chat_id == chat_id, Warning.active.is_(True)))
        count = 0
        for row in result.scalars().all():
            row.active = False
            row.removed_at = datetime.utcnow()
            row.removed_by = callback.from_user.id
            count += 1
        await session.execute(
            select(ChatMemberState).where(ChatMemberState.chat_id == chat_id))
        await session.flush()
        from ..services.chat_state import get_member_state

        states = await session.execute(
            select(ChatMemberState).where(ChatMemberState.chat_id == chat_id))
        for state in states.scalars().all():
            state.warn_count = 0
        await session.flush()
        done = f"🧹 {to_persian_digits(str(count))} اخطار پاک شد."
    elif action == "clearfilters":
        count = await filter_service.clear_rules(session, chat_id, blocklist=True, custom=False)
        done = f"🧹 {to_persian_digits(str(count))} فیلتر حذف شد."
    elif action == "clearnotes":
        count = await note_service.clear_notes(session, chat_id)
        done = f"🧹 {to_persian_digits(str(count))} یادداشت حذف شد."
    elif action == "clearlocks":
        count = await lock_service.clear_locks(session, chat_id)
        done = f"🧹 {to_persian_digits(str(count))} قفل غیرفعال شد."
    elif action == "unpinall":
        from ..services import pins as pin_service

        ok = await pin_service.unpin_all(bot, chat_id)
        done = "🧹 همه پین‌ها حذف شدند." if ok else "⚠️ عملیات انجام نشد."
    elif action == "delrules":
        settings_obj = await get_settings(session, chat_id)
        settings_obj.rules_text = ""
        invalidate_settings(chat_id)
        done = "🗑 قوانین حذف شد."
    elif action == "clearschedule":
        result = await session.execute(
            select(ScheduledMessage).where(ScheduledMessage.chat_id == chat_id))
        count = 0
        for row in result.scalars().all():
            await session.delete(row)
            count += 1
        await session.flush()
        done = f"🗑 {to_persian_digits(str(count))} زمان‌بندی حذف شد."
    elif action == "resetsettings":
        settings_obj = await get_settings(session, chat_id)
        from ..db.models import ChatSettings

        for column in ChatSettings.__table__.columns:  # type: ignore[attr-defined]
            if column.name in {"chat_id", "created_at", "updated_at"}:
                continue
            setattr(settings_obj, column.name, column.default.arg if column.default else None)
        invalidate_settings(chat_id)
        done = "♻️ تنظیمات به حالت پیش‌فرض بازگشت."
    elif action == "fedleave":
        from ..services import federation as fed_service

        await fed_service.leave_fed(session, chat_id)
        done = "🚪 گروه از فدراسیون خارج شد."
    elif action == "ban":
        from ..services import moderation as mod
        from ..services.targeting import html_user

        user_id = int(arg) if arg.isdigit() else 0
        if user_id:
            actor = await permissions.build_actor(bot, chat_id, callback.from_user.id,
                                                  session=session)
            await mod.ban_user(bot, session, chat_id=chat_id, actor=actor, target_id=user_id,
                               target_name=str(user_id), reason="تأیید از پنل")
            done = "🔨 کاربر بن شد."
    else:
        done = "ℹ️ عملیات نامشخص است."

    await safe_edit(callback.message, done, reply_markup=markup([row(primary("🏠 خانه", cb("nav", "home")))]))


@router.callback_query(F.data.startswith("w:"))
async def on_warn_action(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    parts = parse_cb(callback.data or "")
    if len(parts) < 3:
        await safe_answer(callback)
        return
    action, raw_user = parts[1], parts[2]
    user_id = int(raw_user) if raw_user.isdigit() else 0
    chat_id = await _panel_chat_id(callback, session)
    actor = await permissions.build_actor(bot, chat_id, callback.from_user.id, session=session)
    if not (actor.can_ban() or actor.can_restrict() or actor.has_role("moderator")):
        await safe_answer(callback, "⛔️ شما اجازه این کار را ندارید.", show_alert=True)
        return
    if not user_id:
        await safe_answer(callback)
        return
    await safe_answer(callback, "⏳ ...")
    from ..services import moderation as mod

    if action == "warn":
        text, _ = await mod.warn_user(bot, session, chat_id=chat_id, actor=actor,
                                      target_id=user_id, target_name=str(user_id),
                                      reason="اخطار از پنل")
    elif action == "unwarn":
        text = await mod.unwarn_user(bot, session, chat_id=chat_id, actor=actor,
                                     target_id=user_id, target_name=str(user_id))
    elif action == "reset":
        count = await mod.reset_warnings(session, chat_id, user_id, by_id=callback.from_user.id)
        text = f"♻️ {to_persian_digits(str(count))} اخطار پاک شد."
    elif action == "history":
        actions = await mod.user_history(session, chat_id, user_id)
        text = mod.history_text(actions)
    else:
        text = "ℹ️ عملیات نامشخص است."
    await safe_edit(callback.message, text)


@router.callback_query(F.data.startswith("locknum:"))
async def on_lock_number(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """Change the threshold of a lock (e.g. the 1000 character limit)."""
    parts = parse_cb(callback.data or "")
    # locknum:<key>:<field>:<delta>[:<page>]
    if len(parts) < 4:
        await safe_answer(callback)
        return
    key, field, raw_delta = parts[1], parts[2], parts[3]
    page = parts[4] if len(parts) > 4 else "links"
    try:
        delta = int(raw_delta)
    except ValueError:
        await safe_answer(callback)
        return
    chat_id = await _panel_chat_id(callback, session)
    if not await _ensure_admin(callback, bot, chat_id, session):
        return
    spec = lock_service.LOCK_REGISTRY.get(key)
    if spec is None or not spec.threshold:
        await safe_answer(callback)
        return
    locks = await lock_service.get_locks_map(session, chat_id)
    state = locks.get(key)
    current = int((state.extra or {}).get(field, spec.default_threshold)) if state \
        else spec.default_threshold
    value = max(1, min(100000, current + delta))
    await lock_service.set_lock(session, chat_id, key,
                                bool(state.enabled) if state else True,
                                action=(state.action if state else None),
                                extra={field: value}, updated_by=callback.from_user.id)
    await safe_answer(callback, f"✅ {to_persian_digits(str(value))}")
    await _refresh(callback, session, bot, chat_id, "lock", lock_key=key, lock_page=page)


@router.callback_query(F.data.startswith("lg:"))
async def on_leave_guard(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """Disable a «قفل خروج» rule straight from the ban notice."""
    from ..services.chat_state import get_settings, invalidate_settings

    parts = parse_cb(callback.data or "")
    if len(parts) < 3:
        await safe_answer(callback)
        return
    action, field = parts[1], parts[2]
    chat_id = await _panel_chat_id(callback, session)
    if not await _ensure_admin(callback, bot, chat_id, session):
        return
    if action != "off" or field not in {"ban_on_leave", "quick_leave_ban"}:
        await safe_answer(callback)
        return
    settings_obj = await get_settings(session, chat_id)
    setattr(settings_obj, field, False)
    invalidate_settings(chat_id)
    await session.flush()
    await safe_answer(callback, "⛔️ قانون خاموش شد.")
    if callback.message:
        label = "بن هنگام خروج" if field == "ban_on_leave" else "بن خروج سریع"
        await safe_edit(callback.message,
                        f"⛔️ «{label}» خاموش شد.\n"
                        f"از این لحظه کسی به‌خاطر خروج بن نمی‌شود.\n\n"
                        f"برای روشن کردن دوباره: <code>قفل خروج</code>")


@router.callback_query(F.data.startswith("undo:"))
async def on_undo(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """Reverse the moderation action the button was attached to."""
    from ..core.telegram_extra import call_api_raw
    from ..keyboards.menus import RIGHT_PRESETS
    from ..services import moderation as mod
    from ..services import tags as tag_service
    from ..services.chat_state import invalidate_member

    parts = parse_cb(callback.data or "")
    if len(parts) < 3:
        await safe_answer(callback)
        return
    action, raw_user = parts[1], parts[2]
    user_id = int(raw_user) if raw_user.isdigit() else 0
    chat_id = await _panel_chat_id(callback, session)
    actor = await permissions.build_actor(bot, chat_id, callback.from_user.id, session=session)
    if not user_id:
        await safe_answer(callback)
        return

    if action == "unmute":
        if not actor.can_restrict():
            await safe_answer(callback, "⛔️ شما اجازه «محدود کردن اعضا» را ندارید.",
                              show_alert=True)
            return
        text = await mod.unmute_user(bot, session, chat_id=chat_id, actor=actor,
                                     target_id=user_id, target_name=str(user_id),
                                     reason="لغو از دکمه")
    elif action == "unban":
        if not actor.can_ban():
            await safe_answer(callback, "⛔️ شما اجازه «بن/رفع بن» را ندارید.", show_alert=True)
            return
        text = await mod.unban_user(bot, session, chat_id=chat_id, actor=actor,
                                    target_id=user_id, target_name=str(user_id),
                                    reason="لغو از دکمه")
    elif action == "unwarn":
        if not (actor.can_ban() or actor.can_restrict() or actor.has_role("moderator")):
            await safe_answer(callback, "⛔️ شما اجازه این کار را ندارید.", show_alert=True)
            return
        text = await mod.unwarn_user(bot, session, chat_id=chat_id, actor=actor,
                                     target_id=user_id, target_name=str(user_id))
    elif action == "demote":
        if not actor.can_promote():
            await safe_answer(callback, "⛔️ فقط مدیران گروه می‌توانند مدیر عزل کنند.",
                              show_alert=True)
            return
        if not await permissions.bot_has_right(bot, chat_id, "can_promote_members"):
            await safe_answer(callback, "⚠️ ربات دسترسی «افزودن مدیر جدید» را ندارد.",
                              show_alert=True)
            return
        payload = {"chat_id": chat_id, "user_id": user_id, "can_manage_chat": True,
                   **RIGHT_PRESETS["none"]}
        if not await call_api_raw(bot, "promoteChatMember", payload):
            await safe_answer(callback, "⚠️ تلگرام این تغییر را نپذیرفت.", show_alert=True)
            return
        invalidate_member(chat_id, user_id)
        text = "↩️ مدیر عزل شد و همهٔ دسترسی‌های او گرفته شد."
    elif action == "deltag":
        if not actor.can_manage_settings():
            await safe_answer(callback, "⛔️ فقط مدیران گروه می‌توانند تگ را حذف کنند.",
                              show_alert=True)
            return
        await tag_service.set_tag(session, chat_id, user_id, None)
        await tag_service.apply_member_tag(bot, chat_id, user_id, None)
        text = "↩️ تگ کاربر حذف شد."
    else:
        await safe_answer(callback)
        return

    await safe_answer(callback)
    if callback.message:
        await safe_edit(callback.message, f"↩️ <b>لغو شد</b>\n\n{text}")


@router.callback_query(F.data.startswith("cl:"))
async def on_chat_lock(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """قفل گروه / باز کردن گروه straight from the locks panel."""
    from ..services import chatlock

    parts = parse_cb(callback.data or "")
    mode = parts[1] if len(parts) > 1 else ""
    chat_id = await _panel_chat_id(callback, session)
    actor = await permissions.build_actor(bot, chat_id, callback.from_user.id, session=session)
    if not actor.can_restrict():
        await safe_answer(callback, "⛔️ شما اجازه «محدود کردن اعضا» را ندارید.", show_alert=True)
        return
    if mode not in chatlock.MODES:
        await safe_answer(callback)
        return
    if not await permissions.bot_has_right(bot, chat_id, "can_restrict_members"):
        await safe_answer(callback, "⚠️ ربات دسترسی «محدود کردن اعضا» را ندارد.", show_alert=True)
        return
    if not await chatlock.apply_chat_lock(bot, chat_id, mode):
        await safe_answer(callback, "⚠️ تلگرام این تغییر را نپذیرفت.", show_alert=True)
        return
    await safe_answer(callback, "✅ اعمال شد.")
    if callback.message:
        state = await chatlock.chat_lock_state(bot, chat_id)
        await safe_edit(callback.message,
                        f"{chatlock.MODE_MESSAGES[mode]}\n\n"
                        f"وضعیت کنونی: {chatlock.MODE_LABELS[state]}")


@router.callback_query(F.data.startswith("ap:"))
async def on_admin_rights(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """Toggle the Telegram administrator rights of one admin, live."""
    from ..core.telegram_extra import call_api_raw
    from ..keyboards.menus import (ADMIN_RIGHTS, RIGHT_PRESETS, admin_rights_keyboard,
                                   admin_rights_lines)
    from ..services.chat_state import invalidate_member

    parts = parse_cb(callback.data or "")
    if len(parts) < 3:
        await safe_answer(callback)
        return
    action, raw_user = parts[1], parts[2]
    key = parts[3] if len(parts) > 3 else ""
    user_id = int(raw_user) if raw_user.isdigit() else 0
    chat_id = await _panel_chat_id(callback, session)
    actor = await permissions.build_actor(bot, chat_id, callback.from_user.id, session=session)
    if not actor.can_promote():
        await safe_answer(callback, "⛔️ فقط مدیران گروه می‌توانند دسترسی مدیران را تغییر دهند.",
                          show_alert=True)
        return
    if not user_id:
        await safe_answer(callback)
        return
    if action == "x":
        await safe_answer(callback)
        if callback.message:
            await safe_edit(callback.message, "✅ بسته شد.")
        return
    if not await permissions.bot_has_right(bot, chat_id, "can_promote_members"):
        await safe_answer(callback, "⚠️ ربات دسترسی «افزودن مدیر جدید» را ندارد.",
                          show_alert=True)
        return

    member = await permissions.fetch_chat_member(bot, chat_id, user_id, refresh=True)
    if member is None:
        await safe_answer(callback, "⚠️ وضعیت این کاربر قابل تشخیص نیست.", show_alert=True)
        return
    if isinstance(member, ChatMemberOwner):
        await safe_answer(callback, "👑 دسترسی‌های مالک گروه قابل ویرایش نیست.", show_alert=True)
        return
    if not isinstance(member, ChatMemberAdministrator):
        await safe_answer(callback, "ℹ️ این کاربر دیگر مدیر گروه نیست.", show_alert=True)
        return
    if action != "r" and not member.can_be_edited:
        await safe_answer(callback,
                          "⚠️ این مدیر توسط ربات منصوب نشده است؛ ابتدا با دستور "
                          "«ارتقا» او را دوباره منصوب کنید.", show_alert=True)
        return

    name = member.user.full_name if member.user else str(user_id)
    rights = {flag: bool(getattr(member, flag, False)) for flag, _label, _d in ADMIN_RIGHTS}
    if action == "t" and key in rights:
        rights[key] = not rights[key]
    elif action in RIGHT_PRESETS:
        rights = dict(RIGHT_PRESETS[action])
    elif action == "demote":
        rights = dict(RIGHT_PRESETS["none"])
    elif action not in {"r", "t"}:
        await safe_answer(callback)
        return

    if action != "r":
        # Every flag is transmitted explicitly - False must reach Telegram.
        payload = {"chat_id": chat_id, "user_id": user_id, "can_manage_chat": True, **rights}
        ok = await call_api_raw(bot, "promoteChatMember", payload)
        invalidate_member(chat_id, user_id)
        if not ok:
            await safe_answer(callback, "⚠️ تلگرام این تغییر را نپذیرفت. "
                                        "دسترسی ربات را بررسی کنید.", show_alert=True)
            return
        await safe_answer(callback, "✅ اعمال شد.")
        member = await permissions.fetch_chat_member(bot, chat_id, user_id, refresh=True)
        if not isinstance(member, ChatMemberAdministrator):
            if callback.message:
                await safe_edit(callback.message,
                                f"⬇️ {name} از مدیریت گروه برکنار شد.")
            return
        rights = {flag: bool(getattr(member, flag, False)) for flag, _label, _d in ADMIN_RIGHTS}
    else:
        await safe_answer(callback, "🔄 به‌روز شد.")

    if callback.message:
        await safe_edit(callback.message,
                        "\n".join(admin_rights_lines(name, user_id, rights)),
                        reply_markup=admin_rights_keyboard(user_id, rights))


@router.callback_query(F.data.startswith("rep:"))
async def on_report_action(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    parts = parse_cb(callback.data or "")
    if len(parts) < 3:
        await safe_answer(callback)
        return
    report_id = int(parts[1]) if parts[1].isdigit() else 0
    action = parts[2]
    report = await session.get(Report, report_id)
    if report is None:
        await safe_answer(callback, "ℹ️ این گزارش دیگر وجود ندارد.", show_alert=True)
        return
    chat_id = report.chat_id
    actor = await permissions.build_actor(bot, chat_id, callback.from_user.id, session=session)
    await safe_answer(callback)

    if action == "close":
        await report_service.resolve_report(session, report_id, "closed",
                                            handled_by=callback.from_user.id)
        if callback.message:
            await safe_edit(callback.message, "❌ گزارش بسته شد.")
        return
    if action == "ignore":
        await report_service.resolve_report(session, report_id, "ignored",
                                            handled_by=callback.from_user.id)
        if callback.message:
            await safe_edit(callback.message, "✅ گزارش بدون اقدام بسته شد.")
        return

    if action == "delete":
        if not actor.can_delete():
            await safe_answer(callback, "⛔️ شما اجازه حذف پیام را ندارید.", show_alert=True)
            return
        from ..core.errors import safe_delete

        await safe_delete(bot, chat_id, report.message_id, context="report_delete")
        await report_service.resolve_report(session, report_id, "deleted",
                                            handled_by=callback.from_user.id)
        if callback.message:
            await safe_edit(callback.message, "🗑 پیام حذف شد.")
        return

    from ..services import moderation as mod

    if action == "ban" and not actor.can_ban():
        await safe_answer(callback, "⛔️ شما اجازه بن کردن را ندارید.", show_alert=True)
        return
    if action in {"mute", "warn"} and not actor.can_restrict():
        await safe_answer(callback, "⛔️ شما اجازه محدود کردن اعضا را ندارید.", show_alert=True)
        return
    if not report.target_id:
        await safe_answer(callback, "ℹ️ هدف این گزارش مشخص نیست.", show_alert=True)
        return

    if action == "ban":
        text = await mod.ban_user(bot, session, chat_id=chat_id, actor=actor,
                                  target_id=report.target_id, target_name=str(report.target_id),
                                  reason=f"گزارش #{report_id}")
    elif action == "mute":
        text = await mod.mute_user(bot, session, chat_id=chat_id, actor=actor,
                                   target_id=report.target_id, target_name=str(report.target_id),
                                   reason=f"گزارش #{report_id}")
    else:
        text, _ = await mod.warn_user(bot, session, chat_id=chat_id, actor=actor,
                                      target_id=report.target_id,
                                      target_name=str(report.target_id),
                                      reason=f"گزارش #{report_id}")
    await report_service.resolve_report(session, report_id, f"action:{action}",
                                        handled_by=callback.from_user.id)
    if callback.message:
        await safe_edit(callback.message, text)


@router.callback_query(F.data.startswith("conn:"))
async def on_connect(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    parts = parse_cb(callback.data or "")
    chat_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    if not chat_id:
        await safe_answer(callback)
        return
    if not await connection_service.is_authorized(bot, session, callback.from_user.id, chat_id):
        await safe_answer(callback, "⛔️ شما در این گروه مدیر نیستید.", show_alert=True)
        return
    await connection_service.set_active(session, callback.from_user.id, chat_id)
    await safe_answer(callback, "🔗 اتصال برقرار شد.")
    await send_panel(bot, callback.from_user.id, callback.from_user.id, session,
                     message=callback.message)


@router.callback_query(F.data.startswith("help:"))
async def on_help(callback: CallbackQuery) -> None:
    parts = parse_cb(callback.data or "")
    topic = parts[1] if len(parts) > 1 else ""
    await safe_answer(callback)
    if callback.message:
        await safe_edit(callback.message, help_text(topic),
                        reply_markup=markup([row(primary("⬅️ بازگشت", cb("panel", "help"))),
                                             row(primary("🏠 خانه", cb("nav", "home")))]))


@router.callback_query(F.data.startswith("cmd:"))
async def on_command_hint(callback: CallbackQuery) -> None:
    parts = parse_cb(callback.data or "")
    name = parts[1] if len(parts) > 1 else ""
    hint = COMMAND_HINTS.get(name, "ℹ️ این دستور از طریق پنل در دسترس نیست.")
    await safe_answer(callback, show_alert=False)
    if callback.message:
        await safe_edit(callback.message, hint,
                        reply_markup=markup([row(primary("⬅️ بازگشت", cb("nav", "home")))]))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def _ensure_admin(callback: CallbackQuery, bot: Bot, chat_id: int,
                        session: AsyncSession) -> bool:
    actor = await permissions.build_actor(bot, chat_id, callback.from_user.id, session=session)
    if actor.can_manage_settings():
        return True
    await safe_answer(callback, "⛔️ فقط مدیران گروه می‌توانند تنظیمات را تغییر دهند.",
                      show_alert=True)
    return False


@router.callback_query.middleware()
async def panel_owner_middleware(handler, event: CallbackQuery, data: dict):
    """A panel belongs to the admin who opened it - nobody else may click it.

    Every panel message is remembered when it is sent; clicking it from another
    account is refused with an alert instead of silently changing the group.
    """
    message = getattr(event, "message", None)
    user = getattr(event, "from_user", None)
    if message is not None and user is not None:
        owner = _panel_owner(message.chat.id, message.message_id)
        if owner is not None and owner != user.id:
            bot = data.get("bot")
            if bot is not None:
                await safe_call(
                    lambda: bot.answer_callback_query(
                        callback_query_id=event.id,
                        text=("\U0001f512 این پنل مخصوص مدیری است که آن را باز کرده است.\n"
                              "برای پنل خودتان در گروه دستور «پنل» را بزنید."),
                        show_alert=True),
                    default=None, context="panel_owner_denied")
            return None
    return await handler(event, data)
