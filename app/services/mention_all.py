"""Mention every member of a group (the «تگ همه» command).

Telegram never exposes the full member list to a bot, so the bot mentions the
members it has already seen in that chat: everybody who joined, wrote a
message, reacted or was touched by an admin (see :mod:`app.services.roster`).

Two Telegram rules shape the implementation:

1. **Only the first ~5 mentions of a message notify anybody** (and at most 50
   mentions fit in one message at all).  So the roster is sent in small
   batches of five - one message per batch - instead of one huge message.
2. A mention has to be a real ``text_mention`` entity carrying the user
   object; a plain link is silent.  The entities are therefore built by hand
   with UTF-16 offsets (that is what Telegram counts).
"""

from __future__ import annotations

import logging

from aiogram.types import MessageEntity
from aiogram.types import User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.normalization import to_persian_digits
from . import roster

logger = logging.getLogger("armando.mentionall")

# Telegram notifies only the first handful of mentions in a message.
MENTIONS_PER_MESSAGE = 5
# Safety rail: never flood a group with an endless stream of messages.
MAX_MENTION_MESSAGES = 40
# Pause between the batches (a 429 is handled by ``safe_call`` anyway).
SEND_DELAY = 1.0

TELEGRAM_MESSAGE_LIMIT = 4096
# Hard safety cap: a bot should never try to ping a whole city at once.
MAX_MEMBERS = 1000

HEADER = "📣 فراخوانی اعضا"


def _utf16_len(text: str) -> int:
    """Telegram counts entity offsets and lengths in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def mention_text(name: str) -> str:
    """The visible text of a mention (kept short and on a single line)."""
    return " ".join((name or "").split())[:64] or "عضو"


def display_name(first_name: str | None, last_name: str | None,
                 username: str | None) -> str:
    parts = [p for p in ((first_name or "").strip(), (last_name or "").strip()) if p]
    if parts:
        return " ".join(parts)
    if username:
        return f"@{username}"
    return "عضو"


async def collect(session: AsyncSession, chat_id: int, *,
                  caller: tuple[int, str] | None = None,
                  bot=None, limit: int = MAX_MEMBERS) -> list[tuple[int, str]]:
    """Everybody the bot knows in ``chat_id`` as ``(user_id, display_name)``.

    ``caller`` is the admin who ran the command: the bot must not forget to
    mention them just because they never typed anything in the group.
    ``bot`` lets the roster also read the real administrator list.
    """
    members = await roster.known_members(session, chat_id, bot=bot, limit=limit)
    if caller is not None:
        user_id, name = int(caller[0]), caller[1]
        if user_id > 0 and not any(uid == user_id for uid, _ in members):
            members.insert(0, (user_id, display_name(name, None, None)))
    return members


def build_batches(members, *, per_message: int = MENTIONS_PER_MESSAGE,
                  max_messages: int = MAX_MENTION_MESSAGES
                  ) -> list[tuple[str, list[MessageEntity]]]:
    """Split the roster into messages that actually notify their members.

    Returns ``(text, entities)`` pairs, ready for ``send_message``.
    """
    members = list(members)[: max(1, per_message) * max(1, max_messages)]
    if not members:
        return []

    batches = [members[index:index + per_message]
               for index in range(0, len(members), max(1, per_message))]
    total = len(batches)
    out: list[tuple[str, list[MessageEntity]]] = []

    for number, chunk in enumerate(batches, start=1):
        head = HEADER
        if total > 1:
            head = f"{HEADER} ({to_persian_digits(f'{number}/{total}')})"
        text = f"{head}\n"
        offset = _utf16_len(text)
        entities: list[MessageEntity] = []
        names: list[str] = []
        for user_id, name in chunk:
            visible = mention_text(name)
            entities.append(MessageEntity(
                type="text_mention", offset=offset, length=_utf16_len(visible),
                user=TelegramUser(id=int(user_id), is_bot=False, first_name=visible)))
            names.append(visible)
            offset += _utf16_len(visible) + 1   # + the separating space
        text += " ".join(names)
        out.append((text[:TELEGRAM_MESSAGE_LIMIT], entities))
    return out


def mentioned_ids(batches) -> list[int]:
    """Every user id mentioned in the batches (handy for logs and tests)."""
    ids: list[int] = []
    for _text, entities in batches:
        for entity in entities:
            user = getattr(entity, "user", None)
            if user is not None:
                ids.append(int(user.id))
    return ids
