"""Persian moderation commands: بن، سکوت، کیک، اخطار، تاریخچه و ..."""

from __future__ import annotations

import logging

from sqlalchemy import select

from ..core.duration import format_duration
from ..core.normalization import to_persian_digits
from ..core.timeutils import persian_datetime
from ..db.models import ChatMemberState, ModerationAction, User, Warning
from ..keyboards.factory import cb, danger, markup, primary, success
from ..services import moderation as mod
from ..services.moderation import PUNISHMENT_OPTIONS_FA
from ..services.roles import role_name_fa
from ..services.targeting import TargetResult
from .common import (
    CommandContext,
    display_name,
    ensure_bot_admin,
    guard_target,
    require,
    undo_keyboard,
    user_html,
)
from .registry import command

logger = logging.getLogger("armando.handlers.moderation")


async def _target(ctx: CommandContext) -> tuple[TargetResult | None, int | None, str]:
    """Resolve target + duration + reason from the invocation."""
    target = await ctx.target()
    if not target.ok:
        return target, None, ""
    duration, reason = ctx.duration_and_reason(target.rest)
    return target, duration, reason


def _warn_card_buttons(chat_id: int, user_id: int) -> list[list]:
    return [
        [primary("🗑 کسر اخطار", cb("w", "unwarn", user_id)),
         success("♻️ صفر کردن", cb("w", "reset", user_id))],
        [primary("📜 تاریخچه", cb("w", "history", user_id)),
         danger("⚠️ اخطار جدید", cb("w", "warn", user_id))],
    ]


@command("بن", "بن کردن", "مسدود", role="moderator", permission="ban",
         category="moderation", description="بن کردن کاربر", usage="بن [مدت] [دلیل]")
async def cmd_ban(ctx: CommandContext) -> None:
    if not await require(ctx, role="moderator", permission="ban"):
        return
    target, duration, reason = await _target(ctx)
    if target is None or not await guard_target(ctx, target):
        return
    if not await ensure_bot_admin(ctx, permission="ban"):
        return
    reply_id = ctx.message.reply_to_message.message_id if ctx.message.reply_to_message else None
    text = await mod.ban_user(ctx.bot, ctx.session, chat_id=ctx.chat_id, actor=ctx.actor,
                              target_id=target.user_id, target_name=target.full_name,
                              duration=duration, reason=reason, delete_message_id=reply_id,
                              chat_title=ctx.chat_title, source="command")
    await ctx.reply(text, reply_markup=undo_keyboard("unban", target.user_id), reply_to=False)
    await ctx.delete_invocation()


@command("بن موقت", "بن وقت", role="moderator", permission="ban",
         category="moderation", description="بن موقت کاربر", usage="بن موقت ۲روز تبلیغ")
async def cmd_temp_ban(ctx: CommandContext) -> None:
    await cmd_ban(ctx)


@command("رفع بن", "انبن", "آزاد", "آزاد کردن", role="moderator", permission="ban",
         category="moderation", description="رفع بن کاربر")
async def cmd_unban(ctx: CommandContext) -> None:
    if not await require(ctx, role="moderator", permission="ban"):
        return
    target, _, reason = await _target(ctx)
    if target is None or not target.user_id:
        # unban by id/username works even for users who already left
        from ..services.targeting import resolve_target

        fallback = await resolve_target(ctx.bot, ctx.session, ctx.message, ctx.args)
        if fallback.error and not fallback.user_id:
            await ctx.reply(fallback.error)
            return
        target = fallback
    if not target.user_id:
        await ctx.reply("❌ کاربر هدف مشخص نشد.")
        return
    text = await mod.unban_user(ctx.bot, ctx.session, chat_id=ctx.chat_id, actor=ctx.actor,
                                target_id=target.user_id, target_name=target.full_name,
                                chat_title=ctx.chat_title, reason=reason)
    await ctx.reply(text, reply_to=False)
    await ctx.delete_invocation()


@command("کیک", "اخراج", "بیرون", role="moderator", permission="kick",
         category="moderation", description="اخراج کاربر از گروه")
async def cmd_kick(ctx: CommandContext) -> None:
    if not await require(ctx, role="moderator", permission="kick"):
        return
    target, _, reason = await _target(ctx)
    if target is None or not await guard_target(ctx, target):
        return
    if not await ensure_bot_admin(ctx, permission="ban"):
        return
    reply_id = ctx.message.reply_to_message.message_id if ctx.message.reply_to_message else None
    sticky = bool(ctx.settings.get("sticky_ban", True))
    text = await mod.kick_user(ctx.bot, ctx.session, chat_id=ctx.chat_id, actor=ctx.actor,
                               target_id=target.user_id, target_name=target.full_name,
                               reason=reason, delete_message_id=reply_id,
                               chat_title=ctx.chat_title, sticky=sticky)
    await ctx.reply(text, reply_to=False)
    await ctx.delete_invocation()


