"""Chat-scoped owner delegation; never grants Telegram permissions."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta

from aiogram import Bot
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.normalization import normalize_digits
from ..db.models import BotRootGrant, Chat, RootGrantDraft, User
from ..keyboards.factory import cb, danger, markup, primary, row

USER_ID_RE = re.compile(r"^\d{5,15}$")
USERNAME_RE = re.compile(r"^@?[A-Za-z][A-Za-z0-9_]{4,31}$")
CHAT_TYPES = ("group", "supergroup", "channel")


class RootAccessError(ValueError):
    """Safe Persian validation error shown to the bot owner."""


def validate_target_ref(target_ref: str) -> str:
    raw = (target_ref or "").strip()
    normalized = normalize_digits(raw, to="ascii")
    if USER_ID_RE.fullmatch(normalized):
        return normalized
    if USERNAME_RE.fullmatch(raw):
        return "@" + raw.lstrip("@").lower()
    raise RootAccessError("❌ هدف باید شناسهٔ عددی یا نام‌کاربری معتبر تلگرام باشد.")


async def grant_root(session: AsyncSession, *, chat_id: int, user_id: int,
                     granted_by: int) -> bool:
    """Create an idempotent root grant for exactly one chat."""
    existing = await session.execute(
        select(BotRootGrant).where(BotRootGrant.chat_id == chat_id,
                                   BotRootGrant.user_id == user_id).limit(1))
    if existing.scalar_one_or_none() is not None:
        return False
    if await session.get(User, user_id) is None:
        session.add(User(id=user_id, first_name=""))
        await session.flush()
    session.add(BotRootGrant(chat_id=chat_id, user_id=user_id,
                             granted_by=granted_by, created_at=datetime.utcnow()))
    await session.flush()
    return True


async def revoke_root(session: AsyncSession, *, chat_id: int, user_id: int) -> bool:
    result = await session.execute(
        delete(BotRootGrant).where(BotRootGrant.chat_id == chat_id,
                                   BotRootGrant.user_id == user_id))
    return bool(result.rowcount)


async def revoke_root_everywhere(session: AsyncSession, *, user_id: int) -> int:
    result = await session.execute(
        delete(BotRootGrant).where(BotRootGrant.user_id == user_id))
    return int(result.rowcount or 0)


async def root_chat_ids(session: AsyncSession, user_id: int, *,
                        active_only: bool = True) -> list[int]:
    query = (select(BotRootGrant.chat_id)
             .join(Chat, Chat.id == BotRootGrant.chat_id)
             .where(BotRootGrant.user_id == user_id,
                    Chat.type.in_(CHAT_TYPES)))
    if active_only:
        query = query.where(Chat.is_active.is_(True))
    result = await session.execute(query.order_by(BotRootGrant.chat_id))
    return [int(row[0]) for row in result.all()]


async def known_active_chats(session: AsyncSession) -> list[Chat]:
    """Chats the bot has recorded as active (Bot API has no all-chats query)."""
    result = await session.execute(
        select(Chat).where(Chat.is_active.is_(True), Chat.type.in_(CHAT_TYPES))
        .order_by(Chat.title, Chat.id))
    return list(result.scalars().all())


async def chats_for_root(session: AsyncSession, user_id: int) -> list[Chat]:
    ids = await root_chat_ids(session, user_id)
    if not ids:
        return []
    result = await session.execute(
        select(Chat).where(Chat.id.in_(ids), Chat.is_active.is_(True),
                           Chat.type.in_(CHAT_TYPES)).order_by(Chat.title, Chat.id))
    return list(result.scalars().all())


async def create_root_draft(session: AsyncSession, *, owner_id: int,
                            target_ref: str) -> RootGrantDraft:
    target_ref = validate_target_ref(target_ref)
    await session.execute(delete(RootGrantDraft).where(RootGrantDraft.owner_id == owner_id))
    draft = RootGrantDraft(owner_id=owner_id, target_ref=target_ref[:64],
                           created_at=datetime.utcnow(),
                           expires_at=datetime.utcnow() + timedelta(minutes=20))
    session.add(draft)
    await session.flush()
    return draft


async def get_root_draft(session: AsyncSession, draft_id: int,
                         owner_id: int) -> RootGrantDraft | None:
    draft = await session.get(RootGrantDraft, draft_id)
    if (draft is None or int(draft.owner_id) != int(owner_id)
            or draft.expires_at < datetime.utcnow()):
        return None
    return draft


async def list_root_grants(session: AsyncSession):
    result = await session.execute(
        select(BotRootGrant, Chat, User)
        .join(Chat, Chat.id == BotRootGrant.chat_id)
        .join(User, User.id == BotRootGrant.user_id)
        .order_by(Chat.title, User.first_name, BotRootGrant.user_id))
    return list(result.all())


ROOT_PAGE_SIZE = 8


def root_picker_text(draft: RootGrantDraft, chats: list[Chat], page: int) -> str:
    pages = max(1, (len(chats) + ROOT_PAGE_SIZE - 1) // ROOT_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    user_ref = html.escape(draft.target_ref)
    return ("🔐 <b>اعطای دسترسی روت محدود به یک چت</b>\n\n"
            f"هدف: <code>{user_ref}</code>\n"
            "گروه یا کانالی را انتخاب کنید. دسترسی فقط در همان چت خواهد بود؛ "
            "کاربر باید مدیر یا مالک فعلی آن باشد.\n"
            "ℹ️ رابط برنامه‌نویسی ربات تلگرام (Bot API) فهرست کامل چت‌ها را نمی‌دهد؛ این‌ها چت‌های فعالِ ثبت‌شده‌اند.\n\n"
            f"صفحهٔ {page + 1} از {pages}")


def root_picker_keyboard(draft: RootGrantDraft, chats: list[Chat], page: int):
    pages = max(1, (len(chats) + ROOT_PAGE_SIZE - 1) // ROOT_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * ROOT_PAGE_SIZE
    rows = []
    for chat in chats[start:start + ROOT_PAGE_SIZE]:
        title = " ".join((chat.title or "").split()) or (f"@{chat.username}" if chat.username else str(chat.id))
        icon = "📢" if chat.type == "channel" else "👥"
        rows.append([primary(f"{icon} {title[:36]}", cb("rt", "grant", draft.id, chat.id))])
    if len(chats) > ROOT_PAGE_SIZE:
        rows.append(row(
            primary("⬅️ قبلی", cb("rt", "page", draft.id, max(0, page - 1)))
            if page > 0 else None,
            primary(f"{page + 1}/{pages}", cb("rt", "noop")),
            primary("بعدی ➡️", cb("rt", "page", draft.id, min(pages - 1, page + 1)))
            if page + 1 < pages else None,
        ))
    rows.append([danger("❌ لغو", cb("rt", "cancel", draft.id))])
    return markup(rows)


async def resolve_admin_target(bot: Bot, session: AsyncSession, chat_id: int,
                               target_ref: str) -> tuple[int, str, str | None]:
    """Resolve an id/username and require current Telegram admin/creator status.

    Usernames are resolved only from this chat's administrator list or the
    bot's own observed user records; Bot API itself accepts a numeric user id.
    """
    raw = (target_ref or "").strip()
    normalized = normalize_digits(raw, to="ascii")
    user_id: int | None = None
    if USER_ID_RE.fullmatch(normalized):
        user_id = int(normalized)
    else:
        match = USERNAME_RE.fullmatch(raw)
        if not match:
            raise RootAccessError(
                "❌ هدف را با شناسهٔ عددی یا نام‌کاربری معتبر وارد کنید.")
        username = match.group(0).lstrip("@").lower()

        try:
            admins = await bot.get_chat_administrators(chat_id=chat_id)
        except Exception as exc:  # noqa: BLE001 - fail closed when current admin list is unavailable
            admins = None
            logger_message = exc.__class__.__name__
        else:
            logger_message = ""
        for admin in admins or []:
            admin_user = getattr(admin, "user", None)
            if admin_user is not None and (admin_user.username or "").lower() == username:
                user_id = int(admin_user.id)
                break
        if user_id is None:
            result = await session.execute(
                select(User).where(User.username.is_not(None)).limit(2000))
            for user in result.scalars().all():
                if (user.username or "").lower() == username:
                    user_id = int(user.id)
                    break
        if user_id is None and logger_message:
            raise RootAccessError(
                "⚠️ فهرست مدیران این چت قابل بررسی نیست؛ شناسهٔ عددی را بدهید یا دسترسی ربات را بررسی کنید.")
        if user_id is None:
            raise RootAccessError(
                f"❌ نام‌کاربری «@{username}» در کاربران شناخته‌شده یا مدیران این چت پیدا نشد؛ "
                "شناسهٔ عددی را امتحان کنید.")

    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception as exc:  # noqa: BLE001 - fail closed when status is unknown
        raise RootAccessError(
            "⚠️ وضعیت مدیریتی این کاربر در چت قابل بررسی نیست؛ "
            "ربات باید به چت دسترسی داشته باشد.") from exc
    status = str(getattr(member, "status", ""))
    if status not in {"administrator", "creator"}:
        raise RootAccessError(
            "⛔️ دسترسی روت فقط به مدیر فعلی یا مالک همان گروه/کانال داده می‌شود.")
    user = getattr(member, "user", None)
    name = (getattr(user, "full_name", "") or "").strip() or str(user_id)
    return user_id, name, getattr(user, "username", None)


async def is_current_admin_or_creator(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Recheck a delegated user's current status before scoped owner actions."""
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception:  # noqa: BLE001 - fail closed
        return False
    return str(getattr(member, "status", "")) in {"administrator", "creator"}
