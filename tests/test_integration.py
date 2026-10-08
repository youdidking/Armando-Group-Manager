"""End-to-end dispatcher tests with a mocked Telegram bot.

These tests push real ``Update`` objects through the real dispatcher, so they
catch wiring mistakes that unit tests cannot (wrong handler signatures, missing
imports, broken permission flow).  Telegram is never contacted - the bot object
is a mock.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import (
    CallbackQuery,
    Chat,
    ChatMemberAdministrator,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberOwner,
    ChatMemberUpdated,
    Message,
    MessageReactionUpdated,
    ReactionTypeEmoji,
    Update,
    User,
)

from app.keyboards.factory import cb

CHAT_ID = -1001234567890
OWNER_ID = 1000
MEMBER_ID = 2000
ADMIN_ID = 3000
BOT_ID = 999999
PROMOTED_ADMIN_ID = 4000  # an administrator the bot itself promoted (editable)
OUTSIDER_ID = 123456789   # a realistic numeric Telegram id (5+ digits)


# --------------------------------------------------------------------------- #
# Fake Telegram
# --------------------------------------------------------------------------- #
def _msg(**kwargs) -> Message:
    payload: dict = dict(
        message_id=7,
        date=int(time.time()),
        chat=Chat(id=CHAT_ID, type="supergroup", title="گروه تست"),
        from_user=User(id=BOT_ID, is_bot=True, first_name="Armando", username="armando_bot"),
        text="ok",
    )
    payload.update(kwargs)
    return Message(**payload)


def _bot_member(**rights) -> ChatMemberAdministrator:
    """The bot's own administrator record - the rights decide what may work."""
    payload = dict(
        user=User(id=BOT_ID, is_bot=True, first_name="Armando"),
        status="administrator", can_be_edited=False, can_manage_chat=True,
        can_delete_messages=True, can_restrict_members=True, can_promote_members=False,
        can_change_info=True, can_invite_users=True, can_pin_messages=True,
        can_manage_video_chats=True, can_post_messages=True, can_edit_messages=True,
        is_anonymous=False, can_post_stories=True, can_edit_stories=True,
        can_delete_stories=True, can_send_welcome_messages=True)
    payload.update(rights)
    return ChatMemberAdministrator(**payload)


def build_fake_bot(**bot_rights) -> AsyncMock:
    """An ``AsyncMock`` bot: every Telegram call is awaitable and recorded."""
    bot = AsyncMock(spec=Bot)
    bot.id = BOT_ID
    bot_member = _bot_member(**bot_rights)

    async def get_chat_member(chat_id: int, user_id: int):
        user = User(id=user_id, is_bot=user_id == BOT_ID, first_name="User")
        if user_id == OWNER_ID:
            return ChatMemberOwner(user=user, is_anonymous=False, custom_title="مالک",
                                   status="creator")
        if user_id == ADMIN_ID:
            return ChatMemberAdministrator(
                user=user, status="administrator", can_be_edited=False,
                can_manage_chat=True, can_delete_messages=True, can_restrict_members=True,
                can_promote_members=True, can_change_info=True, can_invite_users=True,
                can_pin_messages=True, can_manage_video_chats=True, can_post_messages=True,
                can_edit_messages=True, is_anonymous=False, can_post_stories=True,
                can_edit_stories=True, can_delete_stories=True,
                can_send_welcome_messages=True, custom_title="مدیر")
        if user_id == PROMOTED_ADMIN_ID:
            return ChatMemberAdministrator(
                user=user, status="administrator", can_be_edited=True,
                can_manage_chat=True, can_delete_messages=True, can_restrict_members=True,
                can_promote_members=False, can_change_info=False, can_invite_users=True,
                can_pin_messages=False, can_manage_video_chats=False, can_post_messages=True,
                can_edit_messages=True, is_anonymous=False, can_post_stories=True,
                can_edit_stories=True, can_delete_stories=True,
                can_send_welcome_messages=True)
        if user_id == BOT_ID:
            return bot_member
        return ChatMemberMember(user=user, status="member")

    bot.get_chat_member = AsyncMock(side_effect=get_chat_member)
    bot.get_me = AsyncMock(return_value=User(id=BOT_ID, is_bot=True,
                                             first_name="Armando", username="armando_bot"))
    known_usernames = {"parhannni": MEMBER_ID, "ali": MEMBER_ID, "admin": ADMIN_ID}

    async def get_chat(chat_id):
        if isinstance(chat_id, str) and chat_id.startswith("@"):
            user_id = known_usernames.get(chat_id[1:].lower())
            if user_id is not None:
                return Chat(id=user_id, type="private", first_name="User")
        return Chat(id=CHAT_ID, type="supergroup", title="گروه تست")

    bot.get_chat = AsyncMock(side_effect=get_chat)
    bot.get_chat_administrators = AsyncMock(return_value=[])
    bot.send_message = AsyncMock(return_value=_msg())
    bot.send_document = AsyncMock(return_value=_msg())
    bot.delete_message = AsyncMock(return_value=True)
    bot.restrict_chat_member = AsyncMock(return_value=True)
    bot.ban_chat_member = AsyncMock(return_value=True)
    bot.unban_chat_member = AsyncMock(return_value=True)
    bot.pin_chat_message = AsyncMock(return_value=True)
    bot.unpin_chat_message = AsyncMock(return_value=True)
    bot.unpin_all_chat_messages = AsyncMock(return_value=True)
    bot.answer_callback_query = AsyncMock(return_value=True)
    bot.promote_chat_member = AsyncMock(return_value=True)
    bot.set_chat_permissions = AsyncMock(return_value=True)
    bot.get_file = AsyncMock(return_value=None)
    bot.create_chat_invite_link = AsyncMock(
        return_value=SimpleNamespace(invite_link="https://t.me/joinchat/TESTLINK"))
    bot.export_chat_invite_link = AsyncMock(return_value="https://t.me/joinchat/TESTLINK")
    bot.set_chat_title = AsyncMock(return_value=True)
    bot.set_chat_description = AsyncMock(return_value=True)
    # aiogram routes `message.answer()` / `edit_text()` through Bot.__call__
    calls: list = []

    async def _dispatch(method, *args, **kwargs):  # records and "succeeds"
        calls.append(method)
        return True

    bot.side_effect = _dispatch
    bot.telegram_calls = calls
    bot.return_value = True
    return bot


_counter = {"id": 0}


def _next_update_id() -> int:
    _counter["id"] += 1
    return _counter["id"]


async def feed(dispatcher: Dispatcher, bot, message: Message) -> None:
    await dispatcher.feed_update(bot, Update(update_id=_next_update_id(), message=message))


def _raw_api_recorder(monkeypatch) -> list:
    """Record raw Bot API calls (used for tags/demote) instead of posting them."""
    calls: list = []

    async def _call(bot, method_name, payload, *, timeout=30):
        calls.append((method_name, dict(payload)))
        return True

    monkeypatch.setattr("app.core.telegram_extra.call_api_raw", _call)
    return calls


