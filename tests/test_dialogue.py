from datetime import datetime

import pytest
from aiogram.types import (
    Chat,
    Message,
    MessageOrigin,
    MessageOriginChannel,
    MessageOriginHiddenUser,
    MessageOriginUser,
    PhotoSize,
    User,
)

from intonaciya.dialogue import Line, attribute, extract_text, format_dialogue, to_line

NOW = datetime(2026, 9, 24, 12, 0)
ME = User(id=1, is_bot=False, first_name="Анна", last_name="Петрова")
THEM = User(id=2, is_bot=False, first_name="Иван")


def _message(origin: MessageOrigin | None = None, **fields: object) -> Message:
    return Message(
        message_id=1,
        date=NOW,
        chat=Chat(id=ME.id, type="private"),
        from_user=ME,
        forward_origin=origin,
        **fields,
    )


def _from_user(user: User) -> MessageOriginUser:
    return MessageOriginUser(type="user", date=NOW, sender_user=user)


def _from_hidden(name: str) -> MessageOriginHiddenUser:
    return MessageOriginHiddenUser(type="hidden_user", date=NOW, sender_user_name=name)


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        (_from_user(ME), "me"),
        (_from_user(THEM), "them"),
        (_from_hidden("Анна Петрова"), "me"),
        (_from_hidden("Иван"), "them"),
        (
            MessageOriginChannel(
                type="channel", date=NOW, chat=Chat(id=-100, type="channel"), message_id=5
            ),
            "them",
        ),
    ],
)
def test_attribute(origin: MessageOrigin, expected: str) -> None:
    assert attribute(_message(origin, text="привет"), ME) == expected


def test_extract_text_plain() -> None:
    assert extract_text(_message(text="привет")) == "привет"


def test_extract_text_media_with_caption() -> None:
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]
    assert extract_text(_message(photo=photo, caption="смотри")) == "[фото] смотри"


def test_extract_text_media_without_caption() -> None:
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]
    assert extract_text(_message(photo=photo)) == "[фото]"


def test_to_line_and_format_dialogue() -> None:
    lines = [
        to_line(_message(_from_user(THEM), text="Как дела?"), ME),
        to_line(_message(_from_user(ME), text="Отлично"), ME),
    ]
    assert lines == [Line("them", "Как дела?"), Line("me", "Отлично")]
    assert format_dialogue(lines) == "Собеседник: Как дела?\nЯ: Отлично"
