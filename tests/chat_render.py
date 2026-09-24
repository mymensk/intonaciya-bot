"""Render synthetic chat screenshots for OCR tests."""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from intonaciya.dialogue import Line

FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


@dataclass(frozen=True)
class ChatStyle:
    background: str
    them_bubble: str
    them_text: str
    me_bubble: str
    me_text: str
    chrome_text: str
    show_time: bool


LIGHT = ChatStyle("#FFFFFF", "#E9E9EB", "#000000", "#0B84FE", "#FFFFFF", "#000000", False)
TELEGRAM = ChatStyle("#CFE3C8", "#FFFFFF", "#000000", "#E1FEC6", "#000000", "#000000", True)
DARK = ChatStyle("#000000", "#262628", "#FFFFFF", "#0B84FE", "#FFFFFF", "#FFFFFF", False)


def find_font() -> Path | None:
    return next((Path(p) for p in FONT_CANDIDATES if Path(p).exists()), None)


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int
) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_chat(lines: list[Line], style: ChatStyle, font_path: Path) -> Image.Image:
    width, height = 750, 1334
    image = Image.new("RGB", (width, height), style.background)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 30)
    small = ImageFont.truetype(str(font_path), 22)

    draw.text((40, 20), "9:41", font=small, fill=style.chrome_text)
    draw.text((width // 2, 80), "Мария", font=font, fill=style.chrome_text, anchor="mm")
    draw.text((width // 2, 170), "Сегодня", font=small, fill=style.chrome_text, anchor="mm")
    draw.text((40, height - 60), "Сообщение", font=font, fill="#8E8E93")

    y = 220
    max_text = int(width * 0.62)
    padding = 20
    for line in lines:
        wrapped = _wrap(draw, line.text, font, max_text)
        text_width = max(draw.textlength(w, font=font) for w in wrapped)
        if style.show_time:
            text_width = max(text_width, draw.textlength(wrapped[-1], font=font) + 90)
        bubble_w = int(text_width) + padding * 2
        bubble_h = len(wrapped) * 40 + padding * 2 + (24 if style.show_time else 0)
        is_me = line.author == "me"
        x = width - 24 - bubble_w if is_me else 24
        draw.rounded_rectangle(
            (x, y, x + bubble_w, y + bubble_h),
            radius=28,
            fill=style.me_bubble if is_me else style.them_bubble,
        )
        text_color = style.me_text if is_me else style.them_text
        for i, w in enumerate(wrapped):
            draw.text((x + padding, y + padding + i * 40), w, font=font, fill=text_color)
        if style.show_time:
            stamp = "12:41 ✓✓" if is_me else "12:40"
            draw.text(
                (x + bubble_w - padding, y + bubble_h - 12),
                stamp,
                font=small,
                fill="#5FA35A" if is_me else "#8E8E93",
                anchor="rb",
            )
        y += bubble_h + 16
    return image