def _edit_count(bot) -> int:
    """How many EditMessageText calls went through the mocked Bot."""
    from aiogram.methods import EditMessageText

    return sum(1 for method in bot.telegram_calls if isinstance(method, EditMessageText))


def group_message(text: str, *, user_id: int = OWNER_ID, reply_to: Message | None = None,
                  **kwargs) -> Message:
    return Message(
        message_id=kwargs.pop("message_id", _next_update_id()),
        date=int(time.time()),
        chat=Chat(id=CHAT_ID, type="supergroup", title="گروه تست"),
        from_user=User(id=user_id, is_bot=False, first_name="Ali"),
        text=text,
        reply_to_message=reply_to,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_private_start_answers_in_persian(dispatcher, monkeypatch):
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    message = Message(
        message_id=1, date=int(time.time()),
        chat=Chat(id=OWNER_ID, type="private"),
        from_user=User(id=OWNER_ID, is_bot=False, first_name="Ali"),
        text="/start",
    )
    await feed(dispatcher, bot, message)
    assert bot.send_message.await_count >= 1
    text = bot.send_message.await_args.kwargs.get("text", "")
    assert "Armando" in text
    keyboard = bot.send_message.await_args.kwargs.get("reply_markup")
    buttons = [btn for row in keyboard.inline_keyboard for btn in row]
    texts = [btn.text for btn in buttons]
    assert any("سازنده بات" in item for item in texts), texts
    assert any("اضافه کردن به گروه" in item for item in texts), texts
    assert any((btn.url or "").lower() == "https://t.me/rvivl" for btn in buttons)
    assert not reported


@pytest.mark.asyncio
async def test_persian_ban_by_reply(dispatcher, monkeypatch):
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    target = group_message("سلام", user_id=MEMBER_ID)
    await feed(dispatcher, bot, group_message("بن ۲روز تبلیغ", reply_to=target))
    assert not reported, reported
    assert bot.ban_chat_member.await_count >= 1
    assert bot.ban_chat_member.await_args.kwargs.get("user_id") == MEMBER_ID


@pytest.mark.asyncio
async def test_profanity_message_is_deleted(dispatcher, monkeypatch):
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("این پیام شامل کلمه کیر است",
                                              user_id=MEMBER_ID))
    assert not reported, reported
    assert bot.delete_message.await_count >= 1


@pytest.mark.asyncio
async def test_ordinary_message_is_untouched(dispatcher, monkeypatch):
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("بنظرم این فیلم خوب بود", user_id=MEMBER_ID))
    assert not reported, reported
    assert bot.delete_message.await_count == 0
    assert bot.ban_chat_member.await_count == 0
    assert bot.restrict_chat_member.await_count == 0


@pytest.mark.asyncio
async def test_panel_command_and_callback(dispatcher, monkeypatch):
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("پنل"))
    assert not reported, reported
    assert bot.send_message.await_count >= 1

    callback = CallbackQuery(
        id="1", from_user=User(id=OWNER_ID, is_bot=False, first_name="Ali"),
        chat_instance="1", data=cb("panel", "main"),
        message=_msg(text="پنل"),
    )
    await dispatcher.feed_update(bot, Update(update_id=_next_update_id(), callback_query=callback))
    assert not reported, reported
    assert _edit_count(bot) >= 1, "panel callback must refresh the message"


@pytest.mark.asyncio
async def test_lock_panel_callback(dispatcher, monkeypatch):
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    callback = CallbackQuery(
        id="2", from_user=User(id=OWNER_ID, is_bot=False, first_name="Ali"),
        chat_instance="1", data=cb("panel", "locks"),
        message=_msg(text="پنل"),
    )
    await dispatcher.feed_update(bot, Update(update_id=_next_update_id(), callback_query=callback))
    assert not reported, reported
    assert _edit_count(bot) >= 1, "panel callback must refresh the message"


@pytest.mark.asyncio
async def test_member_cannot_ban(dispatcher, monkeypatch):
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    target = group_message("سلام", user_id=MEMBER_ID)
    await feed(dispatcher, bot, group_message("بن", user_id=MEMBER_ID, reply_to=target))
    assert not reported, reported
    assert bot.ban_chat_member.await_count == 0


@pytest.mark.asyncio
async def test_member_gets_a_personal_panel_only(dispatcher, monkeypatch):
    """Group management is never offered to ordinary members."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("پنل", user_id=MEMBER_ID))
    assert not reported, reported
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("فقط برای مدیران" in text for text in texts), texts


@pytest.mark.asyncio
async def test_admin_gets_the_management_panel(dispatcher, monkeypatch):
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("پنل", user_id=ADMIN_ID))
    assert not reported, reported
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert not any("فقط برای مدیران" in text for text in texts)
    assert any("Armando" in text for text in texts)
    keyboard = bot.send_message.await_args.kwargs.get("reply_markup")
    buttons = [btn.text for row in keyboard.inline_keyboard for btn in row]
    assert not any("سازنده" in item for item in buttons), buttons


@pytest.mark.asyncio
async def test_welcome_is_sent_once(dispatcher, monkeypatch):
    """A single join must produce exactly one welcome message."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    join = group_message("", user_id=MEMBER_ID,
                         new_chat_members=[User(id=MEMBER_ID, is_bot=False,
                                                first_name="تازه‌وارد")])
    await feed(dispatcher, bot, join)
    assert not reported, reported
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    welcomes = [text for text in texts if "خوش" in text]
    assert len(welcomes) == 1, f"expected exactly one welcome, got {welcomes}"


@pytest.mark.asyncio
async def test_telegram_admin_can_moderate_without_internal_role(dispatcher, monkeypatch):
    """A real Telegram admin must be able to moderate, role assignment or not."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    target = group_message("سلام", user_id=MEMBER_ID)
    await feed(dispatcher, bot, group_message("بن ۲روز تبلیغ", user_id=ADMIN_ID,
                                              reply_to=target))
    assert not reported, reported
    assert bot.ban_chat_member.await_count >= 1


@pytest.mark.asyncio
async def test_anonymous_admin_keeps_full_rights(dispatcher, monkeypatch):
    """Anonymous group admins (Telegram hides their id) stay in charge."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    target = group_message("سلام", user_id=MEMBER_ID)
    anonymous = group_message("بن تبلیغ", user_id=1087968824, reply_to=target)
    await feed(dispatcher, bot, anonymous)
    assert not reported, reported
    assert bot.ban_chat_member.await_count >= 1


