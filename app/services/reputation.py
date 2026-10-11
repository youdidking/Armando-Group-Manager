"""Simple + / - reputation system with anti-abuse rules."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.normalization import to_persian_digits
from ..db.models import Reputation, User
from .chat_state import get_member_state

logger = logging.getLogger("armando.reputation")

POSITIVE_MARKS = ("+", "＋", "➕", "👍", "💯", "افزایش اعتبار")
NEGATIVE_MARKS = ("-", "－", "➖", "👎", "کاهش اعتبار")

VOTE_COOLDOWN_SECONDS = 60 * 60 * 24  # one meaningful vote per voter/target per day


def is_positive_mark(text: str) -> bool:
    stripped = (text or "").strip()
    return stripped in ("+", "＋", "➕", "👍") or stripped.startswith("+")


def is_negative_mark(text: str) -> bool:
    stripped = (text or "").strip()
    return stripped in ("-", "－", "➖", "👎") or stripped.startswith("-")


async def _get_or_create(session: AsyncSession, chat_id: int, user_id: int) -> Reputation:
    result = await session.execute(
        select(Reputation).where(Reputation.chat_id == chat_id, Reputation.user_id == user_id)
    )
    rep = result.scalar_one_or_none()
    if rep is None:
        rep = Reputation(chat_id=chat_id, user_id=user_id, positive=0, negative=0, score=0,
                         voters={}, updated_at=datetime.utcnow())
        session.add(rep)
        await session.flush()
    return rep


async def add_vote(session: AsyncSession, chat_id: int, voter_id: int, target_id: int,
                   positive: bool) -> tuple[bool, str, int]:
    if voter_id == target_id:
        return False, "⛔️ نمی‌توانید به خودتان امتیاز بدهید.", 0
    rep = await _get_or_create(session, chat_id, target_id)
    voters = dict(rep.voters or {})
    last = voters.get(str(voter_id))
    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (datetime.utcnow() - last_dt).total_seconds() < VOTE_COOLDOWN_SECONDS:
                return False, "⏳ شما اخیراً به این کاربر امتیاز داده‌اید.", int(rep.score or 0)
        except ValueError:
            pass
    if positive:
        rep.positive = int(rep.positive or 0) + 1
        rep.score = int(rep.score or 0) + 1
    else:
        rep.negative = int(rep.negative or 0) + 1
        rep.score = int(rep.score or 0) - 1
    voters[str(voter_id)] = datetime.utcnow().isoformat()
    rep.voters = voters
    rep.updated_at = datetime.utcnow()
    state = await get_member_state(session, chat_id, target_id)
    state.reputation_total = int(rep.score or 0)
    await session.flush()
    return True, "", int(rep.score or 0)


async def get_score(session: AsyncSession, chat_id: int, user_id: int) -> tuple[int, int, int]:
    rep = await _get_or_create(session, chat_id, user_id)
    return int(rep.positive or 0), int(rep.negative or 0), int(rep.score or 0)


async def reset_score(session: AsyncSession, chat_id: int, user_id: int) -> None:
    rep = await _get_or_create(session, chat_id, user_id)
    rep.positive = 0
    rep.negative = 0
    rep.score = 0
    rep.voters = {}
    rep.updated_at = datetime.utcnow()
    await session.flush()


async def reset_all(session: AsyncSession, chat_id: int) -> int:
    result = await session.execute(delete(Reputation).where(Reputation.chat_id == chat_id))
    await session.flush()
    return int(result.rowcount or 0)


async def leaderboard(session: AsyncSession, chat_id: int, limit: int = 10) -> list[tuple[int, int, int, int]]:
    result = await session.execute(
        select(Reputation).where(Reputation.chat_id == chat_id)
        .order_by(Reputation.score.desc()).limit(limit)
    )
    return [(int(r.user_id), int(r.positive or 0), int(r.negative or 0), int(r.score or 0))
            for r in result.scalars().all()]


async def leaderboard_text(session: AsyncSession, chat_id: int, limit: int = 10) -> str:
    rows = await leaderboard(session, chat_id, limit)
    if not rows:
        return "🏅 جدول اعتبار\n\nهنوز امتیازی ثبت نشده است."
    lines = ["🏅 <b>جدول اعتبار کاربران</b>", ""]
    medals = ["🥇", "🥈", "🥉"]
    from .chat_state import display_names

    names = await display_names(session, [row[0] for row in rows])
    for index, (user_id, positive, negative, score) in enumerate(rows, start=1):
        name = names.get(int(user_id)) or str(user_id)
        medal = medals[index - 1] if index <= 3 else f"{to_persian_digits(str(index))}."
        lines.append(f"{medal} {name} — ⭐️ {to_persian_digits(str(score))} "
                     f"(👍 {to_persian_digits(str(positive))} | 👎 {to_persian_digits(str(negative))})")
    return "\n".join(lines)
