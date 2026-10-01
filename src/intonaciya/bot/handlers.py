import asyncio
import io
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
    User,
)

from intonaciya.bot import texts
from intonaciya.bot.network import retry
from intonaciya.config import Settings
from intonaciya.dialogue import format_dialogue, to_line
from intonaciya.llm import LLMProvider, RefusalKind, classify_refusal
from intonaciya.metrics import MSK, Metrics, clean_source, current_user_id
from intonaciya.metrics_report import build_summary, format_summary
from intonaciya.prompts import build_coach_messages
from intonaciya.screenshots import ScreenshotReader
from intonaciya.sessions import SessionStore

# Forwards arrive as a burst of separate updates: acknowledge once the burst settles.
ACK_DELAY_S = 1.5

logger = logging.getLogger(__name__)
router = Router()

_pending_acks: dict[int, asyncio.Task[None]] = {}

_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.ANALYZE_BUTTON, callback_data="analyze"),
            InlineKeyboardButton(text=texts.RESET_BUTTON, callback_data="reset"),
        ]
    ]
)
_CONSENT_KEYBOARD = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text=texts.CONSENT_BUTTON, callback_data="consent")]]
)
# Picture sent with the first message after "Start".
START_PICTURE = Path(__file__).resolve().parents[3] / "assets" / "start.png"
# Telegram file_id of the uploaded picture: upload once, then reuse.
_start_picture_id: str | None = None

# Reachable before consent: the consent flow itself and the privacy notice.
_OPEN_COMMANDS = ("/start", "/privacy", "/forget")


class AllowlistMiddleware:
    """Closed beta: only listed users get through, unless the bot is open to everyone."""

    def __init__(self, allowed_user_ids: frozenset[int], *, open_to_everyone: bool = False) -> None:
        self._allowed = allowed_user_ids
        self._open = open_to_everyone

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if self._open or (user and user.id in self._allowed):
            return await handler(event, data)
        if isinstance(event, Message) and user:
            await event.answer(texts.CLOSED_BETA.format(user_id=user.id))
        return None


class ConsentMiddleware:
    """Lets users in once they confirm their age and consent to data processing.

    Also binds the user to the update, so metrics recorded deeper down know whose it is.
    """

    def __init__(self, metrics: Metrics) -> None:
        self._metrics = metrics

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        token = current_user_id.set(user.id)
        try:
            if self._metrics.has_consented(user.id) or _is_open(event):
                return await handler(event, data)
            if isinstance(event, Message):
                await event.answer(texts.CONSENT_REQUIRED)
            elif isinstance(event, CallbackQuery):
                await event.answer(texts.CONSENT_REQUIRED, show_alert=True)
            return None
        finally:
            current_user_id.reset(token)


def _is_open(event: TelegramObject) -> bool:
    if isinstance(event, CallbackQuery):
        return event.data == "consent"
    if isinstance(event, Message) and event.text:
        command = event.text.split(maxsplit=1)[0].split("@", 1)[0]
        return command in _OPEN_COMMANDS
    return False


def _over_limit(user_id: int, metrics: Metrics, settings: Settings) -> bool:
    if metrics.is_test(user_id) or metrics.llm_calls_today(user_id) < settings.daily_llm_limit:
        return False
    metrics.record("limit_hit", user_id)
    return True


def _cancel_ack(user_id: int) -> None:
    task = _pending_acks.pop(user_id, None)
    if task:
        task.cancel()


async def _ack_after_delay(message: Message, sessions: SessionStore, user_id: int) -> None:
    await asyncio.sleep(ACK_DELAY_S)
    _pending_acks.pop(user_id, None)
    session = sessions.get(user_id)
    if session:
        dialogue = format_dialogue(session.lines)
        if len(dialogue) > texts.DIALOGUE_PREVIEW_CHARS:
            dialogue = "…" + dialogue[-texts.DIALOGUE_PREVIEW_CHARS :]
        await message.answer(
            texts.DIALOGUE_RECEIVED.format(dialogue=dialogue), reply_markup=_KEYBOARD
        )


