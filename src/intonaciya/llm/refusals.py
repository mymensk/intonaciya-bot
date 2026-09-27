from enum import StrEnum

from intonaciya.llm.base import Completion


class RefusalKind(StrEnum):
    NONE = "none"
    # The API itself blocked the answer: "blacklist" in GigaChat, "content_filter" in
    # OpenAI-compatible APIs.
    HARD = "hard"
    # The model answered, but with a refusal instead of help.
    SOFT = "soft"


# Canned GigaChat deflections: a refusal regardless of reply length.
_CANNED_MARKERS = (
    "что-то в вашем вопросе меня смущает",
    "не люблю менять тему разговора",
    "совсем не хочу говорить на эту тему",
    "может, поговорим на другую тему",
    "давайте поговорим о чем-нибудь другом",
    "давайте сменим тему",
)

# Generic refusal phrases: only count in short replies, since a real answer
# can legitimately contain them (e.g. an apology draft).
_GENERIC_MARKERS = (
    "не могу помочь",
    "не могу выполнить",
    "не могу ответить",
    "не могу предоставить",
    "не буду помогать",
    "не могу участвовать",
    "противоречит моим принципам",
    "как языковая модель",
    "как нейросетевая языковая модель",
)
_SHORT_REPLY_CHARS = 400


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def classify_refusal(completion: Completion) -> RefusalKind:
    if completion.finish_reason in ("blacklist", "content_filter"):
        return RefusalKind.HARD

    text = _normalize(completion.text)
    if any(marker in text for marker in _CANNED_MARKERS):
        return RefusalKind.SOFT
    if len(text) < _SHORT_REPLY_CHARS and any(marker in text for marker in _GENERIC_MARKERS):
        return RefusalKind.SOFT
    return RefusalKind.NONE