@command("سکوت", "میوت", "بیصدا", "بی‌صدا", role="muter", permission="restrict",
         category="moderation", description="بی‌صدا کردن کاربر", usage="سکوت ۳۰دقیقه [دلیل]")
async def cmd_mute(ctx: CommandContext) -> None:
    if not await require(ctx, role="muter", permission="restrict"):
        return
    target, duration, reason = await _target(ctx)
    if target is None or not await guard_target(ctx, target):
        return
    if not await ensure_bot_admin(ctx, permission="restrict"):
        return
    reply_id = ctx.message.reply_to_message.message_id if ctx.message.reply_to_message else None
    text = await mod.mute_user(ctx.bot, ctx.session, chat_id=ctx.chat_id, actor=ctx.actor,
                               target_id=target.user_id, target_name=target.full_name,
                               duration=duration, reason=reason, delete_message_id=reply_id,
                               chat_title=ctx.chat_title)
    await ctx.reply(text + "\n💡 یا با ریپلای بنویسید <code>لغو سکوت</code>",
                    reply_markup=undo_keyboard("unmute", target.user_id), reply_to=False)
    await ctx.delete_invocation()


@command("لغو سکوت", "آنمیوت", "رفع سکوت", "باصدا", "حذف سکوت", "برداشتن سکوت",
         role="muter", permission="restrict",
         category="moderation", description="برداشتن سکوت کاربر")
async def cmd_unmute(ctx: CommandContext) -> None:
    if not await require(ctx, role="muter", permission="restrict"):
        return
    target, _, reason = await _target(ctx)
    if target is None or not await guard_target(ctx, target):
        return
    text = await mod.unmute_user(ctx.bot, ctx.session, chat_id=ctx.chat_id, actor=ctx.actor,
                                 target_id=target.user_id, target_name=target.full_name,
                                 chat_title=ctx.chat_title, reason=reason)
    await ctx.reply(text, reply_to=False)
    await ctx.delete_invocation()


@command("اخطار", "وارن", role="moderator", category="moderation",
         description="دادن اخطار به کاربر", usage="اخطار [دلیل]")
async def cmd_warn(ctx: CommandContext) -> None:
    if not await require(ctx, role="moderator"):
        return
    target, _, reason = await _target(ctx)
    if target is None or not await guard_target(ctx, target):
        return
    reply_id = ctx.message.reply_to_message.message_id if ctx.message.reply_to_message else None
    text, result = await mod.warn_user(ctx.bot, ctx.session, chat_id=ctx.chat_id,
                                       actor=ctx.actor, target_id=target.user_id,
                                       target_name=target.full_name, reason=reason,
                                       delete_message_id=reply_id, chat_title=ctx.chat_title)
    keyboard = markup(_warn_card_buttons(ctx.chat_id, target.user_id)) if not result.get("threshold") else None
    await ctx.reply(text, reply_markup=keyboard, reply_to=False)
    await ctx.delete_invocation()


@command("کسر اخطار", "رفع اخطار", "حذف اخطار", role="moderator",
         category="moderation", description="کم کردن یک اخطار از کاربر")
async def cmd_unwarn(ctx: CommandContext) -> None:
    if not await require(ctx, role="moderator"):
        return
    target, _, _ = await _target(ctx)
    if target is None or not await guard_target(ctx, target):
        return
    text = await mod.unwarn_user(ctx.bot, ctx.session, chat_id=ctx.chat_id, actor=ctx.actor,
                                 target_id=target.user_id, target_name=target.full_name,
                                 chat_title=ctx.chat_title)
    await ctx.reply(text, reply_to=False)
    await ctx.delete_invocation()


@command("صفر کردن اخطار", "پاک کردن اخطارها", "ریست اخطار", "صفر کردن",
         role="moderator", category="moderation", description="صفر کردن اخطارهای کاربر")
async def cmd_reset_warn(ctx: CommandContext) -> None:
    if not await require(ctx, role="moderator"):
        return
    target, _, _ = await _target(ctx)
    if target is None or not await guard_target(ctx, target):
        return
    count = await mod.reset_warnings(ctx.session, ctx.chat_id, target.user_id, by_id=ctx.user_id)
    await ctx.reply(f"♻️ {to_persian_digits(str(count))} اخطار از "
                    f"{user_html(target.user_id, target.full_name)} پاک شد.", reply_to=False)
    await ctx.delete_invocation()


@command("وضعیت اخطار", "اخطارها", role="moderator", category="moderation",
         description="نمایش وضعیت اخطار کاربر")
