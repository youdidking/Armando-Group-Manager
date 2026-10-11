"""Service level tests: filters, notes, backup, permissions, market resolvers."""

from __future__ import annotations

import json

import pytest

from app.services import backup as backup_service
from app.services import filters as filter_service
from app.services import notes as note_service
from app.services import market as market_service
from app.services.permissions import Actor
from app.services.roles import at_least, can_manage_role, level_of, normalize_role_key

CHAT_ID = -10099988877
# Distinct chat ids keep the in-memory member cache from leaking between tests.
TAG_CHAT_1 = -10011110001
TAG_CHAT_2 = -10011110002
TAG_CHAT_3 = -10011110003
TAG_CHAT_4 = -10011110004
TAG_CHAT_5 = -10011110005
TAG_CHAT_6 = -10011110006


@pytest.mark.asyncio
async def test_blocklist_add_match_remove(session):
    await filter_service.add_rule(session, CHAT_ID, "تبلیغ", is_blocklist=True,
                                  match_mode="word", action="delete")
    rules = await filter_service.get_rules(session, CHAT_ID)
    assert filter_service.find_blocklist_match("این یک پیام تبلیغی است", rules) is not None
    assert filter_service.find_blocklist_match("سلام خوبی", rules) is None
    assert await filter_service.remove_rule(session, CHAT_ID, "تبلیغ") is True


@pytest.mark.asyncio
async def test_custom_filter_response(session):
    await filter_service.add_rule(session, CHAT_ID, "سلام", is_blocklist=False,
                                  match_mode="contains", response_text="علیک سلام")
    rules = await filter_service.get_rules(session, CHAT_ID)
    matched = filter_service.find_custom_match("سلام بچه‌ها", rules)
    assert matched is not None and matched.response_text == "علیک سلام"


@pytest.mark.asyncio
async def test_notes_roundtrip(session):
    await note_service.save_note(session, CHAT_ID, "قوانین", text="۱. احترام")
    note = await note_service.get_note(session, CHAT_ID, "قوانین")
    assert note is not None and note.text == "۱. احترام"
    notes = await note_service.list_notes(session, CHAT_ID)
    assert any(n.name == "قوانین" for n in notes)
    assert await note_service.delete_note(session, CHAT_ID, "قوانین") is True


@pytest.mark.asyncio
async def test_backup_export_import_roundtrip(session):
    await filter_service.add_rule(session, CHAT_ID, "کلاهبرداری", is_blocklist=True)
    await note_service.save_note(session, CHAT_ID, "آدرس", text="تهران")
    payload = await backup_service.export_chat(session, CHAT_ID)
    raw = backup_service.dumps(payload)
    reloaded = json.loads(raw)
    ok, error = backup_service.validate(reloaded)
    assert ok, error
    assert reloaded["filters"]
    counters = await backup_service.import_chat(session, CHAT_ID, reloaded, merge=True)
    assert counters["notes"] >= 0
    assert counters["filters"] >= 0


def test_backup_rejects_foreign_payload():
    ok, _ = backup_service.validate({"format": "something-else"})
    assert ok is False


def test_role_hierarchy():
    assert level_of("founder") > level_of("admin") > level_of("moderator") > level_of("member")
    assert at_least("moderator", "admin") is False
    assert at_least("admin", "moderator") is True
    assert can_manage_role("admin", "moderator") is True
    assert can_manage_role("moderator", "admin") is False


def test_persian_role_name_resolution():
    assert normalize_role_key("مدیر") == "admin"
    assert normalize_role_key("ناظر") == "moderator"
    assert normalize_role_key("کمک‌یار") == "helper"
    assert normalize_role_key("ویژه") == "trusted"


def test_actor_permission_mirroring():
    """Telegram rank is authoritative: a Telegram admin may do everything the
    bot itself may do, while a plain member only gets what its role grants."""
    admin = Actor(user_id=1, chat_id=1, telegram_status="administrator",
                  can_restrict_members=False, can_promote_members=False,
                  bot_role="moderator", rights_known=True)
    assert admin.can_ban() is True
    assert admin.can_promote() is True
    plain = Actor(user_id=2, chat_id=1, telegram_status="member", rights_known=True)
    assert plain.can_ban() is False
    assert plain.can_manage_settings() is False
    moderator = Actor(user_id=5, chat_id=1, telegram_status="member",
                      bot_role="moderator", rights_known=True)
    assert moderator.can_ban() is True
    assert moderator.can_promote() is False


