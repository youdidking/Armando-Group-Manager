"""MTProto-only scanner for Telegram users marked with User.deleted."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot
from sqlalchemy import select
from telethon import TelegramClient, functions, types, utils
from telethon.errors import ChannelInvalidError, FloodWaitError

from .. import config
from ..core.errors import schedule_delete
from ..core.normalization import to_persian_digits
from ..db import session_scope
from ..db.models import Chat, ChatSettings

logger = logging.getLogger("armando.deleted_accounts")
_PAGE_SIZE = 200
_MAX_CHANNEL_PAGES = 50
_PAGE_PAUSE_SECONDS = 0.1


def is_deleted_mtproto_user(user: object) -> bool:
    """Use the protocol's explicit deleted flag; never infer from a display name."""
    return isinstance(user, types.User) and bool(getattr(user, "deleted", False))


class DeletedAccountScanner:
    def __init__(self, bot: Bot, *, api_id: int, api_hash: str,
                 bot_token: str, session_path: Path) -> None:
        session_path.parent.mkdir(parents=True, exist_ok=True)
        self.bot = bot
        self.client = TelegramClient(
            str(session_path), api_id, api_hash,
            flood_sleep_threshold=60,
            connection_retries=1,
            request_retries=1,
        )
        self.bot_token = bot_token

    async def connect(self) -> None:
        await self.client.start(bot_token=self.bot_token)
        if not await self.client.is_user_authorized():
            raise RuntimeError("اتصال MTProto مجاز نشد")

    async def disconnect(self) -> None:
        try:
            await self.client.disconnect()
        except Exception as exc:  # noqa: BLE001
            logger.info("بستن نشست MTProto ناموفق بود: %s", exc.__class__.__name__)

    async def run_periodically(self, stop_event: asyncio.Event, interval: int) -> None:
        while not stop_event.is_set():
            try:
                await self.scan_active_chats()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("اجرای اسکن حساب‌های حذف‌شده ناموفق بود: %s",
                               exc.__class__.__name__)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=max(3600, int(interval)))
            except asyncio.TimeoutError:
                continue

    async def scan_active_chats(self) -> None:
        async with session_scope() as session:
            result = await session.execute(
                select(Chat.id, Chat.type).where(
                    Chat.is_active.is_(True),
                    Chat.type.in_(("group", "supergroup", "channel")),
                ).order_by(Chat.id)
            )
            chats = [(int(row[0]), str(row[1])) for row in result.all()]

        for chat_id, chat_type in chats:
            try:
                deleted_ids, incomplete = await self._deleted_user_ids(chat_id, chat_type)
            except asyncio.CancelledError:
                raise
            except FloodWaitError as exc:
                logger.warning("تلگرام اسکن چت %s را به‌دلیل محدودیت نرخ متوقف کرد؛ %s ثانیه",
                               chat_id, int(exc.seconds))
                await asyncio.sleep(min(max(1, int(exc.seconds)), 300))
                continue
            except Exception as exc:  # noqa: BLE001 - one inaccessible chat must not stop others
                logger.info("فهرست اعضای چت %s در دسترس نبود: %s",
                            chat_id, exc.__class__.__name__)
                continue

            if deleted_ids or incomplete:
                await self._ban_and_report(chat_id, deleted_ids, incomplete=incomplete)
            await asyncio.sleep(0.05)

    async def _resolve_input_channel(self, channel_id: int, *, refresh: bool = False):
        """Resolve this session's channel access hash before participant calls.

        Telethon's normal resolver first consults its entity/session cache and,
        for a bot, may use channels.getChannels with a zero hash to obtain a
        usable Channel entity. A zero hash is never sent to getParticipants.
        """
        if refresh:
            # A cached hash can become unusable after a channel migration or
            # access change. Re-bootstrap it through the bot-supported lookup.
            result = await self.client(functions.channels.GetChannelsRequest(
                id=[types.InputChannel(channel_id=channel_id, access_hash=0)]
            ))
            entity = next((item for item in getattr(result, "chats", [])
                           if getattr(item, "id", None) == channel_id
                           and getattr(item, "access_hash", None) is not None), None)
            if entity is None:
                raise ValueError("کانال از نشست MTProto قابل resolve نیست")
            return types.InputChannel(channel_id=channel_id,
                                      access_hash=int(entity.access_hash))

        input_peer = await self.client.get_input_entity(types.PeerChannel(channel_id))
        return utils.get_input_channel(input_peer)

    def _participants_request(self, input_channel, offset: int):
        return functions.channels.GetParticipantsRequest(
            channel=input_channel,
            filter=types.ChannelParticipantsSearch(""),
            offset=offset,
            limit=_PAGE_SIZE,
            hash=0,
        )

    async def _deleted_user_ids(self, chat_id: int, chat_type: str) -> tuple[list[int], bool]:
        # The marked Bot API ID is authoritative for choosing Chat vs Channel.
        # Chat.type can be stale (for example, a group migrated to a supergroup).
        peer_id, peer_class = utils.resolve_id(chat_id)
        if peer_class is types.PeerChat:
            result = await self.client(
                functions.messages.GetFullChatRequest(chat_id=peer_id)
            )
            ids = sorted({int(user.id) for user in getattr(result, "users", [])
                          if is_deleted_mtproto_user(user)})
            return ids, False

        if peer_class is not types.PeerChannel:
            raise ValueError(f"شناسهٔ چت {chat_id} از نوع گروه یا کانال نیست")

        input_channel = await self._resolve_input_channel(peer_id)
        offset = 0
        deleted: set[int] = set()
        incomplete = False
        refreshed_hash = False

        for page in range(_MAX_CHANNEL_PAGES):
            try:
                result = await self.client(self._participants_request(input_channel, offset))
            except FloodWaitError:
                # Keep useful results from earlier pages and report that this
                # scan ended early rather than discarding them.
                incomplete = True
                break
            except ChannelInvalidError:
                if refreshed_hash:
                    raise
                # Retry once after explicitly fetching a current entity/access
                # hash through getChannels; never retry with hash=0 here.
                try:
                    input_channel = await self._resolve_input_channel(peer_id, refresh=True)
                    refreshed_hash = True
                    result = await self.client(
                        self._participants_request(input_channel, offset)
                    )
                except FloodWaitError:
                    incomplete = True
                    break

            for user in getattr(result, "users", []):
                if is_deleted_mtproto_user(user):
                    deleted.add(int(user.id))
            count = len(getattr(result, "participants", []) or [])
            if count == 0 or count < _PAGE_SIZE:
                break
            offset += count
            if page + 1 == _MAX_CHANNEL_PAGES:
                incomplete = True
            else:
                await asyncio.sleep(_PAGE_PAUSE_SECONDS)
        return sorted(deleted), incomplete

    async def _report_delete_after(self, chat_id: int) -> int:
        """Read the per-group report delay; preserve the 12s default on DB errors."""
        try:
            async with session_scope() as session:
                value = await session.scalar(
                    select(ChatSettings.report_delete_after).where(
                        ChatSettings.chat_id == chat_id))
            return 12 if value is None else max(0, min(600, int(value)))
        except Exception as exc:  # noqa: BLE001 - a report should still be sent
            logger.info("could not load report deletion delay for chat %s: %s",
                        chat_id, exc.__class__.__name__)
            return 12

    async def _ban_and_report(self, chat_id: int, user_ids: list[int], *,
                              incomplete: bool = False) -> None:
        banned: list[int] = []
        failed: list[int] = []
        for user_id in user_ids:
            try:
                # Omitting until_date creates a permanent ban. No permission is
                # added or elevated by this code; Telegram enforces bot rights.
                await self.bot.ban_chat_member(
                    chat_id=chat_id, user_id=user_id, revoke_messages=False)
                banned.append(user_id)
            except Exception as exc:  # noqa: BLE001
                failed.append(user_id)
                logger.info("بن دائمی شناسهٔ حذف‌شده %s در چت %s نشد: %s",
                            user_id, chat_id, exc.__class__.__name__)
            await asyncio.sleep(0.05)

        logger.info("نتیجهٔ بن حساب‌های حذف‌شده در چت %s: %s موفق، %s ناموفق",
                    chat_id, len(banned), len(failed))
        lines = ["🧹 <b>بررسی حساب‌های حذف‌شده</b>"]
        if banned:
            lines.append(f"✅ بن دائمی شد: {to_persian_digits(str(len(banned)))} حساب")
            shown = "، ".join(to_persian_digits(str(value)) for value in banned[:20])
            lines.append(f"🆔 شناسه‌ها: <code>{shown}</code>")
            if len(banned) > 20:
                lines.append(f"… و {to_persian_digits(str(len(banned) - 20))} مورد دیگر.")
        if failed:
            lines.append(f"⚠️ بن انجام نشد: {to_persian_digits(str(len(failed)))} حساب")
            shown = "، ".join(to_persian_digits(str(value)) for value in failed[:20])
            lines.append(f"🆔 شناسه‌های ناموفق: <code>{shown}</code>")
            lines.append("دسترسی محدودسازی/مسدودکردن اعضا و وضعیت عضویت را بررسی کنید.")
        if incomplete:
            lines.append("⚠️ این نوبت همهٔ اعضا بررسی نشد؛ تلگرام یا سقف اسکن ادامهٔ فهرست را محدود کرد.")
        lines.append("ℹ️ فهرست اعضا ممکن است به‌علت محدودیت تلگرام کامل نباشد.")
        text = "\n".join(lines)
        delete_after = await self._report_delete_after(chat_id)
        try:
            report = await self.bot.send_message(
                chat_id=chat_id, text=text,
                parse_mode="HTML", disable_web_page_preview=True)
            if report is not None:
                schedule_delete(self.bot, chat_id, report.message_id, delete_after,
                                context="deleted_accounts_report_autodelete")
        except Exception as exc:  # noqa: BLE001 - channel posting rights can differ from ban rights
            logger.info("ارسال گزارش اسکن به چت %s ناموفق بود: %s",
                        chat_id, exc.__class__.__name__)
            owner_id = config.settings.owner_id
            if owner_id:
                try:
                    report = await self.bot.send_message(
                        chat_id=owner_id,
                        text=f"⚠️ گزارش پاکسازی چت <code>{chat_id}</code>\n{text}",
                        parse_mode="HTML", disable_web_page_preview=True)
                    if report is not None:
                        schedule_delete(self.bot, owner_id, report.message_id, delete_after,
                                        context="deleted_accounts_owner_report_autodelete")
                except Exception:  # noqa: BLE001 - owner may not have opened the bot
                    pass
