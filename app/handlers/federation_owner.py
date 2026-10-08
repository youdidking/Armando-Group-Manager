"""Federations and owner-level commands (global ban is opt-in)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import func, select

from ..config import settings
from ..core.normalization import to_persian_digits
from ..core.timeutils import persian_datetime
from ..db.models import BanRecord, Chat, ModerationAction, User
from ..services import federation as fed_service
from ..services import gban as gban_service
from ..services.chat_state import invalidate_settings
from .common import CommandContext, require, user_html
from .registry import command

logger = logging.getLogger("armando.handlers.federation")


async def _current_fed(ctx: CommandContext):
    from ..services.chat_state import get_settings

    settings_obj = await get_settings(ctx.session, ctx.chat_id)
    if not settings_obj.federation_id:
        return None, settings_obj
    fed = await fed_service.get_fed_by_id(ctx.session, int(settings_obj.federation_id))
    return fed, settings_obj


# --------------------------------------------------------------------------- #
# Federations
# --------------------------------------------------------------------------- #
@command("ساخت فدراسیون", "ایجاد فدراسیون", role="admin", permission="manage",
         category="federation", description="ساخت فدراسیون جدید")
async def cmd_fed_create(ctx: CommandContext) -> None:
    if not await require(ctx, role="admin", permission="manage"):
        return
    name = (ctx.arg_text or "").strip()
    if not name:
        await ctx.reply("⚠️ نام فدراسیون را بنویسید: <code>ساخت فدراسیون myfed</code>")
        return
    try:
        fed = await fed_service.create_fed(ctx.session, name, ctx.user_id)
    except ValueError as exc:
        await ctx.reply(str(exc))
        return
    await ctx.reply(f"🌐 فدراسیون «<code>{fed.name}</code>» ساخته شد.\n"
                    "برای اتصال گروه‌ها: <code>اتحاد فدراسیون نام</code>")


@command("اتحاد فدراسیون", "اتصال فدراسیون", "عضویت فدراسیون", role="admin",
         permission="manage", category="federation", description="اتحاد گروه به فدراسیون")
async def cmd_fed_join(ctx: CommandContext) -> None:
    if not await require(ctx, role="admin", permission="manage"):
        return
    name = (ctx.arg_text or "").strip().lower()
    fed = await fed_service.get_fed(ctx.session, name)
    if fed is None:
        await ctx.reply("❌ فدراسیونی با این نام پیدا نشد.")
        return
    joined = await fed_service.join_fed(ctx.session, fed, ctx.chat_id, joined_by=ctx.user_id)
    await ctx.reply(f"🔗 گروه به فدراسیون «<code>{fed.name}</code>» پیوست." if joined
                    else "ℹ️ این گروه از قبل عضو فدراسیون است.")


@command("ترک فدراسیون", "خروج از فدراسیون", role="admin", permission="manage",
         category="federation", description="خروج گروه از فدراسیون")
async def cmd_fed_leave(ctx: CommandContext) -> None:
    if not await require(ctx, role="admin", permission="manage"):
        return
    left = await fed_service.leave_fed(ctx.session, ctx.chat_id)
    await ctx.reply("🚪 گروه از فدراسیون خارج شد." if left else "ℹ️ این گروه عضو فدراسیونی نیست.")


@command("اطلاعات فدراسیون", role="member", category="federation",
         description="نمایش اطلاعات فدراسیون")
async def cmd_fed_info(ctx: CommandContext) -> None:
    fed, settings_obj = await _current_fed(ctx)
    if fed is None:
        await ctx.reply("ℹ️ این گروه عضو هیچ فدراسیونی نیست.")
        return
    admins = await fed_service.fed_admins(ctx.session, fed)
    chats = await fed_service.fed_chats(ctx.session, fed)
    bans = await fed_service.fban_list(ctx.session, fed)
    await ctx.reply(fed_service.fed_info_text(fed, chats, admins, len(bans)))


@command("مدیران فدراسیون", role="admin", category="federation",
         description="نمایش مدیران فدراسیون")
async def cmd_fed_admins(ctx: CommandContext) -> None:
    fed, _ = await _current_fed(ctx)
    if fed is None:
        await ctx.reply("ℹ️ این گروه عضو هیچ فدراسیونی نیست.")
        return
    admins = await fed_service.fed_admins(ctx.session, fed)
    lines = ["👮 <b>مدیران فدراسیون</b>", ""]
    for user_id in admins:
        user = await ctx.session.get(User, user_id)
        name = f"{user.first_name or ''} {user.last_name or ''}".strip() if user else str(user_id)
        lines.append(f"• {user_html(user_id, name or str(user_id))}")
    await ctx.reply("\n".join(lines))


@command("افزودن مدیر فدراسیون", role="admin", permission="manage",
         category="federation", description="افزودن مدیر به فدراسیون")
async def cmd_fed_admin_add(ctx: CommandContext) -> None:
    fed, _ = await _current_fed(ctx)
    if fed is None:
        await ctx.reply("ℹ️ این گروه عضو هیچ فدراسیونی نیست.")
        return
    if not await fed_service.is_fed_admin(ctx.session, fed, ctx.user_id):
        await ctx.reply("⛔️ فقط مدیران فدراسیون می‌توانند مدیر اضافه کنند.")
        return
    target = await ctx.target()
    if target is None or not target.user_id:
        await ctx.reply("❌ کاربر هدف مشخص نشد.")
        return
    await fed_service.add_fed_admin(ctx.session, fed, target.user_id, added_by=ctx.user_id)
    await ctx.reply(f"✅ {user_html(target.user_id, target.full_name)} مدیر فدراسیون شد.")


@command("حذف مدیر فدراسیون", role="admin", permission="manage",
         category="federation", description="حذف مدیر فدراسیون")
async def cmd_fed_admin_remove(ctx: CommandContext) -> None:
    fed, _ = await _current_fed(ctx)
    if fed is None:
        await ctx.reply("ℹ️ این گروه عضو هیچ فدراسیونی نیست.")
        return
    if fed.owner_id != ctx.user_id:
        await ctx.reply("⛔️ فقط مالک فدراسیون می‌تواند مدیران را حذف کند.")
        return
    target = await ctx.target()
    if target is None or not target.user_id:
        await ctx.reply("❌ کاربر هدف مشخص نشد.")
        return
    removed = await fed_service.remove_fed_admin(ctx.session, fed, target.user_id)
    await ctx.reply("🗑 مدیر فدراسیون حذف شد." if removed else "ℹ️ تغییری ایجاد نشد.")


@command("بن فدراسیون", "افبن", role="admin", permission="manage",
         category="federation", description="بن کردن کاربر در سطح فدراسیون")
async def cmd_fban(ctx: CommandContext) -> None:
    fed, _ = await _current_fed(ctx)
    if fed is None:
        await ctx.reply("ℹ️ این گروه عضو هیچ فدراسیونی نیست.")
        return
    if not await fed_service.is_fed_admin(ctx.session, fed, ctx.user_id):
        await ctx.reply("⛔️ فقط مدیران فدراسیون می‌توانند بن کنند.")
        return
    target = await ctx.target()
    if target is None or not target.user_id:
        await ctx.reply("❌ کاربر هدف مشخص نشد.")
        return
    _, _, reason = ctx.duration_and_reason(target.rest)
    applied = await fed_service.fban(ctx.session, ctx.bot, fed, user_id=target.user_id,
                                     reason=reason or "بدون دلیل", banned_by=ctx.user_id,
                                     enforce=(fed.ban_mode == "enforce"))
    await ctx.reply(f"🌐 کاربر {user_html(target.user_id, target.full_name)} در فدراسیون بن شد.\n"
                    f"📌 دلیل: {reason or '—'}\n"
                    f"⚡️ اعمال‌شده در {to_persian_digits(str(applied))} گروه")


@command("رفع بن فدراسیون", "آنفبن", role="admin", permission="manage",
         category="federation", description="رفع بن فدراسیون")
async def cmd_funban(ctx: CommandContext) -> None:
    fed, _ = await _current_fed(ctx)
    if fed is None:
        await ctx.reply("ℹ️ این گروه عضو هیچ فدراسیونی نیست.")
        return
    if not await fed_service.is_fed_admin(ctx.session, fed, ctx.user_id):
        await ctx.reply("⛔️ فقط مدیران فدراسیون می‌توانند رفع بن کنند.")
        return
    target = await ctx.target()
    if target is None or not target.user_id:
        await ctx.reply("❌ کاربر هدف مشخص نشد.")
        return
    removed = await fed_service.funban(ctx.session, ctx.bot, fed, user_id=target.user_id,
                                       unbanned_by=ctx.user_id)
    await ctx.reply("🕊 بن فدراسیون برداشته شد." if removed else "ℹ️ این کاربر در فدراسیون بن نبود.")


@command("لیست بن فدراسیون", role="admin", category="federation",
         description="نمایش لیست بن‌های فدراسیون")
async def cmd_fban_list(ctx: CommandContext) -> None:
    fed, _ = await _current_fed(ctx)
    if fed is None:
        await ctx.reply("ℹ️ این گروه عضو هیچ فدراسیونی نیست.")
        return
    records = await fed_service.fban_list(ctx.session, fed)
    await ctx.reply(fed_service.fban_list_text(records))


@command("تنظیم قوانین فدراسیون", role="admin", permission="manage",
         category="federation", description="تنظیم قوانین فدراسیون")
async def cmd_fed_rules(ctx: CommandContext) -> None:
    fed, _ = await _current_fed(ctx)
    if fed is None:
        await ctx.reply("ℹ️ این گروه عضو هیچ فدراسیونی نیست.")
        return
    if not await fed_service.is_fed_admin(ctx.session, fed, ctx.user_id):
        await ctx.reply("⛔️ فقط مدیران فدراسیون می‌توانند قوانین را تنظیم کنند.")
        return
    fed.rules = ctx.arg_text.strip()
    await ctx.session.flush()
    await ctx.reply("📜 قوانین فدراسیون ذخیره شد.")


@command("قوانین فدراسیون", role="member", category="federation",
         description="نمایش قوانین فدراسیون")
async def cmd_fed_show_rules(ctx: CommandContext) -> None:
    fed, _ = await _current_fed(ctx)
    if fed is None or not fed.rules:
        await ctx.reply("ℹ️ قوانینی برای فدراسیون تنظیم نشده است.")
        return
    await ctx.reply(f"📜 <b>قوانین فدراسیون {fed.name}</b>\n\n{fed.rules}")


@command("حالت فدراسیون", role="admin", permission="manage", category="federation",
         description="تغییر حالت اجرای بن فدراسیون")
async def cmd_fed_mode(ctx: CommandContext) -> None:
    fed, settings_obj = await _current_fed(ctx)
    if fed is None:
        await ctx.reply("ℹ️ این گروه عضو هیچ فدراسیونی نیست.")
        return
    if not await fed_service.is_fed_admin(ctx.session, fed, ctx.user_id):
        await ctx.reply("⛔️ فقط مدیران فدراسیون می‌توانند این تنظیم را تغییر دهند.")
        return
    mode = "share"
    if ctx.chat_id in await fed_service.fed_chats(ctx.session, fed):
        settings_obj.fed_enforce = not bool(settings_obj.fed_enforce)
        invalidate_settings(ctx.chat_id)
        await ctx.reply("✅ اجرای خودکار بن‌های فدراسیون در این گروه: "
                        + ("🟢 فعال" if settings_obj.fed_enforce else "🔴 غیرفعال"))
        return
    fed.ban_mode = "enforce" if fed.ban_mode == "share" else "share"
    mode = fed.ban_mode
    await ctx.session.flush()
    await ctx.reply("⚙️ حالت فدراسیون: " +
                    ("اجرای خودکار" if mode == "enforce" else "فقط اشتراک‌گذاری"))


# --------------------------------------------------------------------------- #
# Global ban - background rollout
# --------------------------------------------------------------------------- #
async def _rollout(bot, user_id: int, reason: str, source_chat_id: int, *,
                   unban: bool = False) -> None:
    """Apply (or lift) a global ban in every chat the bot administers."""
    from ..db import session_scope

    try:
        async with session_scope() as session:
            if unban:
                count = await gban_service.lift_everywhere(bot, session, user_id,
                                                           skip=source_chat_id)
                text = (f"🕊 بن سراسری در {to_persian_digits(str(count))} گفتگو برداشته شد.")
            else:
                count = await gban_service.enforce_everywhere(bot, session, user_id, reason,
                                                              skip=source_chat_id)
                text = (f"🌍 بن سراسری در {to_persian_digits(str(count))} گفتگوی دیگر اعمال شد."
                        if count else "")
    except Exception:  # noqa: BLE001 - a background job must never crash the bot
        logger.exception("gban rollout failed user=%s", user_id)
        return
    if text:
        from ..core.errors import safe_send

        await safe_send(bot, source_chat_id, text, parse_mode="HTML")

# --------------------------------------------------------------------------- #
# Owner only (global features are OFF unless explicitly enabled)
# --------------------------------------------------------------------------- #
@command("بن سراسری", role="founder", category="owner", description="بن سراسری (مالک ربات)",
         allow_in_private=True)
async def cmd_global_ban(ctx: CommandContext) -> None:
    if not ctx.actor.is_bot_owner:
        return
    if not settings.global_ban_enabled:
        await ctx.reply("🔒 قابلیت بن سراسری غیرفعال است.\n"
                        "برای فعال‌سازی متغیر <code>GLOBAL_BAN_ENABLED=true</code> را تنظیم کنید.")
        return
    target = await ctx.target()
    user_id = target.user_id if target else None
    if not user_id:
        await ctx.reply("❌ کاربر هدف مشخص نشد.")
        return
    _, reason = ctx.duration_and_reason(target.rest if target else [])
    user = await ctx.session.get(User, user_id)
    if user is None:
        user = User(id=user_id, first_name="")
        ctx.session.add(user)
    user.global_banned = True
    user.global_ban_reason = (reason or "بدون دلیل")[:300]
    user.global_banned_at = datetime.utcnow()
    ctx.session.add(BanRecord(user_id=user_id, banned_by=ctx.user_id,
                              reason=user.global_ban_reason, scope="global", active=True,
                              created_at=datetime.utcnow()))
    await ctx.session.flush()
    gban_service.invalidate(user_id)

    # Ban here and now, then roll it out to every other guarded chat.
    here = await gban_service.ban_in_chat(ctx.bot, ctx.chat_id, user_id)
    await ctx.reply(
        f"🌍 کاربر <code>{user_id}</code> به‌صورت سراسری بن شد.\n"
        f"📌 {user.global_ban_reason}\n"
        + ("✅ در این گفتگو هم بن شد.\n" if here else "")
        + "↻ اعمال در بقیهٔ گروه‌ها و کانال‌ها در پس‌زمینه انجام می‌شود.")

    async def _later() -> None:
        await _rollout(ctx.bot, user_id, user.global_ban_reason or "بدون دلیل", ctx.chat_id)

    asyncio.create_task(_later())


@command("رفع بن سراسری", role="founder", category="owner",
         description="رفع بن سراسری (مالک ربات)", allow_in_private=True)
async def cmd_global_unban(ctx: CommandContext) -> None:
    if not ctx.actor.is_bot_owner:
        return
    target = await ctx.target()
    user_id = target.user_id if target else None
    from ..core.normalization import normalize_digits

    if not user_id and ctx.arg_text:
        digits = "".join(ch for ch in normalize_digits(ctx.arg_text, to="ascii") if ch.isdigit())
        user_id = int(digits) if digits else None
    if not user_id:
        await ctx.reply("❌ کاربر هدف مشخص نشد.")
        return
    user = await ctx.session.get(User, user_id)
    if user is not None:
        user.global_banned = False
        user.global_ban_reason = None
    await ctx.session.execute(
        select(BanRecord).where(BanRecord.scope == "global", BanRecord.user_id == user_id))
    from sqlalchemy import update

    await ctx.session.execute(
        update(BanRecord).where(BanRecord.scope == "global", BanRecord.user_id == user_id)
        .values(active=False))
    await ctx.session.flush()
    gban_service.invalidate(user_id)
    await ctx.reply(f"🕊 بن سراسری کاربر <code>{user_id}</code> برداشته شد.")

    async def _later() -> None:
        await _rollout(ctx.bot, user_id, "", ctx.chat_id, unban=True)

    asyncio.create_task(_later())


@command("لیست بن سراسری", role="founder", category="owner",
         description="نمایش بن‌های سراسری", allow_in_private=True)
async def cmd_global_ban_list(ctx: CommandContext) -> None:
    if not ctx.actor.is_bot_owner:
        return
    result = await ctx.session.execute(
        select(BanRecord).where(BanRecord.scope == "global", BanRecord.active.is_(True)).limit(50))
    rows = list(result.scalars().all())
    if not rows:
        await ctx.reply("✅ هیچ بن سراسری فعالی وجود ندارد.")
        return
    lines = ["🌍 <b>بن‌های سراسری</b>", ""]
    for row in rows:
        lines.append(f"• <code>{row.user_id}</code> — 📌 {row.reason or '—'}\n"
                     f"   🕒 {persian_datetime(row.created_at)}")
    await ctx.reply("\n".join(lines))


@command("ارسال همگانی", "همگانی", role="founder", category="owner",
         description="ارسال پیام به همه گروه‌ها (مالک ربات)", allow_in_private=True)
async def cmd_broadcast(ctx: CommandContext) -> None:
    if not ctx.actor.is_bot_owner:
        return
    text = ctx.arg_text.strip()
    if not text and ctx.message.reply_to_message:
        text = ctx.message.reply_to_message.text or ctx.message.reply_to_message.caption or ""
    if not text:
        await ctx.reply("⚠️ متن پیام را بنویسید: <code>ارسال همگانی متن شما</code>")
        return
    result = await ctx.session.execute(select(Chat.id).where(Chat.is_active.is_(True)))
    chat_ids = [row[0] for row in result.all()]
    sent, failed = 0, 0
    from ..core.errors import safe_send

    for chat_id in chat_ids:
        message = await safe_send(ctx.bot, chat_id, f"📢 <b>اطلاعیه</b>\n\n{text}",
                                  parse_mode="HTML")
        if message is None:
            failed += 1
        else:
            sent += 1
    await ctx.reply(f"📢 ارسال همگانی انجام شد.\n✅ موفق: {to_persian_digits(str(sent))}\n"
                    f"⚠️ ناموفق: {to_persian_digits(str(failed))}")


@command("آمار ربات", role="founder", category="owner", description="آمار کلی ربات",
         allow_in_private=True)
async def cmd_bot_stats(ctx: CommandContext) -> None:
    if not ctx.actor.is_bot_owner:
        return
    chats = await ctx.session.scalar(select(func.count(Chat.id))) or 0
    active_chats = await ctx.session.scalar(
        select(func.count(Chat.id)).where(Chat.is_active.is_(True))) or 0
    users = await ctx.session.scalar(select(func.count(User.id))) or 0
    actions = await ctx.session.scalar(select(func.count(ModerationAction.id))) or 0
    members = await ctx.session.scalar(
        select(func.coalesce(func.sum(Chat.member_count), 0))) or 0
    await ctx.reply("\n".join([
        "📊 <b>آمار ربات</b>",
        "",
        f"💬 تعداد گروه‌ها: {to_persian_digits(str(chats))}",
        f"🟢 گروه‌های فعال: {to_persian_digits(str(active_chats))}",
        f"👥 مجموع اعضا: {to_persian_digits(str(members))}",
        f"🙋 کاربران شناخته‌شده: {to_persian_digits(str(users))}",
        f"🛡 تعداد عملیات مدیریتی: {to_persian_digits(str(actions))}",
    ]))


@command("ترک گروه", role="founder", category="owner", description="خروج ربات از یک گروه",
         allow_in_private=True)
async def cmd_leave_chat(ctx: CommandContext) -> None:
    if not ctx.actor.is_bot_owner:
        return
    from ..core.normalization import normalize_digits

    digits = "".join(ch for ch in normalize_digits(ctx.arg_text, to="ascii") if ch.lstrip("-").isdigit())
    if not digits:
        await ctx.reply("⚠️ شناسه گروه را وارد کنید: <code>ترک گروه -100123456789</code>")
        return
    chat_id = int(digits)
    from ..core.errors import safe_call

    ok = await safe_call(lambda: ctx.bot.leave_chat(chat_id=chat_id), default=None,
                         context="leave_chat")
    if ok is None:
        await ctx.reply("⚠️ خروج انجام نشد.")
        return
    chat = await ctx.session.get(Chat, chat_id)
    if chat is not None:
        chat.is_active = False
    await ctx.session.flush()
    await ctx.reply(f"👋 ربات از گروه <code>{chat_id}</code> خارج شد.")