def test_telegram_admin_may_do_everything_the_bot_may_do():
    """Telegram rights come first: an admin needs no internal role."""
    admin = Actor(user_id=3, chat_id=1, telegram_status="administrator",
                  can_restrict_members=True, can_delete_messages=True,
                  can_promote_members=True, can_invite_users=True,
                  can_pin_messages=True, can_change_info=True, rights_known=True)
    assert admin.can_ban() and admin.can_kick() and admin.can_restrict()
    assert admin.can_delete() and admin.can_promote() and admin.can_pin()
    assert admin.can_edit_chat_info() and admin.can_invite_via_link()
    assert admin.can_manage_settings() and admin.can_manage_staff()
    assert admin.can_manage_filters() and admin.can_manage_notes()


def test_promoted_staff_without_telegram_admin_rights():
    """Internal roles only add powers; they mirror the actor's Telegram rights."""
    staff = Actor(user_id=4, chat_id=1, telegram_status="member",
                  bot_role="moderator", can_restrict_members=True, rights_known=True)
    assert staff.can_ban() is True
    assert staff.can_manage_settings() is False  # needs admin level

    member = Actor(user_id=5, chat_id=1, telegram_status="member",
                   can_restrict_members=True, rights_known=True)
    assert member.can_ban() is False


def test_unknown_rights_are_not_blocked():
    """When Telegram cannot tell us the status we let the API decide."""
    actor = Actor(user_id=6, chat_id=1, telegram_status="member",
                  bot_role="moderator", rights_known=False)
    assert actor.can_ban() is True


def test_anonymous_admin_is_treated_as_administrator():
    from app.services.permissions import is_anonymous_sender

    assert is_anonymous_sender(type("U", (), {"id": 1087968824})()) is True
    assert is_anonymous_sender(type("U", (), {"id": 5, "username": "someone"})()) is False


def test_trusted_bypass_is_configurable():
    actor = Actor(user_id=3, chat_id=1, telegram_status="member", is_trusted=True,
                  trust_bypass=["locks"])
    assert actor.bypasses("locks") is True
    assert actor.bypasses("filters") is False


def test_market_symbol_resolution():
    assert market_service.resolve_currency("دلار") == "usd"
    assert market_service.resolve_currency("یورو") == "eur"
    assert market_service.resolve_crypto("بیت‌کوین") == "btc"
    assert market_service.resolve_crypto("btc") == "btc"
    assert market_service.resolve_gold("سکه") == "coin_emami"
    assert market_service.resolve_gold("طلای ۱۸ عیار") == "gram18"
    assert market_service.resolve_currency("چیز نامعلوم") is None


# --------------------------------------------------------------------------- #
# Telegram tag mirroring (setChatMemberTag / setChatAdministratorCustomTitle)
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, text: str) -> None:
        self._text = text

    async def text(self) -> str:
        return self._text


class _FakePost:
    """async context manager around :class:`_FakeResponse`."""

    def __init__(self, calls: list, url: str, data) -> None:
        self._calls = calls
        self._url = url
        self._data = data

    async def __aenter__(self):
        self._calls.append((self._url.rsplit("/", 1)[-1], dict(self._data)))
        return _FakeResponse('{"ok": true, "result": true}')

    async def __aexit__(self, *exc_info):
        return False


class _FakeForm:
    def __init__(self, *args, **kwargs) -> None:
        self.fields: dict = {}

    def add_field(self, key, value):
        self.fields[key] = value


class _FakeSession:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    async def create_session(self):
        return self

    @property
    def api(self):
        return self

    def api_url(self, token, method):
        return f"https://api.telegram.org/bot{token}/{method}"

    def post(self, url, data=None, timeout=None):
        return _FakePost(self._calls, url, data.fields if isinstance(data, _FakeForm) else data)


class _FakeBot:
    """Minimal Bot stand-in recording the calls made by the tag service."""

    def __init__(self, member, bot_member=None):
        self.id = 999
        self.token = "123456:fake-token"
        self._member = member
        self._bot_member = bot_member if bot_member is not None else member
        self.calls: list[tuple[str, dict]] = []
        self.session = _FakeSession(self.calls)

    async def get_chat_member(self, chat_id, user_id):
        return self._bot_member if user_id == self.id else self._member

    async def set_chat_member_tag(self, chat_id, user_id, tag=None):
        self.calls.append(("setChatMemberTag", {"chat_id": chat_id, "user_id": user_id,
                                                "tag": tag}))
        return True

    async def set_chat_administrator_custom_title(self, chat_id, user_id, custom_title):
        self.calls.append(("setChatAdministratorCustomTitle",
                           {"chat_id": chat_id, "user_id": user_id,
                            "custom_title": custom_title}))
        return True