@pytest.mark.asyncio
async def test_admin_fallback_to_administrators_list(dispatcher, monkeypatch):
    """If getChatMember fails, the administrators list still proves the rank."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    bot.get_chat_administrators = AsyncMock(return_value=[
        ChatMemberAdministrator(
            user=User(id=ADMIN_ID, is_bot=False, first_name="مدیر"),
            status="administrator", can_be_edited=False, can_manage_chat=True,
            can_delete_messages=True, can_restrict_members=True, can_promote_members=False,
            can_change_info=True, can_invite_users=True, can_pin_messages=True,
            can_manage_video_chats=True, can_post_messages=True, can_edit_messages=True,
            is_anonymous=False, can_post_stories=True, can_edit_stories=True,
            can_delete_stories=True, can_send_welcome_messages=True)])

    original = bot.get_chat_member

    async def flaky(chat_id: int, user_id: int):
        if user_id == ADMIN_ID:  # simulate a failing getChatMember
            raise RuntimeError("member not found")
        return await original(chat_id=chat_id, user_id=user_id)

    bot.get_chat_member = AsyncMock(side_effect=flaky)

    target = group_message("سلام", user_id=MEMBER_ID)
    await feed(dispatcher, bot, group_message("بن تبلیغ", user_id=ADMIN_ID, reply_to=target))
    assert not reported, reported
    assert bot.ban_chat_member.await_count >= 1


@pytest.mark.asyncio
async def test_help_topics_answer(dispatcher, monkeypatch):
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    for phrase, expected in [("راهنما مدیریت", "مدیریت کاربران"),
                             ("راهنما قفل‌ها", "قفل‌ها"),
                             ("راهنما ارز", "ارز")]:
        await feed(dispatcher, bot, group_message(phrase, user_id=MEMBER_ID))
        texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
        assert any(expected in text for text in texts), (phrase, texts)
    assert not reported, reported


@pytest.mark.asyncio
async def test_create_invite_link(dispatcher, monkeypatch):
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("ساخت لینک", user_id=ADMIN_ID))
    assert not reported, reported
    assert bot.create_chat_invite_link.await_count >= 1
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("https://t.me/joinchat/TESTLINK" in text for text in texts)


# --------------------------------------------------------------------------- #
# Member tags and promote/demote (the two reported bugs)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_tag_is_mirrored_into_the_telegram_member_list(dispatcher, monkeypatch):
    """`تگ` must call setChatMemberTag so the tag shows up in Telegram itself."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))
    raw_calls = _raw_api_recorder(monkeypatch)

    bot = build_fake_bot(can_manage_tags=True)
    target = group_message("سلام", user_id=MEMBER_ID)
    await feed(dispatcher, bot, group_message("تگ مدیر فروش", user_id=ADMIN_ID,
                                              reply_to=target))
    assert not reported, reported
    assert ("setChatMemberTag", {"chat_id": CHAT_ID, "user_id": MEMBER_ID,
                                 "tag": "مدیر فروش"}) in [(name, payload)
                                                          for name, payload in raw_calls]
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("در لیست اعضای تلگرام" in text for text in texts), texts


@pytest.mark.asyncio
async def test_tag_without_the_manage_tags_right_names_it(dispatcher, monkeypatch):
    """No `can_manage_tags` -> the bot says exactly which right is missing."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))
    raw_calls = _raw_api_recorder(monkeypatch)

    bot = build_fake_bot(can_manage_tags=False)
    target = group_message("سلام", user_id=MEMBER_ID)
    await feed(dispatcher, bot, group_message("تگ مدیر فروش", user_id=ADMIN_ID,
                                              reply_to=target))
    assert not reported, reported
    assert raw_calls == []
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("مدیریت تگ اعضا" in text for text in texts), texts


@pytest.mark.asyncio
async def test_promote_reports_the_missing_bot_right(dispatcher, monkeypatch):
    """`ارتقا` explains itself instead of silently doing nothing."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))
    raw_calls = _raw_api_recorder(monkeypatch)

    bot = build_fake_bot(can_promote_members=False)
    target = group_message("سلام", user_id=MEMBER_ID)
    await feed(dispatcher, bot, group_message("ارتقا", user_id=ADMIN_ID, reply_to=target))
    assert not reported, reported
    assert raw_calls == []
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("افزودن مدیر جدید" in text for text in texts), texts


@pytest.mark.asyncio
async def test_promote_works_when_the_bot_may_promote(dispatcher, monkeypatch):
    """With `can_promote_members` the bot really promotes the member."""
    import app.handlers.errors as errors_module
    from aiogram.methods import PromoteChatMember

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot(can_promote_members=True)
    target = group_message("سلام", user_id=MEMBER_ID)
    await feed(dispatcher, bot, group_message("ارتقا", user_id=ADMIN_ID, reply_to=target))
    assert not reported, reported
    promoted = [m for m in bot.telegram_calls
                if type(m).__name__ == "PromoteChatMemberWithTitle"]
    assert promoted, [type(m).__name__ for m in bot.telegram_calls]
    assert promoted[0].user_id == MEMBER_ID
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("ارتقا یافت" in text for text in texts), texts


@pytest.mark.asyncio
async def test_demote_sends_every_right_as_false(dispatcher, monkeypatch):
    """`تنزل` must transmit the False values, not drop them from the request."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))
    raw_calls = _raw_api_recorder(monkeypatch)

    bot = build_fake_bot(can_promote_members=True)
    target = group_message("سلام", user_id=MEMBER_ID)
    await feed(dispatcher, bot, group_message("تنزل", user_id=ADMIN_ID, reply_to=target))
    assert not reported, reported
    assert raw_calls and raw_calls[0][0] == "promoteChatMember"
    payload = raw_calls[0][1]
    assert payload["user_id"] == MEMBER_ID
    assert payload["can_delete_messages"] is False
    assert payload["can_restrict_members"] is False
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("برکنار شد" in text for text in texts), texts


@pytest.mark.asyncio
async def test_bot_rights_command_lists_what_is_missing(dispatcher, monkeypatch):
    """`دسترسی ربات` is the diagnostic an admin needs after a failure."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot(can_manage_tags=False, can_promote_members=True)
    await feed(dispatcher, bot, group_message("دسترسی ربات", user_id=ADMIN_ID))
    assert not reported, reported
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("مدیریت تگ اعضا" in text for text in texts), texts


# --------------------------------------------------------------------------- #
# Editing the Telegram rights of an administrator
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_admin_rights_command_renders_the_toggles(dispatcher, monkeypatch):
    """`دسترسی مدیر` shows a live editor for an admin promoted by the bot."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot(can_promote_members=True)
    target = group_message("سلام", user_id=PROMOTED_ADMIN_ID)
    await feed(dispatcher, bot, group_message("دسترسی مدیر", user_id=OWNER_ID,
                                              reply_to=target))
    assert not reported, reported
    keyboard = bot.send_message.await_args.kwargs.get("reply_markup")
    assert keyboard is not None
    labels = [btn.text for row in keyboard.inline_keyboard for btn in row]
    assert any("سنجاق" in label for label in labels), labels
    assert any("عزل مدیر" in label for label in labels), labels


@pytest.mark.asyncio
async def test_admin_rights_command_on_a_plain_member(dispatcher, monkeypatch):
    """A non-admin has no rights to edit - say how to promote instead."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot(can_promote_members=True)
    target = group_message("سلام", user_id=MEMBER_ID)
    await feed(dispatcher, bot, group_message("دسترسی مدیر", user_id=OWNER_ID,
                                              reply_to=target))
    assert not reported, reported
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("مدیر گروه نیست" in text for text in texts), texts