def _schedule_ack(message: Message, sessions: SessionStore, user_id: int) -> None:
    _cancel_ack(user_id)
    _pending_acks[user_id] = asyncio.create_task(_ack_after_delay(message, sessions, user_id))


async def _analyze(
    message: Message,
    user_id: int,
    request: str,
    sessions: SessionStore,
    llm: LLMProvider,
    metrics: Metrics,
    settings: Settings,
) -> None:
    session = sessions.get(user_id)
    if session is None:
        await message.answer(texts.SESSION_EXPIRED)
        return
    if _over_limit(user_id, metrics, settings):
        await message.answer(texts.LIMIT_REACHED)
        return

    await message.answer(texts.THINKING)
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    messages = build_coach_messages(texts.DEFAULT_SITUATION, session.lines, request)
    started = time.monotonic()
    try:
        completion = await llm.complete(messages)
    except Exception as exc:
        # Log only the error type: exception messages may echo conversation text.
        logger.error("llm_failed user=%s error=%s", user_id, type(exc).__name__)
        latency_ms = (time.monotonic() - started) * 1000
        metrics.record(
            "llm_call",
            user_id,
            purpose="coach",
            status="error",
            model=llm.model,
            latency_ms=latency_ms,
        )
        metrics.record("analysis", user_id, status="error", value=len(session.lines))
        await message.answer(texts.LLM_ERROR)
        return

    latency_ms = (time.monotonic() - started) * 1000
    refusal = classify_refusal(completion)
    metrics.record(
        "llm_call",
        user_id,
        purpose="coach",
        status="ok",
        model=llm.model,
        tokens_in=completion.prompt_tokens,
        tokens_out=completion.completion_tokens,
        latency_ms=latency_ms,
    )
    metrics.record("analysis", user_id, status=refusal.value, value=len(session.lines))
    logger.info(
        "analysis_done user=%s lines=%s refusal=%s latency_ms=%d tokens_in=%s tokens_out=%s",
        user_id,
        len(session.lines),
        refusal.value,
        latency_ms,
        completion.prompt_tokens,
        completion.completion_tokens,
    )
    sessions.pop(user_id)
    if refusal is RefusalKind.HARD:
        await message.answer(texts.LLM_BLOCKED)
    else:
        await message.answer(completion.text)


