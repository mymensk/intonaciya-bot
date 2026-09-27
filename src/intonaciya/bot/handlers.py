import asyncio
import io
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
    User,
)

from intonaciya.bot import texts
from intonaciya.bot.network import retry
from intonaciya.dialogue import format_dialogue, to_line
from intonaciya.llm import LLMProvider, RefusalKind, classify_refusal
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
    message: Message, user_id: int, request: str, sessions: SessionStore, llm: LLMProvider
) -> None:
    session = sessions.get(user_id)
    if session is None:
        await message.answer(texts.SESSION_EXPIRED)
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
        await message.answer(texts.LLM_ERROR)
        return

    refusal = classify_refusal(completion)
    logger.info(
        "analysis_done user=%s lines=%s refusal=%s latency_ms=%d tokens_in=%s tokens_out=%s",
        user_id,
        len(session.lines),
        refusal.value,
        (time.monotonic() - started) * 1000,
        completion.prompt_tokens,
        completion.completion_tokens,
    )
    sessions.pop(user_id)
    if refusal is RefusalKind.HARD:
        await message.answer(texts.LLM_BLOCKED)
    else:
        await message.answer(completion.text)


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    await message.answer(texts.START)


@router.message(Command("reset"))
async def on_reset(message: Message, event_from_user: User, sessions: SessionStore) -> None:
    _cancel_ack(event_from_user.id)
    sessions.pop(event_from_user.id)
    await message.answer(texts.RESET_DONE)


@router.message(F.forward_origin)
async def on_forward(message: Message, event_from_user: User, sessions: SessionStore) -> None:
    sessions.add(event_from_user.id, message.message_id, [to_line(message, event_from_user)])
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
) -> None:
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    started = time.monotonic()
    try:
        # Kept in memory only and dropped with this call: screenshots are never stored.
        image = await retry(lambda: _download(message, file_id), label="download")
        lines = await screenshots.read(image)
    except Exception as exc:
        logger.error("screenshot_failed user=%s error=%s", event_from_user.id, type(exc).__name__)
        await message.answer(texts.SCREENSHOT_ERROR)
        return

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
    message: Message, event_from_user: User, sessions: SessionStore, llm: LLMProvider
) -> None:
    if sessions.get(event_from_user.id) is None:
        await message.answer(texts.NO_DIALOGUE)
        return
    _cancel_ack(event_from_user.id)
    await _analyze(message, event_from_user.id, message.text, sessions, llm)


@router.message()
async def on_other(message: Message) -> None:
    await message.answer(texts.NO_DIALOGUE)


@router.callback_query(F.data == "analyze")
async def on_analyze_button(
    callback: CallbackQuery, sessions: SessionStore, llm: LLMProvider
) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _analyze(
            callback.message, callback.from_user.id, texts.DEFAULT_REQUEST, sessions, llm
        )


@router.callback_query(F.data == "reset")
async def on_reset_button(callback: CallbackQuery, sessions: SessionStore) -> None:
    sessions.pop(callback.from_user.id)
    await callback.answer(texts.RESET_DONE)
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=None)