@pytest.mark.asyncio
async def test_admin_rights_toggle_promotes_with_the_new_value(dispatcher, monkeypatch):
    """Clicking a right flips exactly that right through promoteChatMember."""
    raw_calls = _raw_api_recorder(monkeypatch)
    bot = build_fake_bot(can_promote_members=True)
    callback = CallbackQuery(
        id="11", from_user=User(id=OWNER_ID, is_bot=False, first_name="Ali"),
        chat_instance="1",
        data=cb("ap", "t", PROMOTED_ADMIN_ID, "can_pin_messages"),
        message=_msg(text="دسترسی مدیر"),
    )
    await dispatcher.feed_update(bot, Update(update_id=_next_update_id(),
                                             callback_query=callback))
    assert raw_calls and raw_calls[0][0] == "promoteChatMember"
    payload = raw_calls[0][1]
    assert payload["user_id"] == PROMOTED_ADMIN_ID
    assert payload["can_pin_messages"] is True       # was False -> flipped on
    assert payload["can_delete_messages"] is True    # unchanged
    assert payload["can_change_info"] is False       # unchanged
    assert _edit_count(bot) >= 1, "the editor must refresh after a toggle"


@pytest.mark.asyncio
async def test_admin_rights_demote_preset_clears_every_right(dispatcher, monkeypatch):
    """The «عزل مدیر» button revokes every administrator right."""
    raw_calls = _raw_api_recorder(monkeypatch)
    bot = build_fake_bot(can_promote_members=True)
    callback = CallbackQuery(
        id="12", from_user=User(id=OWNER_ID, is_bot=False, first_name="Ali"),
        chat_instance="1", data=cb("ap", "demote", PROMOTED_ADMIN_ID),
        message=_msg(text="دسترسی مدیر"),
    )
    await dispatcher.feed_update(bot, Update(update_id=_next_update_id(),
                                             callback_query=callback))
    assert raw_calls and raw_calls[0][0] == "promoteChatMember"
    payload = raw_calls[0][1]
    assert payload["user_id"] == PROMOTED_ADMIN_ID
    for flag in ("can_delete_messages", "can_restrict_members", "can_invite_users",
                 "can_pin_messages", "can_change_info", "can_promote_members"):
        assert payload[flag] is False, flag


@pytest.mark.asyncio
async def test_demote_alias_works_with_azl(dispatcher, monkeypatch):
    """`عزل` is accepted as a plain-Persian alias of `تنزل`."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))
    raw_calls = _raw_api_recorder(monkeypatch)

    bot = build_fake_bot(can_promote_members=True)
    target = group_message("سلام", user_id=PROMOTED_ADMIN_ID)
    await feed(dispatcher, bot, group_message("عزل", user_id=OWNER_ID, reply_to=target))
    assert not reported, reported
    assert raw_calls and raw_calls[0][0] == "promoteChatMember"
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("برکنار شد" in text for text in texts), texts


@pytest.mark.asyncio
async def test_azl_in_a_sentence_is_not_a_command(dispatcher, monkeypatch):
    """«عزل شد» is ordinary talk - the bot must not demote anybody for it."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))
    raw_calls = _raw_api_recorder(monkeypatch)

    bot = build_fake_bot(can_promote_members=True)
    target = group_message("سلام", user_id=PROMOTED_ADMIN_ID)
    await feed(dispatcher, bot, group_message("عزل شد", user_id=OWNER_ID, reply_to=target))
    assert not reported, reported
    assert raw_calls == []


# --------------------------------------------------------------------------- #
# Undo buttons and group-wide lock
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_mute_reply_carries_an_undo_button(dispatcher, monkeypatch):
    """After «سکوت» the reply itself offers «لغو سکوت»."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    target = group_message("سلام", user_id=MEMBER_ID)
    await feed(dispatcher, bot, group_message("سکوت", user_id=OWNER_ID, reply_to=target))
    assert not reported, reported
    keyboard = bot.send_message.await_args.kwargs.get("reply_markup")
    assert keyboard is not None, "the mute reply must carry the undo button"
    callbacks = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
    assert f"undo:unmute:{MEMBER_ID}" in callbacks, callbacks


@pytest.mark.asyncio
async def test_undo_button_unmutes_the_member(dispatcher, monkeypatch):
    """Clicking «لغو سکوت» really lifts the restriction."""
    bot = build_fake_bot()
    callback = CallbackQuery(
        id="21", from_user=User(id=OWNER_ID, is_bot=False, first_name="Ali"),
        chat_instance="1", data=cb("undo", "unmute", MEMBER_ID),
        message=_msg(text="سکوت"),
    )
    await dispatcher.feed_update(bot, Update(update_id=_next_update_id(),
                                             callback_query=callback))
    assert bot.restrict_chat_member.await_count >= 1
    permissions = bot.restrict_chat_member.await_args.kwargs.get("permissions")
    assert permissions is not None and permissions.can_send_messages is True


@pytest.mark.asyncio
async def test_lock_group_command_sets_chat_permissions(dispatcher, monkeypatch):
    """«قفل گروه» locks the whole chat for ordinary members."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("قفل گروه", user_id=OWNER_ID))
    assert not reported, reported
    assert bot.set_chat_permissions.await_count == 1
    permissions = bot.set_chat_permissions.await_args.kwargs.get("permissions")
    assert permissions.can_send_messages is False
    assert permissions.can_send_photos is False
    assert permissions.can_send_videos is False


@pytest.mark.asyncio
async def test_unlock_group_command_restores_permissions(dispatcher, monkeypatch):
    """«باز کردن گروه» gives the members their voice back."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("باز کردن گروه", user_id=OWNER_ID))
    assert not reported, reported
    assert bot.set_chat_permissions.await_count == 1
    permissions = bot.set_chat_permissions.await_args.kwargs.get("permissions")
    assert permissions.can_send_messages is True
    assert permissions.can_send_photos is True


@pytest.mark.asyncio
async def test_group_lock_button_in_the_panel(dispatcher, monkeypatch):
    """The locks panel can lock and unlock the group with one tap."""
    bot = build_fake_bot()
    callback = CallbackQuery(
        id="22", from_user=User(id=OWNER_ID, is_bot=False, first_name="Ali"),
        chat_instance="1", data=cb("cl", "media"),
        message=_msg(text="قفل‌ها"),
    )
    await dispatcher.feed_update(bot, Update(update_id=_next_update_id(),
                                             callback_query=callback))
    assert bot.set_chat_permissions.await_count == 1
    permissions = bot.set_chat_permissions.await_args.kwargs.get("permissions")
    assert permissions.can_send_messages is True    # text stays allowed
    assert permissions.can_send_photos is False     # media is blocked


# --------------------------------------------------------------------------- #
# Commands that take @username / numeric id instead of a reply
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_unmute_by_username_without_reply(dispatcher, monkeypatch):
    """«لغو سکوت @Parhannni» must work exactly like a reply."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("لغو سکوت @Parhannni", user_id=OWNER_ID))
    assert not reported, reported
    assert bot.restrict_chat_member.await_count >= 1
    assert bot.restrict_chat_member.await_args.kwargs.get("user_id") == MEMBER_ID