@pytest.fixture(autouse=True)
def _formdata_for_raw_calls(monkeypatch):
    """The raw API helper builds its own FormData - use a readable stand-in."""
    monkeypatch.setattr("app.core.telegram_extra.FormData", _FakeForm)


def _payload(calls: list, name: str) -> dict:
    for method_name, payload in calls:
        if method_name == name:
            return payload
    raise AssertionError(f"call {name} not found in {[c[0] for c in calls]}")


def _admin_member(**overrides):
    from aiogram.types import ChatMemberAdministrator

    data = {"user": None, "can_manage_chat": True, "is_anonymous": False}
    data.update(overrides)
    return ChatMemberAdministrator.model_construct(**{"user": None} | data)


@pytest.mark.asyncio
async def test_tag_of_regular_member_uses_set_chat_member_tag():
    """A regular member's tag goes through the official setChatMemberTag API."""
    from aiogram.types import ChatMemberAdministrator, ChatMemberMember, User
    from app.services import tags as tag_service

    user = User.model_construct(id=42, is_bot=False, first_name="Ali")
    member = ChatMemberMember.model_construct(user=user, until_date=None)
    bot_admin = ChatMemberAdministrator.model_construct(
        user=User.model_construct(id=999, is_bot=True, first_name="Armando"),
        can_manage_chat=True, can_manage_tags=True, is_anonymous=False)
    bot = _FakeBot(member, bot_admin)
    ok, reason = await tag_service.apply_member_tag(bot, TAG_CHAT_1, 42, "مدیر فروش")
    assert ok is True and reason == ""
    assert bot.calls and bot.calls[0][0] == "setChatMemberTag"
    assert bot.calls[0][1]["tag"] == "مدیر فروش"


@pytest.mark.asyncio
async def test_tag_without_manage_tags_reports_the_exact_right():
    """When the bot lacks can_manage_tags the user is told which right is missing."""
    from aiogram.types import ChatMemberAdministrator, ChatMemberMember, User
    from app.services import tags as tag_service

    member = ChatMemberMember.model_construct(
        user=User.model_construct(id=42, is_bot=False, first_name="Ali"), until_date=None)
    bot_admin = ChatMemberAdministrator.model_construct(
        user=User.model_construct(id=999, is_bot=True, first_name="Armando"),
        can_manage_chat=True, can_manage_tags=False, is_anonymous=False)
    bot = _FakeBot(member, bot_admin)
    ok, reason = await tag_service.apply_member_tag(bot, TAG_CHAT_2, 42, "مدیر فروش")
    assert ok is False
    assert "مدیریت تگ اعضا" in reason
    assert bot.calls == []


@pytest.mark.asyncio
async def test_tag_of_admin_promoted_by_bot_uses_custom_title():
    """Admins promoted by the bot get a custom title, not a member tag."""
    from aiogram.types import ChatMemberAdministrator, User
    from app.services import tags as tag_service

    member = ChatMemberAdministrator.model_construct(
        user=User.model_construct(id=42, is_bot=False, first_name="Ali"),
        can_manage_chat=True, can_be_edited=True, is_anonymous=False)
    bot_admin = ChatMemberAdministrator.model_construct(
        user=User.model_construct(id=999, is_bot=True, first_name="Armando"),
        can_manage_chat=True, can_manage_tags=True, can_promote_members=True,
        is_anonymous=False)
    bot = _FakeBot(member, bot_admin)
    ok, _ = await tag_service.apply_member_tag(bot, TAG_CHAT_3, 42, "مدیر فروش")
    assert ok is True
    assert bot.calls[0][0] == "setChatAdministratorCustomTitle"
    assert bot.calls[0][1]["custom_title"] == "مدیر فروش"


@pytest.mark.asyncio
async def test_tag_of_foreign_admin_explains_promote_first():
    """Admins not promoted by the bot cannot get a title - say so explicitly."""
    from aiogram.types import ChatMemberAdministrator, User
    from app.services import tags as tag_service

    member = ChatMemberAdministrator.model_construct(
        user=User.model_construct(id=42, is_bot=False, first_name="Ali"),
        can_manage_chat=True, can_be_edited=False, is_anonymous=False)
    bot_admin = ChatMemberAdministrator.model_construct(
        user=User.model_construct(id=999, is_bot=True, first_name="Armando"),
        can_manage_chat=True, can_manage_tags=True, can_promote_members=True,
        is_anonymous=False)
    bot = _FakeBot(member, bot_admin)
    ok, reason = await tag_service.apply_member_tag(bot, TAG_CHAT_4, 42, "مدیر فروش")
    assert ok is False
    assert "ارتقا" in reason and bot.calls == []


