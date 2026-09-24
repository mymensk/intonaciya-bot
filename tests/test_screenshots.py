import io
import shutil

import pytest
from chat_render import DARK, LIGHT, TELEGRAM, ChatStyle, find_font, render_chat

from intonaciya.dialogue import Line
from intonaciya.screenshots import TesseractReader, TextBox, boxes_to_lines

WIDTH, HEIGHT = 1000, 1800


def _box(text: str, left: int, top: int, right: int, height: int = 40) -> TextBox:
    return TextBox(text, left, top, right, top + height)


def test_attribution_by_bubble_side() -> None:
    boxes = [
        _box("Привет, как дела?", 40, 300, 500),
        _box("Отлично, а у тебя?", 480, 400, 960),
        _box("Ок", 880, 500, 960),
        _box("Тоже норм", 40, 600, 300),
    ]
    assert boxes_to_lines(boxes, WIDTH, HEIGHT) == [
        Line("them", "Привет, как дела?"),
        Line("me", "Отлично, а у тебя?"),
        Line("me", "Ок"),
        Line("them", "Тоже норм"),
    ]


def test_long_bubbles_are_attributed_by_margins() -> None:
    boxes = [
        _box("длинное сообщение собеседника на всю ширину", 40, 300, 800),
        _box("моё длинное сообщение тоже почти на всю ширину", 200, 400, 960),
    ]
    assert [line.author for line in boxes_to_lines(boxes, WIDTH, HEIGHT)] == ["them", "me"]


def test_lines_of_one_bubble_are_merged() -> None:
    boxes = [
        _box("первая строка", 40, 300, 600),
        _box("вторая строка", 40, 355, 600),
        _box("новое сообщение", 40, 480, 600),
    ]
    assert boxes_to_lines(boxes, WIDTH, HEIGHT) == [
        Line("them", "первая строка вторая строка"),
        Line("them", "новое сообщение"),
    ]


def test_chrome_separators_and_timestamps_are_dropped() -> None:
    boxes = [
        _box("9:41", 40, 20, 120),  # status bar
        _box("Мария", 440, 100, 560),  # header
        _box("Сегодня", 430, 250, 570),  # centered date separator
        _box("Привет 12:40", 40, 300, 400),
        _box("12:41 ✓✓", 800, 400, 960),  # bare timestamp
        _box("Сообщение", 40, 1720, 300),  # input field
    ]
    assert boxes_to_lines(boxes, WIDTH, HEIGHT) == [Line("them", "Привет")]


DIALOGUE = [
    Line("them", "Привет! Спасибо за вечер, давно так не смеялась"),
    Line("me", "Взаимно! Теперь я знаю, что ты ешь пиццу с ананасами"),
    Line("them", "Это был тест. Ты его прошёл"),
    Line("them", "Может, в субботу сходим в кино?"),
]


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract is not installed")
@pytest.mark.skipif(find_font() is None, reason="no Cyrillic font for rendering")
@pytest.mark.parametrize("style", [LIGHT, TELEGRAM, DARK], ids=["light", "telegram", "dark"])
async def test_tesseract_reads_rendered_chat(style: ChatStyle) -> None:
    image = render_chat(DIALOGUE, style, find_font())
    buffer = io.BytesIO()
    image.save(buffer, "PNG")

    lines = await TesseractReader().read(buffer.getvalue())

    assert [line.author for line in lines] == [line.author for line in DIALOGUE]
    assert "субботу" in lines[-1].text