@pytest.mark.asyncio
async def test_unmute_by_numeric_id(dispatcher, monkeypatch):
    """A numeric id works as the target as well."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message(f"لغو سکوت {OUTSIDER_ID}", user_id=OWNER_ID))
    assert not reported, reported
    assert bot.restrict_chat_member.await_count >= 1
    assert bot.restrict_chat_member.await_args.kwargs.get("user_id") == OUTSIDER_ID


@pytest.mark.asyncio
async def test_a_sentence_is_still_not_a_command(dispatcher, monkeypatch):
    """«لغو سکوت الان انجام بده» is ordinary talk, not a moderation command."""
    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("لغو سکوت الان انجام بده لطفا",
                                              user_id=OWNER_ID))
    assert bot.restrict_chat_member.await_count == 0


# --------------------------------------------------------------------------- #
# «قفل خروج» - automatic ban on leave
# --------------------------------------------------------------------------- #
def _leave_message(user_id: int) -> Message:
    return Message(
        message_id=_next_update_id(),
        date=int(time.time()),
        chat=Chat(id=CHAT_ID, type="supergroup", title="گروه تست"),
        from_user=User(id=user_id, is_bot=False, first_name="Leaver"),
        left_chat_member=User(id=user_id, is_bot=False, first_name="Leaver"),
    )


@pytest.mark.asyncio
async def test_ban_on_leave_bans_and_offers_undo(dispatcher, monkeypatch):
    """After «قفل خروج» a member who leaves is banned, with an undo button."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("قفل خروج روشن", user_id=OWNER_ID))
    await feed(dispatcher, bot, _leave_message(MEMBER_ID))
    assert not reported, reported
    assert bot.ban_chat_member.await_count >= 1
    assert bot.ban_chat_member.await_args.kwargs.get("user_id") == MEMBER_ID
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("قفل خروج" in text for text in texts), texts
    keyboard = bot.send_message.await_args.kwargs.get("reply_markup")
    callbacks = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
    assert f"undo:unban:{MEMBER_ID}" in callbacks, callbacks
    assert any(data.startswith("lg:off:") for data in callbacks), callbacks


@pytest.mark.asyncio
async def test_leave_is_ignored_when_the_rule_is_off(dispatcher, monkeypatch):
    """Default configuration: nobody is banned for leaving."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, _leave_message(MEMBER_ID))
    assert not reported, reported
    assert bot.ban_chat_member.await_count == 0


@pytest.mark.asyncio
async def test_quick_leave_rule_bans_a_join_and_run(dispatcher, monkeypatch):
    """«بن خروج سریع ۱۰» catches someone who joins and leaves at once."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("بن خروج سریع ۱۰", user_id=OWNER_ID))
    joiner = User(id=MEMBER_ID, is_bot=False, first_name="Leaver")
    await feed(dispatcher, bot, Message(
        message_id=_next_update_id(), date=int(time.time()),
        chat=Chat(id=CHAT_ID, type="supergroup", title="گروه تست"),
        from_user=joiner, new_chat_members=[joiner]))
    await feed(dispatcher, bot, _leave_message(MEMBER_ID))
    assert not reported, reported
    assert bot.ban_chat_member.await_count >= 1


@pytest.mark.asyncio
async def test_leave_guard_can_be_switched_off_from_the_button(dispatcher, monkeypatch):
    """The «خاموش کردن این قانون» button really disables the rule."""
    bot = build_fake_bot()
    callback = CallbackQuery(
        id="31", from_user=User(id=OWNER_ID, is_bot=False, first_name="Ali"),
        chat_instance="1", data=cb("lg", "off", "ban_on_leave"),
        message=_msg(text="قفل خروج"),
    )
    await dispatcher.feed_update(bot, Update(update_id=_next_update_id(),
                                             callback_query=callback))
    assert _edit_count(bot) >= 1
    await feed(dispatcher, bot, _leave_message(MEMBER_ID))
    assert bot.ban_chat_member.await_count == 0


# --------------------------------------------------------------------------- #
# An open panel belongs to the admin who opened it
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_other_admins_cannot_click_an_open_panel(dispatcher, monkeypatch):
    """A panel is exclusive: another admin tapping it gets a refusal."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("پنل", user_id=ADMIN_ID))
    callback = CallbackQuery(
        id="41", from_user=User(id=OWNER_ID, is_bot=False, first_name="Owner"),
        chat_instance="1", data=cb("nav", "home"),
        message=_msg(text="پنل"),
    )
    await dispatcher.feed_update(bot, Update(update_id=_next_update_id(),
                                             callback_query=callback))
    assert not reported, reported
    assert bot.answer_callback_query.await_count >= 1
    alert = bot.answer_callback_query.await_args.kwargs
    assert alert.get("show_alert") is True
    assert "پنل" in (alert.get("text") or "")
    assert _edit_count(bot) == 0, "a foreign click must not change the panel"


@pytest.mark.asyncio
async def test_the_owner_may_still_use_their_own_panel(dispatcher, monkeypatch):
    """The admin who opened the panel can navigate it normally."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("پنل", user_id=OWNER_ID))
    callback = CallbackQuery(
        id="42", from_user=User(id=OWNER_ID, is_bot=False, first_name="Owner"),
        chat_instance="1", data=cb("panel", "locks"),
        message=_msg(text="پنل"),
    )
    await dispatcher.feed_update(bot, Update(update_id=_next_update_id(),
                                             callback_query=callback))
    assert not reported, reported
    assert _edit_count(bot) >= 1, "the owner's own clicks must work"


# --------------------------------------------------------------------------- #
# Language locks, long-message lock and anonymous (channel) senders
# --------------------------------------------------------------------------- #
async def _enable_lock(dispatcher, bot, phrase: str) -> None:
    await feed(dispatcher, bot, group_message(phrase, user_id=OWNER_ID))


@pytest.mark.asyncio
async def test_chinese_lock_mutes_the_member(dispatcher, monkeypatch):
    """With «قفل چینی» a Chinese message mutes its author."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await _enable_lock(dispatcher, bot, "قفل چینی")
    await feed(dispatcher, bot, group_message("你好朋友 这是中文", user_id=MEMBER_ID))
    assert not reported, reported
    assert bot.restrict_chat_member.await_count >= 1
    assert bot.delete_message.await_count >= 1


@pytest.mark.asyncio
async def test_russian_lock_mutes_cyrillic_text(dispatcher, monkeypatch):
    """«قفل روسی» catches Cyrillic writing."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await _enable_lock(dispatcher, bot, "قفل روسی")
    await feed(dispatcher, bot, group_message("Привет как дела", user_id=MEMBER_ID))
    assert not reported, reported
    assert bot.restrict_chat_member.await_count >= 1