async def cmd_warn_status(ctx: CommandContext) -> None:
    target, _, _ = await _target(ctx)
    if target is None or not target.user_id:
        await ctx.reply("❌ کاربر هدف مشخص نشد. روی پیام کاربر ریپلای کنید.")
        return
    count, limit, records = await mod.warn_status(ctx.session, ctx.chat_id, target.user_id)
    last_reason = records[0].reason if records else ""
    last_time = persian_datetime(records[0].created_at) if records else ""
    from ..services.warnings import warn_card

    text = warn_card(user_html(target.user_id, target.full_name), count, limit,
                     last_reason, last_time, target.user_id)
    await ctx.reply(text, reply_markup=markup(_warn_card_buttons(ctx.chat_id, target.user_id)))


@command("تاریخچه", "سابقه", "سوابق", role="moderator", category="moderation",
         description="نمایش سوابق مدیریتی کاربر")
async def cmd_history(ctx: CommandContext) -> None:
    if not await require(ctx, role="moderator"):
        return
    target, _, _ = await _target(ctx)
    if target is None or not target.user_id:
        await ctx.reply("❌ کاربر هدف مشخص نشد.")
        return
    actions = await mod.user_history(ctx.session, ctx.chat_id, target.user_id)
    name = await display_name(ctx.session, target.user_id)
    await ctx.reply(mod.history_text(actions, title=f"📜 تاریخچه {name}"))


@command("تعداد اخطار", "سقف اخطار", role="admin", permission="manage",
         category="moderation", description="تنظیم سقف اخطار", usage="تعداد اخطار ۴")
async def cmd_warn_limit(ctx: CommandContext) -> None:
    if not await require(ctx, role="admin", permission="manage"):
        return
    from ..core.duration import parse_duration

    raw = (ctx.arg_text or "").strip()
    if not raw:
        await ctx.reply(f"🔢 سقف فعلی اخطار: {to_persian_digits(str(ctx.settings.get('warn_limit', 4)))}\n"
                        "برای تغییر: <code>تعداد اخطار ۵</code>")
        return
    digits = "".join(ch for ch in raw if ch.isdigit() or ch in "۰۱۲۳۴۵۶۷۸۹")
    if not digits:
        await ctx.reply("❌ لطفاً یک عدد وارد کنید. مثال: <code>تعداد اخطار ۵</code>")
        return
    from ..core.normalization import normalize_digits

    value = int(normalize_digits(digits, to="ascii"))
    if not 1 <= value <= 20:
        await ctx.reply("❌ سقف اخطار باید بین ۱ تا ۲۰ باشد.")
        return
    from ..services.chat_state import get_settings, invalidate_settings

    settings_obj = await get_settings(ctx.session, ctx.chat_id)
    settings_obj.warn_limit = value
    invalidate_settings(ctx.chat_id)
    await ctx.reply(f"✅ سقف اخطار گروه روی {to_persian_digits(str(value))} تنظیم شد.")


@command("اقدام اخطار", role="admin", permission="manage", category="moderation",
         description="تعیین اقدام هنگام رسیدن به سقف اخطار")
async def cmd_warn_action(ctx: CommandContext) -> None:
    if not await require(ctx, role="admin", permission="manage"):
        return
    raw = (ctx.arg_text or "").strip()
    mapping = {
        "هیچ": "none", "بدون اقدام": "none", "none": "none",
        "اخطار": "warn", "فقط اخطار": "warn",
        "سکوت": "mute", "سکوت موقت": "temp_mute", "میوت": "mute",
        "اخراج": "kick", "کیک": "kick",
        "بن": "ban", "بن موقت": "temp_ban",
    }
    from ..core.normalization import normalize_text

    key = mapping.get(normalize_text(raw, mode="command"))
    if key is None:
        options = "\n".join(f"• <code>اقدام اخطار {name}</code>" for name in
                            ["بدون اقدام", "سکوت", "سکوت موقت", "اخراج", "بن", "بن موقت"])
        await ctx.reply("🎯 اقدام نامعتبر است. گزینه‌های مجاز:\n" + options)
        return
    from ..services.chat_state import get_settings, invalidate_settings

    settings_obj = await get_settings(ctx.session, ctx.chat_id)
    settings_obj.warn_action = key
    invalidate_settings(ctx.chat_id)
    await ctx.reply(f"✅ اقدام هنگام رسیدن به سقف اخطار: {PUNISHMENT_OPTIONS_FA.get(key, key)}")


@command("لیست بن", "لیست بن‌ها", "بن‌ها", role="moderator", category="moderation",
         description="نمایش کاربران بن‌شده")