async def _send_with_picture(
    message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    global _start_picture_id
    if _start_picture_id is None and not START_PICTURE.exists():
        await message.answer(text, reply_markup=reply_markup)
        return
    sent = await message.answer_photo(
        _start_picture_id or FSInputFile(START_PICTURE), caption=text, reply_markup=reply_markup
    )
    if sent.photo:
        _start_picture_id = sent.photo[-1].file_id


@router.message(CommandStart())
async def on_start(
    message: Message, command: CommandObject, event_from_user: User, metrics: Metrics
) -> None:
    # t.me/<bot>?start=<source> tags where the user came from.
    source = clean_source(command.args)
    metrics.register(event_from_user.id, source)
    metrics.record("start", event_from_user.id, purpose=source)
    if metrics.has_consented(event_from_user.id):
        await _send_with_picture(message, texts.START)
    else:
        await _send_with_picture(message, texts.CONSENT, _CONSENT_KEYBOARD)


@router.callback_query(F.data == "consent")
async def on_consent(callback: CallbackQuery, metrics: Metrics) -> None:
    if not metrics.has_consented(callback.from_user.id):
        metrics.record_consent(callback.from_user.id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(texts.START)


@router.message(Command("privacy"))
async def on_privacy(message: Message, settings: Settings) -> None:
    text = texts.PRIVACY
    if settings.support_contact:
        text += texts.PRIVACY_CONTACT.format(contact=settings.support_contact)
    await message.answer(text)


@router.message(Command("forget"))
async def on_forget(
    message: Message, event_from_user: User, sessions: SessionStore, metrics: Metrics
) -> None:
    _cancel_ack(event_from_user.id)
    sessions.pop(event_from_user.id)
    metrics.forget(event_from_user.id)
    await message.answer(texts.FORGET_DONE)


@router.message(Command("stats"))
async def on_stats(
    message: Message, event_from_user: User, metrics: Metrics, settings: Settings
) -> None:
    if event_from_user.id != settings.admin_user_id:
        await message.answer(texts.NO_DIALOGUE)
        return
    summary = build_summary(metrics.connection, days=7, today=datetime.now(MSK).date())
    await message.answer(format_summary(summary))


@router.message(Command("reset"))
async def on_reset(
    message: Message, event_from_user: User, sessions: SessionStore, metrics: Metrics
) -> None:
    _cancel_ack(event_from_user.id)
    sessions.pop(event_from_user.id)
    metrics.record("reset", event_from_user.id)
    await message.answer(texts.RESET_DONE)


@router.message(F.forward_origin)
async def on_forward(
    message: Message, event_from_user: User, sessions: SessionStore, metrics: Metrics
) -> None:
    sessions.add(event_from_user.id, message.message_id, [to_line(message, event_from_user)])
    metrics.record("forward", event_from_user.id)
    _schedule_ack(message, sessions, event_from_user.id)


async def _download(message: Message, file_id: str) -> bytes:
    buffer = io.BytesIO()
    await message.bot.download(file_id, destination=buffer)
    return buffer.getvalue()


@router.message(F.photo | F.document.mime_type.startswith("image/"))
async def on_screenshot(
    message: Message,
    event_from_user: User,
    sessions: SessionStore,
    screenshots: ScreenshotReader,
    metrics: Metrics,
    settings: Settings,
) -> None:
    if _over_limit(event_from_user.id, metrics, settings):
        await message.answer(texts.LIMIT_REACHED)
        return
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    started = time.monotonic()
    try:
        # Kept in memory only and dropped with this call: screenshots are never stored.
        image = await retry(lambda: _download(message, file_id), label="download")
        lines = await screenshots.read(image)
    except Exception as exc:
        logger.error("screenshot_failed user=%s error=%s", event_from_user.id, type(exc).__name__)
        metrics.record("screenshot", event_from_user.id, status="error")
        await message.answer(texts.SCREENSHOT_ERROR)
        return

    metrics.record(
        "screenshot",
        event_from_user.id,
        status="ok" if lines else "empty",
        value=len(lines),
        latency_ms=(time.monotonic() - started) * 1000,
    )
    logger.info(
        "screenshot_read user=%s lines=%s latency_ms=%d",
        event_from_user.id,
        len(lines),
        (time.monotonic() - started) * 1000,
    )
    if not lines:
        await message.answer(texts.SCREENSHOT_EMPTY)
        return
    sessions.add(event_from_user.id, message.message_id, lines)
    _schedule_ack(message, sessions, event_from_user.id)


@router.message(F.text)
async def on_request(
    message: Message,
    event_from_user: User,
    sessions: SessionStore,
    llm: LLMProvider,
    metrics: Metrics,
    settings: Settings,
) -> None:
    if sessions.get(event_from_user.id) is None:
        await message.answer(texts.NO_DIALOGUE)
        return
    _cancel_ack(event_from_user.id)
    await _analyze(message, event_from_user.id, message.text, sessions, llm, metrics, settings)


@router.message()
async def on_other(message: Message) -> None:
    await message.answer(texts.NO_DIALOGUE)


@router.callback_query(F.data == "analyze")
async def on_analyze_button(
    callback: CallbackQuery,
    sessions: SessionStore,
    llm: LLMProvider,
    metrics: Metrics,
    settings: Settings,
) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _analyze(
            callback.message,
            callback.from_user.id,
            texts.DEFAULT_REQUEST,
            sessions,
            llm,
            metrics,
            settings,
        )


@router.callback_query(F.data == "reset")
async def on_reset_button(callback: CallbackQuery, sessions: SessionStore) -> None:
    sessions.pop(callback.from_user.id)
    await callback.answer(texts.RESET_DONE)
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