@pytest.mark.asyncio
async def test_hindi_lock_mutes_devanagari_text(dispatcher, monkeypatch):
    """«قفل هندی» catches Devanagari writing."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await _enable_lock(dispatcher, bot, "قفل هندی")
    await feed(dispatcher, bot, group_message("नमस्ते दोस्तों", user_id=MEMBER_ID))
    assert not reported, reported
    assert bot.restrict_chat_member.await_count >= 1


@pytest.mark.asyncio
async def test_persian_text_is_never_touched_by_the_language_locks(dispatcher, monkeypatch):
    """Ordinary Persian talk must stay untouched even with the locks on."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    for phrase in ("قفل چینی", "قفل روسی", "قفل هندی"):
        await _enable_lock(dispatcher, bot, phrase)
    await feed(dispatcher, bot, group_message("سلام چه خبر", user_id=MEMBER_ID))
    assert not reported, reported
    assert bot.restrict_chat_member.await_count == 0
    assert bot.delete_message.await_count == 0


@pytest.mark.asyncio
async def test_long_message_lock_mutes_over_the_limit(dispatcher, monkeypatch):
    """A message longer than the limit (1000 by default) mutes the member."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await _enable_lock(dispatcher, bot, "قفل طولانی")
    await feed(dispatcher, bot, group_message("ا" * 1001, user_id=MEMBER_ID))
    assert not reported, reported
    assert bot.restrict_chat_member.await_count >= 1

    short_bot = build_fake_bot()
    await _enable_lock(dispatcher, short_bot, "قفل طولانی")
    await feed(dispatcher, short_bot, group_message("ا" * 1000, user_id=MEMBER_ID))
    assert short_bot.restrict_chat_member.await_count == 0


@pytest.mark.asyncio
async def test_anonymous_channel_post_is_blocked(dispatcher, monkeypatch):
    """A message posted as a channel gets the channel banned, not ignored."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await _enable_lock(dispatcher, bot, "قفل کانال")
    channel = Chat(id=-1007777777, type="channel", title="My Channel")
    message = Message(
        message_id=_next_update_id(), date=int(time.time()),
        chat=Chat(id=CHAT_ID, type="supergroup", title="گروه تست"),
        from_user=User(id=1087968824, is_bot=True, first_name="Group"),
        sender_chat=channel, text="تبلیغ از طرف کانال")
    await feed(dispatcher, bot, message)
    assert not reported, reported
    assert bot.ban_chat_sender_chat.await_count >= 1
    assert bot.ban_chat_sender_chat.await_args.kwargs.get("sender_chat_id") == -1007777777
    assert bot.delete_message.await_count >= 1