async def cmd_ban_list(ctx: CommandContext) -> None:
    if not await require(ctx, role="moderator"):
        return
    result = await ctx.session.execute(
        select(ModerationAction).where(ModerationAction.chat_id == ctx.chat_id,
                                       ModerationAction.action.in_(["ban", "temp_ban"]),
                                       ModerationAction.active.is_(True))
        .order_by(ModerationAction.created_at.desc()).limit(40)
    )
    rows = list(result.scalars().all())
    if not rows:
        await ctx.reply("✅ هیچ کاربر بن‌شده فعالی ثبت نشده است.")
        return
    lines = [f"🔨 <b>لیست بن‌شده‌ها</b> ({to_persian_digits(str(len(rows)))} مورد)", ""]
    for item in rows:
        name = await display_name(ctx.session, item.target_id)
        until = f" | ⏱ {persian_datetime(item.expires_at)}" if item.expires_at else ""
        lines.append(f"• {user_html(item.target_id, name)}{until}\n   📌 {item.reason or '—'}")
    await ctx.reply("\n".join(lines))


@command("لیست سکوت", "لیست میوت", "سکوت‌ها", role="moderator", category="moderation",
         description="نمایش کاربران بی‌صدا شده")
async def cmd_mute_list(ctx: CommandContext) -> None:
    if not await require(ctx, role="moderator"):
        return
    result = await ctx.session.execute(
        select(ModerationAction).where(ModerationAction.chat_id == ctx.chat_id,
                                       ModerationAction.action.in_(["mute", "temp_mute"]),
                                       ModerationAction.active.is_(True))
        .order_by(ModerationAction.created_at.desc()).limit(40)
    )
    rows = list(result.scalars().all())
    if not rows:
        await ctx.reply("✅ هیچ کاربر بی‌صدای فعالی ثبت نشده است.")
        return
    lines = [f"🔇 <b>لیست بی‌صداها</b> ({to_persian_digits(str(len(rows)))} مورد)", ""]
    for item in rows:
        name = await display_name(ctx.session, item.target_id)
        until = f" | ⏱ {format_duration(item.duration)}" if item.duration else ""
        lines.append(f"• {user_html(item.target_id, name)}{until}")
    await ctx.reply("\n".join(lines))


@command("اطلاعات کاربر", "اطلاعات", "آیدی", "ایدی", role="member", category="members",
         description="نمایش اطلاعات کاربر", allow_in_private=True)
async def cmd_user_info(ctx: CommandContext) -> None:
    target, _, _ = await _target(ctx)
    user_id = target.user_id if target and target.user_id else ctx.user_id
    from ..services import permissions

    member = await permissions.fetch_chat_member(ctx.bot, ctx.chat_id, user_id) if ctx.is_group else None
    user = await ctx.session.get(User, user_id)
    state_result = await ctx.session.execute(
        select(ChatMemberState).where(ChatMemberState.chat_id == ctx.chat_id,
                                      ChatMemberState.user_id == user_id)
    )
    state = state_result.scalar_one_or_none()
    warns_result = await ctx.session.execute(
        select(Warning).where(Warning.chat_id == ctx.chat_id, Warning.user_id == user_id,
                              Warning.active.is_(True))
    )
    warns = len(warns_result.scalars().all())
    from ..services.reputation import get_score

    positive, negative, score = await get_score(ctx.session, ctx.chat_id, user_id)
    name = f"{user.first_name or ''} {user.last_name or ''}".strip() if user else "—"
    lines = [
        "ℹ️ <b>اطلاعات کاربر</b>",
        "",
        f"👤 نام: {name or '—'}",
        f"🆔 نام کاربری: {'@' + user.username if user and user.username else '—'}",
        f"🔢 شناسه: <code>{user_id}</code>",
        f"🤖 ربات: {'بله' if user and user.is_bot else 'خیر'}",
    ]
    if ctx.is_group:
        lines.append(f"🎖 سمت: {role_name_fa(state.bot_role if state else None,
                                             member.status if member else None)}")
        lines.append(f"⚠️ اخطارها: {to_persian_digits(str(warns))} از "
                     f"{to_persian_digits(str(ctx.settings.get('warn_limit', 4)))}")
        lines.append(f"⭐️ کاربر ویژه: {'بله' if state and state.is_trusted else 'خیر'}")
        live_tag = getattr(member, "tag", None) or getattr(member, "custom_title", None)
        shown_tag = live_tag or (state.tag if state and state.tag else None)
        lines.append(f"🏷 تگ: {shown_tag or '—'}"
                     + (" (در تلگرام)" if live_tag else ""))
        lines.append(f"📨 تعداد پیام: {to_persian_digits(str(state.message_count if state else 0))}")
        lines.append(f"⭐️ اعتبار: {to_persian_digits(str(score))} "
                     f"(👍 {to_persian_digits(str(positive))} | 👎 {to_persian_digits(str(negative))})")
    if user and user.afk:
        lines.append(f"💤 AFK: {user.afk_reason or 'بله'}")
    await ctx.reply("\n".join(lines))
