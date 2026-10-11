"""Middlewares: database session, throttling and light chat tracking."""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.ratelimit import callback_limiter
from ..db.base import get_sessionmaker

logger = logging.getLogger("armando.middleware")


class DbSessionMiddleware(BaseMiddleware):
    """Provide a database session per update and commit when the handler ends."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if "session" in data:  # already provided (tests)
            return await handler(event, data)
        maker = get_sessionmaker()
        async with maker() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
            finally:
                data.pop("session", None)


class ThrottlingMiddleware(BaseMiddleware):
    """Basic flood protection for the bot itself (callbacks and messages)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            key = (event.from_user.id if event.from_user else 0, "cb")
            allowed, retry = callback_limiter.check(key)
            if not allowed:
                try:
                    await event.answer(f"⏳ کمی صبر کنید ({int(retry)} ثانیه)", show_alert=False)
                except Exception:  # noqa: BLE001
                    pass
                return None
        return await handler(event, data)


class ChatTrackingMiddleware(BaseMiddleware):
    """Keep chat metadata fresh (cheap: only touches a few rows per update)."""

    def __init__(self) -> None:
        self._last_touch: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession | None = data.get("session")
        message = event if isinstance(event, Message) else getattr(event, "message", None)
        if session is not None and message is not None and getattr(message, "chat", None):
            chat = message.chat

            # Telegram never announces a profile edit, so every update is one
            # more chance to notice that somebody renamed themselves.
            actor = getattr(message, "from_user", None)
            if actor is not None and chat.type in {"group", "supergroup"}:
                try:
                    from ..services.identity_watch import watch_identity

                    bot = data.get("bot")
                    if bot is not None:
                        await watch_identity(bot, session, chat.id, actor)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("identity watch skipped: %s", exc)

            now = time.monotonic()
            last = self._last_touch.get(chat.id, 0.0)
            if now - last > 60:  # at most once a minute per chat
                self._last_touch[chat.id] = now
                try:
                    from ..db.models import Chat

                    row = await session.get(Chat, chat.id)
                    if row is not None:
                        from datetime import datetime

                        row.last_seen_at = datetime.utcnow()
                        if chat.title and row.title != chat.title:
                            row.title = chat.title
                except Exception as exc:  # noqa: BLE001
                    logger.debug("chat tracking failed: %s", exc)
        return await handler(event, data)