@pytest.mark.asyncio
async def test_hidden_admin_posts_are_left_alone(dispatcher, monkeypatch):
    """A hidden group admin is a real admin: the lock must not touch them."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await _enable_lock(dispatcher, bot, "قفل ناشناس")
    await feed(dispatcher, bot, group_message("پیام پنهان", user_id=1087968824))
    assert not reported, reported
    assert bot.delete_message.await_count == 0
    assert bot.restrict_chat_member.await_count == 0


@pytest.mark.asyncio
async def test_locks_are_off_by_default(dispatcher, monkeypatch):
    """Nothing is muted until an admin turns a lock on."""
    import app.handlers.errors as errors_module

    reported = []
    monkeypatch.setattr(errors_module, "notify_error_chat",
                        AsyncMock(side_effect=lambda *a, **k: reported.append(a)))

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("你好朋友", user_id=MEMBER_ID))
    await feed(dispatcher, bot, group_message("الف" * 1500, user_id=MEMBER_ID))
    assert not reported, reported
    assert bot.restrict_chat_member.await_count == 0
    assert bot.delete_message.await_count == 0


@pytest.mark.asyncio
async def test_long_lock_threshold_is_adjustable(dispatcher, session):
    """The character limit is not hardcoded: it can be raised from the panel."""
    bot = build_fake_bot()
    await _enable_lock(dispatcher, bot, "قفل طولانی")
    callback = CallbackQuery(
        id="55", from_user=User(id=OWNER_ID, is_bot=False, first_name="Ali"),
        chat_instance="1", data=cb("locknum", "long", "max_length", 500, "text"),
        message=_msg(text="قفل پیام طولانی"),
    )
    await dispatcher.feed_update(bot, Update(update_id=_next_update_id(),
                                             callback_query=callback))

    from app.services import locks as lock_service

    locks = await lock_service.get_locks_map(session, CHAT_ID)
    assert int(locks["long"].extra.get("max_length", 1000)) == 1500

    # 1200 characters is under the new limit, 1600 is above it.
    ok_bot = build_fake_bot()
    await _enable_lock(dispatcher, ok_bot, "قفل طولانی")
    await feed(dispatcher, ok_bot, group_message("ا" * 1200, user_id=MEMBER_ID))
    assert ok_bot.restrict_chat_member.await_count == 0


# --------------------------------------------------------------------------- #
# «تگ همه» - mentioning every member of the group
# --------------------------------------------------------------------------- #
def _mention_calls(bot):
    """Every send_message call that carries real mention entities."""
    return [call for call in bot.send_message.await_args_list
            if call.kwargs.get("entities")]


def _mention_ids(bot) -> list[int]:
    """Every user id the bot mentioned as a ``text_mention`` entity."""
    ids: list[int] = []
    for call in _mention_calls(bot):
        for entity in call.kwargs.get("entities") or []:
            user = getattr(entity, "user", None)
            if user is not None:
                ids.append(int(user.id))
    return ids


@pytest.mark.asyncio
async def test_mention_all_mentions_every_known_member(dispatcher, monkeypatch):
    """«تگ همه» mentions the members the bot has seen in this group."""
    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("سلام", user_id=MEMBER_ID))
    await feed(dispatcher, bot, group_message("من هم هستم", user_id=PROMOTED_ADMIN_ID))
    await feed(dispatcher, bot, group_message("تگ همه", user_id=OWNER_ID))

    ids = _mention_ids(bot)
    for uid in (MEMBER_ID, PROMOTED_ADMIN_ID, OWNER_ID):
        assert uid in ids, (uid, ids)
    # Telegram only notifies the first ~5 mentions of a message.
    for call in _mention_calls(bot):
        assert 1 <= len(call.kwargs["entities"]) <= 5
        assert len(call.kwargs["text"]) <= 4096


@pytest.mark.asyncio
async def test_mention_all_includes_the_caller_in_a_fresh_group(dispatcher):
    """A group where nobody has written yet still mentions the caller."""
    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("تگ همه", user_id=OWNER_ID))
    assert OWNER_ID in _mention_ids(bot)


@pytest.mark.asyncio
async def test_mention_all_is_admin_only(dispatcher):
    """Ordinary members must not be able to ping everybody."""
    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("سلام", user_id=MEMBER_ID))
    await feed(dispatcher, bot, group_message("تگ همه", user_id=MEMBER_ID))
    assert _mention_ids(bot) == []
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("فقط برای مدیران" in text for text in texts), texts


@pytest.mark.asyncio
async def test_mention_all_can_be_repeated_without_a_cooldown(dispatcher):
    """There is no throttle: admins may call it again immediately."""
    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("سلام", user_id=MEMBER_ID))
    await feed(dispatcher, bot, group_message("تگ همه", user_id=OWNER_ID))
    await feed(dispatcher, bot, group_message("تگ همه", user_id=OWNER_ID))

    assert len(_mention_calls(bot)) == 2
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert not any("⏳" in text for text in texts), texts


def test_mention_all_splits_into_notifying_batches():
    """Telegram notifies only the first ~5 mentions - so batches of five."""
    from app.services import mention_all as mention_service

    members = [(1000 + index, "نام کاربر") for index in range(23)]
    batches = mention_service.build_batches(members)

    assert len(batches) == 5                      # 5 + 5 + 5 + 5 + 3
    assert mention_service.mentioned_ids(batches) == [1000 + i for i in range(23)]
    for text, entities in batches:
        assert len(text) <= 4096
        assert 1 <= len(entities) <= 5
        # every entity must point at the name it is supposed to highlight
        # (Telegram counts offsets in UTF-16 code units, Python in code points)
        units = text.encode("utf-16-le")
        for entity in entities:
            start, end = entity.offset * 2, (entity.offset + entity.length) * 2
            assert units[start:end].decode("utf-16-le") == "نام کاربر"
            assert entity.type == "text_mention"
            assert entity.user is not None

    # the header counts the batches for the admins
    assert "۱/۵" in batches[0][0]
    assert "۵/۵" in batches[-1][0]


# --------------------------------------------------------------------------- #
# Roster: who the bot believes is in the group
# --------------------------------------------------------------------------- #
def _reaction(chat_id: int, user: User, *, message_id: int = 555) -> MessageReactionUpdated:
    return MessageReactionUpdated(
        chat=Chat(id=chat_id, type="supergroup", title="گروه تست"),
        message_id=message_id, date=int(time.time()),
        old_reaction=[], new_reaction=[ReactionTypeEmoji(type="emoji", emoji="👍")],
        user=user)


@pytest.mark.asyncio
async def test_mention_all_includes_members_who_only_react(dispatcher):
    """A member who never writes a word is still mentioned (reactions count)."""
    bot = build_fake_bot()
    await dispatcher.feed_update(bot, Update(
        update_id=_next_update_id(),
        message_reaction=_reaction(CHAT_ID, User(id=MEMBER_ID, is_bot=False,
                                                 first_name="سارا"))))
    await feed(dispatcher, bot, group_message("تگ همه", user_id=OWNER_ID))
    assert MEMBER_ID in _mention_ids(bot)


@pytest.mark.asyncio
async def test_mention_all_includes_members_seen_in_a_member_update(dispatcher):
    """Somebody muted/promoted by an admin ends up on the roster as well."""
    bot = build_fake_bot()
    await dispatcher.feed_update(bot, Update(
        update_id=_next_update_id(), chat_member=ChatMemberUpdated(
            chat=Chat(id=CHAT_ID, type="supergroup", title="گروه تست"),
            from_user=User(id=OWNER_ID, is_bot=False, first_name="Owner"),
            date=int(time.time()),
            old_chat_member=ChatMemberMember(
                user=User(id=MEMBER_ID, is_bot=False, first_name="رضا"), status="member"),
            new_chat_member=ChatMemberMember(
                user=User(id=MEMBER_ID, is_bot=False, first_name="رضا"), status="member"))))
    await feed(dispatcher, bot, group_message("تگ همه", user_id=OWNER_ID))
    assert MEMBER_ID in _mention_ids(bot)


@pytest.mark.asyncio
async def test_mention_all_includes_the_telegram_admin_list(dispatcher):
    """Administrators are always known - Telegram gives us that list."""
    bot = build_fake_bot()
    stranger = 555000111
    bot.get_chat_administrators = AsyncMock(return_value=[
        ChatMemberAdministrator(
            user=User(id=stranger, is_bot=False, first_name="مدیر ناشناخته"),
            status="administrator", is_anonymous=False, can_be_edited=False,
            can_manage_chat=True, can_delete_messages=True, can_restrict_members=True,
            can_promote_members=False, can_change_info=True, can_invite_users=True,
            can_pin_messages=True, can_post_stories=True, can_edit_stories=True,
            can_delete_stories=True, can_manage_video_chats=True,
            can_send_welcome_messages=True)])
    await feed(dispatcher, bot, group_message("تگ همه", user_id=OWNER_ID))
    assert stranger in _mention_ids(bot)


@pytest.mark.asyncio
async def test_mention_all_skips_members_who_left(dispatcher):
    """Somebody who has left the group is never mentioned."""
    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("سلام", user_id=MEMBER_ID))
    await dispatcher.feed_update(bot, Update(
        update_id=_next_update_id(), chat_member=ChatMemberUpdated(
            chat=Chat(id=CHAT_ID, type="supergroup", title="گروه تست"),
            from_user=User(id=MEMBER_ID, is_bot=False, first_name="Ali"),
            date=int(time.time()),
            old_chat_member=ChatMemberMember(
                user=User(id=MEMBER_ID, is_bot=False, first_name="Ali"), status="member"),
            new_chat_member=ChatMemberLeft(
                user=User(id=MEMBER_ID, is_bot=False, first_name="Ali"), status="left"))))
    await feed(dispatcher, bot, group_message("تگ همه", user_id=OWNER_ID))
    assert MEMBER_ID not in _mention_ids(bot)


@pytest.mark.asyncio
async def test_group_creator_who_is_not_the_bot_owner_has_full_access(dispatcher):
    """The real owner of a group manages it, even without being the bot owner."""
    from app.config import settings as app_settings

    creator_id = 987654321
    assert creator_id != int(app_settings.owner_id or 0)

    bot = build_fake_bot()

    async def get_chat_member(chat_id: int, user_id: int):
        user = User(id=user_id, is_bot=False, first_name="مالک گروه")
        if user_id == creator_id:
            return ChatMemberOwner(user=user, is_anonymous=False, status="creator")
        return ChatMemberMember(user=user, status="member")

    bot.get_chat_member = AsyncMock(side_effect=get_chat_member)

    await feed(dispatcher, bot, group_message("قفل لینک", user_id=creator_id))
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("فعال شد" in text for text in texts), texts

    await feed(dispatcher, bot, group_message("تگ همه", user_id=creator_id))
    assert creator_id in _mention_ids(bot)


# --------------------------------------------------------------------------- #
# Global ban (GBan) - owner level, applied in every guarded chat
# --------------------------------------------------------------------------- #
async def _gban(dispatcher, bot, user_id: int) -> None:
    """Run «بن سراسری» as the bot owner, with a reply on the target."""
    target = group_message("پیام خاطی", user_id=user_id)
    await feed(dispatcher, bot, group_message("بن سراسری", user_id=OWNER_ID,
                                              reply_to=target))
    await asyncio.sleep(0.05)   # let the background rollout finish


@pytest.mark.asyncio
async def test_global_ban_bans_the_target_without_crashing(dispatcher, monkeypatch):
    """The owner command must resolve the reason and ban in the current chat."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "global_ban_enabled", True)

    bot = build_fake_bot()
    await _gban(dispatcher, bot, MEMBER_ID)
    assert bot.ban_chat_member.await_count >= 1
    assert bot.ban_chat_member.await_args.kwargs.get("user_id") == MEMBER_ID
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert not any("Error" in text for text in texts), texts


