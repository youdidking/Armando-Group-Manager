"""«تگ همه» / «منشن همه» - mention every member of the group, with real notices.

Telegram only notifies the first ~5 mentions of a message, so the members are
sent in small batches (see :mod:`app.services.mention_all`).
"""

from __future__ import annotations

import asyncio
import logging

from ..core.errors import safe_send
from ..core.normalization import to_persian_digits
from ..services import mention_all as mention_service
from .common import CommandContext, require
from .registry import command

logger = logging.getLogger("armando.handlers.mention_all")


@command("تگ همه", "منشن همه", "صدا زدن همه", "فراخوانی همه", "تگ اعضا", "منشن اعضا",
         "@all", role="admin", category="members", group_only=True,
         description="منشن کردن همهٔ اعضای گروه", usage="تگ همه")
async def cmd_mention_all(ctx: CommandContext) -> None:
    if not await require(ctx, role="admin"):
        return

    caller = ctx.message.from_user
    caller_pair = None
    if caller is not None and not caller.is_bot:
        caller_pair = (caller.id, mention_service.display_name(
            caller.first_name, caller.last_name, caller.username))

    members = await mention_service.collect(ctx.session, ctx.chat_id, bot=ctx.bot,
                                            caller=caller_pair)
    if not members:
        await ctx.reply(
            "📣 هنوز عضوی در این گروه ثبت نشده است.\n"
            "ربات فقط اعضایی را می‌شناسد که بعد از حضورش در گروه پیام فرستاده، "
            "ری‌اکت زده یا وارد شده باشند.")
        return

    batches = mention_service.build_batches(members)
    if not batches:
        return

    sent = 0
    for index, (text, entities) in enumerate(batches):
        # ``parse_mode=None`` is essential: the bot sets HTML as its global
        # default and Telegram then re-parses the text, throwing the mention
        # entities away (no entities -> nobody gets a notification).
        message = await safe_send(ctx.bot, ctx.chat_id, text, entities=entities,
                                  parse_mode=None, disable_web_page_preview=True)
        if message is not None:
            sent += 1
        if index + 1 < len(batches):
            await asyncio.sleep(mention_service.SEND_DELAY)

    if len(batches) > 1:
        logger.info("mention-all: %s members in %s messages (chat=%s)",
                    len(mention_service.mentioned_ids(batches)), sent, ctx.chat_id)
        await ctx.reply(
            "📣 برای اینکه همه واقعاً نوتیفیکیشن بگیرند، پیام در "
            f"{to_persian_digits(str(len(batches)))} بخش ارسال شد "
            "(تلگرام فقط به ۵ منشنِ اولِ هر پیام نوتیفیکیشن می‌فرستد).")
