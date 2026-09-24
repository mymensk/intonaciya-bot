from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from aiogram.types import Message, MessageOriginHiddenUser, MessageOriginUser, User

Author = Literal["me", "them"]

AUTHOR_LABELS: dict[Author, str] = {"me": "Я", "them": "Собеседник"}

_MEDIA_PLACEHOLDERS = {
    "photo": "[фото]",
    "video": "[видео]",
    "voice": "[голосовое сообщение]",
    "video_note": "[видеосообщение]",
    "audio": "[аудио]",
    "sticker": "[стикер]",
    "animation": "[гифка]",
    "document": "[файл]",
}


@dataclass(frozen=True, slots=True)
class Line:
    author: Author
    text: str


def attribute(message: Message, user: User) -> Author:
    """Decide who wrote a forwarded message: the bot user or their interlocutor.

    Telegram keeps the original sender of a forward. Users who hide their account
    in forwards show up as a bare name, so compare it with the user's own name.
    """
    origin = message.forward_origin
    if isinstance(origin, MessageOriginUser):
        return "me" if origin.sender_user.id == user.id else "them"
    if isinstance(origin, MessageOriginHiddenUser):
        return "me" if origin.sender_user_name == user.full_name else "them"
    return "them"


def extract_text(message: Message) -> str:
    text = message.text or message.caption
    placeholder = _MEDIA_PLACEHOLDERS.get(message.content_type, "")
    if text and placeholder:
        return f"{placeholder} {text}"
    return text or placeholder or "[сообщение]"


def to_line(message: Message, user: User) -> Line:
    return Line(author=attribute(message, user), text=extract_text(message))


def format_dialogue(lines: Sequence[Line]) -> str:
    return "\n".join(f"{AUTHOR_LABELS[line.author]}: {line.text}" for line in lines)