@pytest.mark.asyncio
async def test_global_ban_accepts_a_numeric_id(dispatcher, monkeypatch):
    """«بن سراسری 123456789 دلیل» works without a reply."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "global_ban_enabled", True)

    bot = build_fake_bot()
    await feed(dispatcher, bot,
               group_message(f"بن سراسری {OUTSIDER_ID} تبلیغ", user_id=OWNER_ID))
    await asyncio.sleep(0.05)
    assert bot.ban_chat_member.await_count >= 1
    assert bot.ban_chat_member.await_args.kwargs.get("user_id") == OUTSIDER_ID
    texts = [call.kwargs.get("text", "") for call in bot.send_message.await_args_list]
    assert any("تبلیغ" in text for text in texts), texts


@pytest.mark.asyncio
async def test_global_ban_is_enforced_when_the_user_joins(dispatcher, monkeypatch):
    """A banned user is removed as soon as they join a guarded chat."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "global_ban_enabled", True)

    bot = build_fake_bot()
    await _gban(dispatcher, bot, MEMBER_ID)

    join_bot = bot
    join_bot.ban_chat_member.reset_mock()
    await dispatcher.feed_update(join_bot, Update(
        update_id=_next_update_id(), chat_member=ChatMemberUpdated(
            chat=Chat(id=CHAT_ID, type="supergroup", title="گروه تست"),
            from_user=User(id=OWNER_ID, is_bot=False, first_name="Owner"),
            date=int(time.time()),
            old_chat_member=ChatMemberLeft(
                user=User(id=MEMBER_ID, is_bot=False, first_name="Ali"), status="left"),
            new_chat_member=ChatMemberMember(
                user=User(id=MEMBER_ID, is_bot=False, first_name="Ali"), status="member"))))
    assert join_bot.ban_chat_member.await_count >= 1
    assert join_bot.ban_chat_member.await_args.kwargs.get("user_id") == MEMBER_ID


@pytest.mark.asyncio
async def test_global_ban_is_enforced_in_a_channel(dispatcher, monkeypatch):
    """Channels are covered too (Telegram sends member updates to admins)."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "global_ban_enabled", True)

    bot = build_fake_bot()
    await _gban(dispatcher, bot, MEMBER_ID)
    bot.ban_chat_member.reset_mock()

    channel_id = -1005555555
    await dispatcher.feed_update(bot, Update(
        update_id=_next_update_id(), chat_member=ChatMemberUpdated(
            chat=Chat(id=channel_id, type="channel", title="کانال تست"),
            from_user=User(id=OWNER_ID, is_bot=False, first_name="Owner"),
            date=int(time.time()),
            old_chat_member=ChatMemberLeft(
                user=User(id=MEMBER_ID, is_bot=False, first_name="Ali"), status="left"),
            new_chat_member=ChatMemberMember(
                user=User(id=MEMBER_ID, is_bot=False, first_name="Ali"), status="member"))))
    assert bot.ban_chat_member.await_count >= 1
    assert bot.ban_chat_member.await_args.kwargs.get("chat_id") == channel_id


@pytest.mark.asyncio
async def test_global_ban_is_enforced_on_every_message(dispatcher, monkeypatch):
    """Someone who was banned while already inside the group is removed too."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "global_ban_enabled", True)

    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("سلام", user_id=MEMBER_ID))
    await _gban(dispatcher, bot, MEMBER_ID)
    bot.ban_chat_member.reset_mock()
    bot.delete_message.reset_mock()

    await feed(dispatcher, bot, group_message("هنوز اینجام", user_id=MEMBER_ID))
    assert bot.ban_chat_member.await_count >= 1
    assert bot.delete_message.await_count >= 1


@pytest.mark.asyncio
async def test_global_unban_frees_the_user(dispatcher, monkeypatch):
    """«رفع بن سراسری» clears the flag so the member may talk again."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "global_ban_enabled", True)

    bot = build_fake_bot()
    await _gban(dispatcher, bot, MEMBER_ID)
    target = group_message("پیام خاطی", user_id=MEMBER_ID)
    await feed(dispatcher, bot, group_message("رفع بن سراسری", user_id=OWNER_ID,
                                              reply_to=target))
    await asyncio.sleep(0.05)

    bot.ban_chat_member.reset_mock()
    await feed(dispatcher, bot, group_message("برگشتم", user_id=MEMBER_ID))
    assert bot.ban_chat_member.await_count == 0


@pytest.mark.asyncio
async def test_global_ban_is_inert_when_disabled(dispatcher, monkeypatch):
    """With GLOBAL_BAN_ENABLED unset nothing is banned."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "global_ban_enabled", False)

    bot = build_fake_bot()
    target = group_message("پیام خاطی", user_id=MEMBER_ID)
    await feed(dispatcher, bot, group_message("بن سراسری", user_id=OWNER_ID, reply_to=target))
    await feed(dispatcher, bot, group_message("سلام", user_id=MEMBER_ID))
    assert bot.ban_chat_member.await_count == 0

@pytest.mark.asyncio
async def test_mention_all_sends_everyone_in_small_notifying_batches(dispatcher, monkeypatch):
    """A big roster is split into several messages so every member is notified.

    Telegram only pushes a notification for the first ~5 mentions of a
    message, so one long message would silently skip most of the group.
    """
    from app.services import mention_all as mention_service

    monkeypatch.setattr(mention_service, "SEND_DELAY", 0.0)

    bot = build_fake_bot()
    crowd = [4100 + index for index in range(12)]
    for user_id in crowd:
        await feed(dispatcher, bot, group_message(f"سلام از {user_id}", user_id=user_id))

    await feed(dispatcher, bot, group_message("تگ همه", user_id=OWNER_ID))

    calls = _mention_calls(bot)
    assert len(calls) == 3, [len(call.kwargs["entities"]) for call in calls]
    assert sorted(_mention_ids(bot)) == sorted(crowd + [OWNER_ID])
    for call in calls:
        assert len(call.kwargs["entities"]) <= mention_service.MENTIONS_PER_MESSAGE

@pytest.mark.asyncio
async def test_mention_messages_are_sent_without_a_parse_mode(dispatcher):
    """Regression: the bot's default ``parse_mode=HTML`` would kill the mentions.

    When ``parse_mode`` and ``entities`` are sent together, Telegram parses the
    text again and drops the ``text_mention`` entities - so nobody would be
    notified at all.  The mention messages must carry entities *only*.
    """
    bot = build_fake_bot()
    await feed(dispatcher, bot, group_message("سلام", user_id=MEMBER_ID))
    await feed(dispatcher, bot, group_message("تگ همه", user_id=OWNER_ID))

    calls = _mention_calls(bot)
    assert calls, "no mention message was sent"
    for call in calls:
        assert call.kwargs.get("parse_mode") is None, call.kwargs
        assert call.kwargs.get("entities")
