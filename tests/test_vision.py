import json

import httpx
import pytest

from intonaciya.dialogue import Line
from intonaciya.vision import FallbackReader, VisionParseError, VisionReader, parse_lines

PNG = b"\x89PNG\r\n\x1a\nfake"


def test_parse_plain_json() -> None:
    answer = '[{"author": "them", "text": "Привет"}, {"author": "me", "text": "Ок"}]'
    assert parse_lines(answer) == [Line("them", "Привет"), Line("me", "Ок")]


def test_parse_json_in_code_fence_and_skips_bad_items() -> None:
    answer = (
        "```json\n"
        '[{"author": "them", "text": " Привет "}, {"author": "system", "text": "12:40"},'
        ' {"author": "me", "text": ""}, "мусор"]\n```'
    )
    assert parse_lines(answer) == [Line("them", "Привет")]


@pytest.mark.parametrize("answer", ["не вижу сообщений", "[{author: them}]", '{"a": 1}'])
def test_parse_rejects_non_list(answer: str) -> None:
    with pytest.raises(VisionParseError):
        parse_lines(answer)


def _reader(answers: list[str], requests: list[httpx.Request]) -> VisionReader:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        content = answers.pop(0)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return VisionReader("key", base_url="https://gw.example/v1", model="vl", client=client)


async def test_read_sends_inline_image_and_parses_answer() -> None:
    requests: list[httpx.Request] = []
    reader = _reader(['[{"author": "me", "text": "Привет"}]'], requests)

    assert await reader.read(PNG) == [Line("me", "Привет")]

    body = json.loads(requests[0].content)
    assert body["model"] == "vl"
    image_part = body["messages"][0]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


async def test_read_retries_once_on_malformed_answer() -> None:
    requests: list[httpx.Request] = []
    reader = _reader(["[{oops", '[{"author": "them", "text": "Да"}]'], requests)

    assert await reader.read(PNG) == [Line("them", "Да")]
    assert len(requests) == 2


class _Failing:
    async def read(self, image: bytes) -> list[Line]:
        raise httpx.ConnectError("down")


class _Static:
    async def read(self, image: bytes) -> list[Line]:
        return [Line("them", "из Tesseract")]


async def test_fallback_reader_uses_secondary_on_failure() -> None:
    assert await FallbackReader(_Failing(), _Static()).read(PNG) == [Line("them", "из Tesseract")]
