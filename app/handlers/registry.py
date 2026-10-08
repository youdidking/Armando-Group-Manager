"""Persian text command registry and dispatcher.

Commands are matched as **exact leading token sequences** of the normalized
message, longest phrase first.  This prevents ordinary Persian sentences from
being mistaken for commands:

* ``بن`` matches the moderation command
* ``بنظرم این کار خوبه`` does **not** (its first token is ``بنظرم``)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from ..core.normalization import normalize_digits, normalize_text

logger = logging.getLogger("armando.commands")


@dataclass
class Command:
    phrases: tuple[str, ...]
    handler: Callable[..., Awaitable[None]]
    role: str | None = None
    permission: str | None = None
    category: str = "general"
    description: str = ""
    usage: str = ""
    group_only: bool = False
    private_only: bool = False
    allow_in_private: bool = False
    hidden: bool = False
    # How the arguments after the phrase may look (keeps normal talk safe):
    #   "none"    - the message must be exactly the phrase
    #   "keys"    - every argument must be a known keyword (arg_vocab)
    #   "numeric" - every argument must contain a number / time
    #   "free"    - short free text (or unlimited when the text has "=")
    arg_kind: str = ""
    arg_vocab: str = ""


@dataclass
class Match:
    command: Command
    phrase: str
    args: list[str] = field(default_factory=list)


class CommandRegistry:
    """Longest-prefix matcher over normalized Persian command phrases."""

    def __init__(self) -> None:
        self.commands: list[Command] = []
        self._index: dict[str, Command] = {}
        self._max_tokens: int = 1

    def register(self, command: Command) -> Command:
        self.commands.append(command)
        return command

    def build(self) -> None:
        """(Re)build the lookup index after all registrations."""
        index: dict[str, Command] = {}
        max_tokens = 1
        for command in self.commands:
            if not command.arg_kind:
                command.arg_kind = "none" if not command.usage else "free"
            for phrase in command.phrases:
                normalized = normalize_text(phrase, mode="command")
                if not normalized:
                    continue
                index[normalized] = command
                max_tokens = max(max_tokens, len(normalized.split()))
        self._index = index
        self._max_tokens = max_tokens
        apply_arg_rules()

    def match(self, raw_text: str) -> Match | None:
        """Match a message against the registry.

        A message is only a command when it **is** the phrase (optionally
        followed by real arguments).  Ordinary Persian sentences that merely
        start with a command word are ignored, e.g.:

        * ``قیمت دلار``                -> the price command
        * ``قیمت دلار چقدر گرون شده``  -> not a command (plain conversation)
        """
        if not raw_text:
            return None
        normalized = strip_tail_punctuation(normalize_text(raw_text, mode="command"))
        if not normalized:
            return None
        tokens = normalized.split()
        limit = min(self._max_tokens, len(tokens))
        for size in range(limit, 0, -1):
            candidate = " ".join(tokens[:size])
            command = self._index.get(candidate)
            if command is None:
                continue
            args = tokens[size:]
            if args_are_valid(command, args):
                return Match(command=command, phrase=candidate, args=args)
            return None  # phrase + ordinary words = conversation, not a command
        return None

    def by_category(self) -> dict[str, list[Command]]:
        result: dict[str, list[Command]] = {}
        for command in self.commands:
            if command.hidden:
                continue
            result.setdefault(command.category, []).append(command)
        return result


# --------------------------------------------------------------------------- #
# Strict argument validation
# --------------------------------------------------------------------------- #
# Words that only ever appear inside ordinary sentences (never in an argument).
SENTENCE_MARKERS = frozenset({
    "شده", "شدم", "شد", "است", "نیست", "بود", "بودم", "هست", "هستم", "چنده", "چند",
    "چقدر", "چطور", "چرا", "چی", "چیست", "کی", "کجا", "کدوم", "گرون", "ارزون",
    "میشه", "میشود", "میخوام", "میخواهم", "میخواد", "باید", "دارم", "داره", "داریم",
    "الان", "امروز", "فردا", "دیروز", "دیگه", "لطفا", "خواهش", "ممنون",
    "خوبه", "بد", "لازمه", "کنید", "کردم", "کرد", "میکنم", "میکنه", "بنظرم", "بنظر",
    "فکر", "یعنی", "واقعا", "اصلا", "همیشه", "هیچی", "چیزی", "کردن", "شدن",
    "خواهم", "باشه", "باشید", "نیستم", "نبود", "موند", "میمونه", "گرفت",
})

ACTION_WORDS = frozenset({
    "حذف", "اخطار", "سکوت", "میوت", "بن", "کیک", "اخراج", "هیچ", "ندارد", "none",
    "delete", "warn", "mute", "ban", "kick",
})

MODE_WORDS = frozenset({
    "کلمه", "عبارت", "دقیق", "الگو", "ریجکس", "wildcard", "word", "phrase",
    "contains", "regex",
})

CAPTCHA_WORDS = frozenset({"دکمه", "ریاضی", "ایموجی", "تصادفی", "button", "math", "emoji"})

_TAIL_PUNCTUATION = "؟?!.؛;،,:«»'\"()[]{}…"


def strip_tail_punctuation(text: str) -> str:
    """Drop trailing punctuation so ``تاریخ؟`` still matches ``تاریخ``."""
    return (text or "").rstrip(_TAIL_PUNCTUATION).strip()


def _has_digit(token: str) -> bool:
    return any(ch.isdigit() for ch in token)


def _vocab_validator(vocab: str, token: str) -> bool:
    """True when ``token`` is a keyword of the given vocabulary."""
    if token in ACTION_WORDS or token in {"همه", "لیست", "list"}:
        return True
    try:
        if vocab == "market":
            from ..services import market as market_service

            return bool(market_service.resolve_currency(token)
                        or market_service.resolve_crypto(token)
                        or market_service.resolve_gold(token))
        if vocab == "lock":
            from ..services import locks as lock_service

            return lock_service.normalize_lock_key(token) is not None
        if vocab == "role":
            from ..services import roles as role_service

            return role_service.normalize_role_key(token) is not None
        if vocab == "module":
            from ..services import disabled as disabled_service

            return disabled_service.resolve_key(token) is not None
    except Exception:  # noqa: BLE001 - never let validation break parsing
        return False
    if vocab == "action":
        return token in ACTION_WORDS
    if vocab == "mode":
        return token in MODE_WORDS
    if vocab == "captcha":
        return token in CAPTCHA_WORDS
    return False


_TARGET_USERNAME_RE = re.compile(r"^@?(?=[A-Za-z0-9_]*[A-Za-z_])[A-Za-z0-9_]{4,32}$")
_TARGET_ID_RE = re.compile(r"^\d{5,15}$")


def args_are_valid(command: Command, args: list[str]) -> bool:
    """Decide whether ``args`` look like real arguments or like a sentence."""
    kind = command.arg_kind or ("none" if not command.usage else "free")
    if not args:
        return True
    if kind == "none":
        return False
    if kind == "target":
        # ``لغو سکوت @Parhannni`` / ``لغو سکوت 123456`` / a short name.
        first = args[0]
        if _TARGET_USERNAME_RE.match(first) or _TARGET_ID_RE.match(
                normalize_digits(first, to="ascii")):
            return len(args) <= 3
        return len(args) <= 2 and not any(token in SENTENCE_MARKERS for token in args)
    if kind == "numeric":
        return len(args) <= 3 and all(_has_digit(token) for token in args)
    if kind == "keys":
        if len(args) > 3:
            return False
        return all(_vocab_validator(command.arg_vocab, token) for token in args)
    # free text: short, or delimited by "=" (قوانین/یادداشت/دستور شخصی)
    if any(token == "=" or token.startswith("=") for token in args):
        return True
    if any(token in SENTENCE_MARKERS for token in args):
        return False  # "بن کردن این کار لازمه" is a sentence, not a command
    if len(args) <= 3:
        return True
    if len(args) <= 10 and not any(token in SENTENCE_MARKERS for token in args):
        return True
    return False


# Rules for the commands that take arguments (keyed by the first phrase).
ARG_RULES: dict[str, tuple[str, str]] = {
    "عدد تصادفی": ("numeric", ""),
    "ارز": ("keys", "market"),
    "قیمت": ("keys", "market"),
    "فیلتر کردن": ("free", ""),
    "حالت فیلتر": ("keys", "mode"),
    "اقدام فیلتر": ("keys", "action"),
    "ذخیره": ("free", ""),
    "گرفتن": ("free", ""),
    "افزودن دستور": ("free", ""),
    "قفل": ("keys", "lock"),
    "بازکردن": ("keys", "lock"),
    "برخورد": ("keys", "lock"),
    "بن": ("free", ""),
    "بن موقت": ("free", ""),
    "سکوت": ("free", ""),
    "اخطار": ("free", ""),
    "تعداد اخطار": ("numeric", ""),
    "فعال‌ترین‌ها": ("numeric", ""),
    "غیرفعال‌ها": ("numeric", ""),
    "پین": ("free", ""),
    "پاکسازی": ("numeric", ""),
    "تگ": ("free", ""),
    "برم افک": ("free", ""),
    "معافیت‌ها": ("keys", "module"),
    "زمان‌بندی": ("free", ""),
    "خاموش‌کردن": ("keys", "module"),
    "تنظیم خوشامد": ("free", ""),
    "تنظیم قوانین": ("free", ""),
    "حالت کپچا": ("keys", "captcha"),
    "اقدام کپچا": ("keys", "action"),
    "حالت شب شروع": ("numeric", ""),
    "اقدام شب": ("keys", "action"),
    "ضدفلاود": ("numeric", ""),
    "ضد رید": ("numeric", ""),
    # ---- commands that act on a member: reply OR @username OR numeric id
    "بن سراسری": ("free", ""),
    "رفع بن سراسری": ("target", ""),
    "رفع بن": ("target", ""),
    "کیک": ("target", ""),
    "لغو سکوت": ("target", ""),
    "کسر اخطار": ("target", ""),
    "صفر کردن اخطار": ("target", ""),
    "وضعیت اخطار": ("target", ""),
    "تاریخچه": ("target", ""),
    "اطلاعات کاربر": ("target", ""),
    "حذف تگ": ("target", ""),
    "ارتقا": ("target", ""),
    "تنزل": ("target", ""),
    "دسترسی مدیر": ("target", ""),
    "عزل": ("target", ""),
}

_RULE_INDEX: dict[str, tuple[str, str]] = {
    normalize_text(phrase, mode="command"): rule for phrase, rule in ARG_RULES.items()
}


def apply_arg_rules() -> None:
    """Attach the argument rules above to the registered commands."""
    for command in registry.commands:
        rule = _RULE_INDEX.get(normalize_text(command.phrases[0], mode="command"))
        if rule:
            command.arg_kind, command.arg_vocab = rule


registry = CommandRegistry()


def command(*phrases: str, role: str | None = None, permission: str | None = None,
            category: str = "general", description: str = "", usage: str = "",
            group_only: bool = False, private_only: bool = False,
            allow_in_private: bool = False, hidden: bool = False):
    """Decorator registering a Persian command handler."""

    def decorator(func: Callable[..., Awaitable[None]]):
        registry.register(Command(
            phrases=tuple(phrases),
            handler=func,
            role=role,
            permission=permission,
            category=category,
            description=description,
            usage=usage,
            group_only=group_only,
            private_only=private_only,
            allow_in_private=allow_in_private,
            hidden=hidden,
        ))
        return func

    return decorator


CATEGORY_LABELS_FA = {
    "general": "🧩 عمومی",
    "moderation": "🛡 مدیریت و نظارت",
    "locks": "🔒 قفل‌ها",
    "filters": "🚫 فیلترها",
    "notes": "📝 یادداشت‌ها",
    "welcome": "👋 خوشامد و خداحافظی",
    "rules": "📜 قوانین",
    "staff": "👮 مدیران و نقش‌ها",
    "messages": "📌 پیام‌ها",
    "stats": "📊 آمار",
    "members": "👥 اعضا",
    "fun": "🎮 سرگرمی",
    "market": "💱 ارز و قیمت",
    "tools": "🛠 ابزارها",
    "federation": "🌐 فدراسیون",
    "owner": "🌍 مالک ربات",
    "afk": "💤 AFK",
    "report": "📢 گزارش",
    "tags": "🏷 تگ‌ها",
}