@pytest.mark.asyncio
async def test_bot_has_right_asks_telegram_twice_on_a_cached_no():
    """A cached negative must be re-checked so newly granted rights work at once."""
    from aiogram.types import ChatMemberAdministrator, User
    from app.services import permissions

    user = User.model_construct(id=999, is_bot=True, first_name="Armando")
    member = ChatMemberAdministrator.model_construct(
        user=user, can_manage_chat=True, can_manage_tags=False, is_anonymous=False)
    calls = {"n": 0}

    class _Bot(_FakeBot):
        async def get_chat_member(self, chat_id, user_id):
            calls["n"] += 1
            if calls["n"] == 1:
                return member
            return ChatMemberAdministrator.model_construct(
                user=user, can_manage_chat=True, can_manage_tags=True, is_anonymous=False)

    assert await permissions.bot_has_right(_Bot(member), TAG_CHAT_5,
                                           "can_manage_tags") is True
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_clearing_a_tag_sends_an_empty_tag():
    """Removing a tag must transmit an empty tag, not simply drop the field."""
    from aiogram.types import ChatMemberAdministrator, ChatMemberMember, User
    from app.services import tags as tag_service

    member = ChatMemberMember.model_construct(
        user=User.model_construct(id=42, is_bot=False, first_name="Ali"), until_date=None)
    bot_admin = ChatMemberAdministrator.model_construct(
        user=User.model_construct(id=999, is_bot=True, first_name="Armando"),
        can_manage_chat=True, can_manage_tags=True, is_anonymous=False)
    bot = _FakeBot(member, bot_admin)
    ok, _ = await tag_service.apply_member_tag(bot, TAG_CHAT_6, 42, None)
    assert ok is True
    assert _payload(bot.calls, "setChatMemberTag")["tag"] == ""


@pytest.mark.asyncio
async def test_raw_api_call_keeps_false_values():
    """aiogram drops falsy form values - the raw helper must not (demote!)."""
    from app.core.telegram_extra import call_api_raw

    bot = _FakeBot(None, None)
    result = await call_api_raw(bot, "promoteChatMember",
                                {"chat_id": -1001, "user_id": 55,
                                 "can_manage_chat": False, "skip": None})
    assert result is True
    assert _payload(bot.calls, "promoteChatMember") == {"chat_id": "-1001",
                                                        "user_id": "55",
                                                        "can_manage_chat": "false"}


# --------------------------------------------------------------------------- #
# Identity change notices
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_identity_change_is_announced(session):
    """Telegram sends no profile-change update, so the bot diffs every message
    against the stored row and announces «از حسن با آیدی @hassan به محمد ...»."""
    from unittest.mock import AsyncMock

    from aiogram.types import User as TgUser

    from app.db.models import User
    from app.services import identity_watch

    identity_watch._recent.clear()
    session.add(User(id=555, first_name="حسن", username="hassan73273"))
    await session.flush()

    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=True)
    await identity_watch.watch_identity(
        bot, session, CHAT_ID,
        TgUser(id=555, is_bot=False, first_name="محمد", username="muhammad346456"))

    assert bot.send_message.await_count == 1
    text = bot.send_message.await_args.kwargs["text"]
    assert "حسن" in text and "محمد" in text
    assert "@hassan73273" in text and "@muhammad346456" in text

    row = await session.get(User, 555)
    assert row.first_name == "محمد"
    assert row.username == "muhammad346456"


@pytest.mark.asyncio
async def test_identity_watch_stays_silent_when_nothing_changed(session):
    from unittest.mock import AsyncMock

    from aiogram.types import User as TgUser

    from app.db.models import User
    from app.services import identity_watch

    identity_watch._recent.clear()
    session.add(User(id=556, first_name="علی", username="ali"))
    await session.flush()

    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=True)
    await identity_watch.watch_identity(
        bot, session, CHAT_ID, TgUser(id=556, is_bot=False, first_name="علی", username="ali"))
    assert bot.send_message.await_count == 0


# --------------------------------------------------------------------------- #
# Auto reply payload
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_rule_view_exposes_the_custom_response_fields(session):
    """Regression: the cached view lacked ``replies`` / ``response_*`` so the
    renderer raised ``AttributeError`` on every auto-reply trigger."""
    await filter_service.add_rule(session, CHAT_ID, "سلام", is_blocklist=False,
                                  match_mode="contains", response_text="علیک سلام")
    rule = (await filter_service.get_rules(session, CHAT_ID))[0]
    for field in ("replies", "response_file_id", "response_caption", "response_buttons"):
        assert hasattr(rule, field), field
    assert rule.response_text == "علیک سلام"
