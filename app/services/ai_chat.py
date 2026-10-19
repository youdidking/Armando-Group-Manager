"""Opt-in, short-context Gemini chat. Nothing is persisted to the database."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import aiohttp
from aiogram import Bot
from aiogram.types import Message

from .. import config
from ..core.normalization import normalize_text

logger = logging.getLogger("armando.ai_chat")
_MAX_PROMPT_CHARS = 1500
_MAX_REPLY_CHARS = 3800
_CONTEXT_TTL = 1800
_MAX_TURNS = 8
_COOLDOWN_SECONDS = 3.0
_history: dict[tuple[int, int], tuple[float, list[dict[str, Any]]]] = {}
_last_request: dict[tuple[int, int], float] = {}
_locks: dict[tuple[int, int], asyncio.Lock] = {}


def extract_ai_prompt(text: str) -> str | None:
    """Return the body after the explicit Persian prefix, or None."""
    raw = (text or "").strip()
    target = normalize_text("آرماندو بگو", mode="command").split()
    if not raw:
        return None
    found: list[str] = []
    end = 0
    for match in re.finditer(r"\S+", raw):
        words = normalize_text(match.group(0), mode="command").split()
        if not words:
            continue
        # A colon immediately after «بگو» is accepted as punctuation.
        if len(found) + len(words) == len(target) and words:
            words[-1] = words[-1].rstrip(":،,؛!؟?")
        found.extend(words)
        if target[:len(found)] != found:
            return None
        end = match.end()
        if found == target:
            return raw[end:].strip()
    return None


def build_gemini_payload(history: list[dict[str, Any]], prompt: str) -> dict[str, Any]:
    contents = list(history[-_MAX_TURNS:])
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    return {
        "systemInstruction": {
            "parts": [{"text": (
                "تو دستیار فارسیِ بات Armando Group Manager هستی. "
                "به‌طور پیش‌فرض روان، کوتاه و محترمانه به فارسی پاسخ بده. "
                "اگر کاربر صریحاً ترجمه یا متنی به زبان دیگری خواست، همان را انجام بده."
            )}]
        },
        "contents": contents,
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1200},
    }


def _trim_history(key: tuple[int, int], now: float) -> list[dict[str, Any]]:
    stored = _history.get(key)
    if stored is None or now - stored[0] > _CONTEXT_TTL:
        _history.pop(key, None)
        return []
    return list(stored[1])


async def _reply(bot: Bot, message: Message, text: str) -> None:
    await bot.send_message(chat_id=message.chat.id, text=text,
                           reply_to_message_id=message.message_id,
                           parse_mode=None, disable_web_page_preview=True)


async def handle_ai_message(message: Message, bot: Bot) -> bool:
    """Handle a message beginning with «آرماندو بگو» without raising outward."""
    prompt = extract_ai_prompt(message.text or message.caption or "")
    if prompt is None:
        return False

    settings = config.settings
    if not settings.ai_chat_enabled or not settings.gemini_api_key:
        try:
            await _reply(bot, message,
                         "ℹ️ گفت‌وگوی هوشمند هنوز فعال نشده است؛ مدیر ربات باید آن را با کلید Gemini فعال کند.")
        except Exception:  # noqa: BLE001
            pass
        return True
    if not prompt:
        try:
            await _reply(bot, message, "💬 بعد از «آرماندو بگو» پرسش خود را بنویسید.")
        except Exception:  # noqa: BLE001
            pass
        return True
    if len(prompt) > _MAX_PROMPT_CHARS:
        try:
            await _reply(bot, message, "⚠️ متن پرسش بیش از حد طولانی است؛ کوتاه‌تر بنویسید.")
        except Exception:  # noqa: BLE001
            pass
        return True

    user_id = message.from_user.id if message.from_user else 0
    key = (message.chat.id, user_id)
    now = time.monotonic()
    expired = [old_key for old_key, last in _last_request.items()
               if now - last > _CONTEXT_TTL
               and old_key != key
               and (old_key not in _locks or not _locks[old_key].locked())]
    for old_key in expired:
        _history.pop(old_key, None)
        _last_request.pop(old_key, None)
        _locks.pop(old_key, None)
    previous_request = _last_request.get(key)
    if previous_request is not None and now - previous_request < _COOLDOWN_SECONDS:
        try:
            await _reply(bot, message, "⏳ چند لحظه صبر کنید و دوباره بپرسید.")
        except Exception:  # noqa: BLE001
            pass
        return True
    _last_request[key] = now
    lock = _locks.setdefault(key, asyncio.Lock())

    async with lock:
        try:
            history = _trim_history(key, time.monotonic())
            payload = build_gemini_payload(history, prompt)
            model = settings.gemini_model or "gemini-3.8-flash"
            url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model}:generateContent")
            timeout = aiohttp.ClientTimeout(total=20)
            headers = {"x-goog-api-key": settings.gemini_api_key,
                       "Content-Type": "application/json"}
            async with aiohttp.ClientSession(timeout=timeout) as client:
                async with client.post(url, json=payload, headers=headers) as response:
                    data = await response.json(content_type=None)
                    if response.status >= 400:
                        logger.warning("پاسخ Gemini با کد %s دریافت شد.", response.status)
                        raise RuntimeError("Gemini request failed")
            candidates = data.get("candidates") or []
            parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
            answer = "".join(part.get("text", "") for part in parts
                             if isinstance(part, dict)).strip()
            if not answer:
                raise RuntimeError("Gemini response contained no text")
            if len(answer) > _MAX_REPLY_CHARS:
                answer = answer[:_MAX_REPLY_CHARS].rstrip() + "…"
            updated = history + [
                {"role": "user", "parts": [{"text": prompt}]},
                {"role": "model", "parts": [{"text": answer}]},
            ]
            _history[key] = (time.monotonic(), updated[-_MAX_TURNS:])
            await _reply(bot, message, answer)
        except Exception as exc:  # noqa: BLE001 - API and network failures must not crash the bot
            logger.warning("درخواست گفت‌وگوی Gemini ناموفق بود: %s", exc.__class__.__name__)
            try:
                await _reply(bot, message,
                             "⚠️ الان پاسخ هوشمند در دسترس نیست؛ کمی بعد دوباره امتحان کنید.")
            except Exception:  # noqa: BLE001
                pass
    return True
