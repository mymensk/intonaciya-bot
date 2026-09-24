"""Turn a chat screenshot into dialogue lines with authorship.

Plain OCR loses who wrote what: in a screenshot authorship is encoded by bubble
position (interlocutor on the left, user on the right). So the OCR engine must
return text boxes, and authorship is inferred from their horizontal margins.
"""

import asyncio
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageChops, ImageFilter, ImageOps

from intonaciya.dialogue import Author, Line

# Full-height phone screenshots carry a status bar and chat header on top and
# an input field at the bottom; skip those bands.
_TALL_ASPECT = 1.6
_TOP_CHROME = 0.1
_BOTTOM_CHROME = 0.08

# Date separators and service messages are short and centered.
_CENTER_MIN_MARGIN = 0.2
_CENTER_MAX_SKEW = 0.1

# Lines of one bubble sit closer than lines of neighbouring bubbles.
_SAME_BUBBLE_GAP = 1.0

_TRAILING_TIME = re.compile(r"\s*\d{1,2}[:.]\d{2}\s*[✓✔√vV/]*\s*$")
# OCR noise from timestamps, read marks and bubble edges has at most one letter.
_LETTER = re.compile(r"[^\W\d_]")
_MIN_LETTERS = 2

_MIN_WORD_CONFIDENCE = 30
_MIN_OCR_WIDTH = 1000
# Blur radius for background estimation, as a share of image width.
_BACKGROUND_BLUR = 0.02
# Max horizontal gap between words of one line, in line heights.
_WORD_GAP = 1.5


@dataclass(frozen=True, slots=True)
class TextBox:
    text: str
    left: int
    top: int
    right: int
    bottom: int

    @property
    def height(self) -> int:
        return self.bottom - self.top


class ScreenshotReader(Protocol):
    async def read(self, image: bytes) -> list[Line]: ...


def _clean(text: str) -> str:
    return _TRAILING_TIME.sub("", text).strip()


def _side(box: TextBox, width: int) -> Author | None:
    left_margin = box.left / width
    right_margin = (width - box.right) / width
    is_centered = (
        min(left_margin, right_margin) > _CENTER_MIN_MARGIN
        and abs(left_margin - right_margin) < _CENTER_MAX_SKEW
    )
    if is_centered:
        return None
    return "them" if left_margin < right_margin else "me"


def boxes_to_lines(boxes: Sequence[TextBox], width: int, height: int) -> list[Line]:
    """Group OCR text lines into chat messages and attribute them by bubble side."""
    is_full_screen = height / width > _TALL_ASPECT
    messages: list[tuple[Author, list[str], TextBox]] = []

    for box in sorted(boxes, key=lambda b: b.top):
        if is_full_screen and (
            box.top < height * _TOP_CHROME or box.bottom > height * (1 - _BOTTOM_CHROME)
        ):
            continue
        text = _clean(box.text)
        author = _side(box, width)
        if not text or len(_LETTER.findall(text)) < _MIN_LETTERS or author is None:
            continue

        if messages:
            prev_author, prev_texts, prev_box = messages[-1]
            gap = box.top - prev_box.bottom
            if prev_author == author and gap < prev_box.height * _SAME_BUBBLE_GAP:
                prev_texts.append(text)
                messages[-1] = (author, prev_texts, box)
                continue
        messages.append((author, [text], box))

    return [Line(author=author, text=" ".join(texts)) for author, texts, _ in messages]


def _prepare(image: Image.Image) -> Image.Image:
    """Make text dark on white regardless of bubble and theme colors.

    Bubbles mix polarities (white on blue, black on grey, white on dark grey),
    which no single threshold separates. Subtracting a blurred copy leaves only
    sharp detail, i.e. text, whatever its color.
    """
    gray = ImageOps.grayscale(image)
    if gray.width < _MIN_OCR_WIDTH:
        scale = _MIN_OCR_WIDTH / gray.width
        gray = gray.resize((_MIN_OCR_WIDTH, round(gray.height * scale)), Image.Resampling.LANCZOS)
    background = gray.filter(ImageFilter.GaussianBlur(gray.width * _BACKGROUND_BLUR))
    detail = ImageChops.difference(gray, background)
    return ImageOps.invert(ImageOps.autocontrast(detail, cutoff=1))


def _ocr_words(image: Image.Image, lang: str) -> list[TextBox]:
    import pytesseract

    # Sparse-text mode: chat bubbles are scattered short fragments, not a page.
    # Sauvola thresholding binarizes locally and copes with uneven contrast.
    data = pytesseract.image_to_data(
        image,
        lang=lang,
        config="--psm 11 -c thresholding_method=2",
        output_type=pytesseract.Output.DICT,
    )
    words = []
    for i, text in enumerate(data["text"]):
        confidence = float(data["conf"][i])
        if text.strip() and confidence >= _MIN_WORD_CONFIDENCE:
            left, top = data["left"][i], data["top"][i]
            box = TextBox(text, left, top, left + data["width"][i], top + data["height"][i])
            words.append(box)
    return words


def _words_to_lines(words: Sequence[TextBox]) -> list[TextBox]:
    """Join words sitting on the same baseline and close to each other."""
    lines: list[list[TextBox]] = []
    for word in sorted(words, key=lambda w: w.left):
        center = (word.top + word.bottom) / 2
        for line in lines:
            last = line[-1]
            same_row = abs(center - (last.top + last.bottom) / 2) < max(last.height, 1) * 0.5
            close = word.left - last.right < max(last.height, word.height) * _WORD_GAP
            if same_row and close:
                line.append(word)
                break
        else:
            lines.append([word])

    return [
        TextBox(
            text=" ".join(w.text for w in line),
            left=min(w.left for w in line),
            top=min(w.top for w in line),
            right=max(w.right for w in line),
            bottom=max(w.bottom for w in line),
        )
        for line in lines
    ]


class TesseractReader:
    """Local OCR: screenshots never leave the server."""

    # Russian only: with "rus+eng" Cyrillic words get read as Latin lookalikes.
    def __init__(self, lang: str = "rus") -> None:
        self._lang = lang

    async def read(self, image: bytes) -> list[Line]:
        return await asyncio.to_thread(self._read_sync, image)

    def _read_sync(self, image: bytes) -> list[Line]:
        with Image.open(io.BytesIO(image)) as original:
            prepared = _prepare(original)
        lines = _words_to_lines(_ocr_words(prepared, self._lang))
        return boxes_to_lines(lines, prepared.width, prepared.height)
