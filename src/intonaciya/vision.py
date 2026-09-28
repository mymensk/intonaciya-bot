"""Read chat screenshots with a multimodal model behind an OpenAI-compatible gateway."""

import base64
import json
import logging
import re
import time
from typing import Any

import httpx

from intonaciya.dialogue import Line
from intonaciya.metrics import Metrics
from intonaciya.prompts import load_prompt
from intonaciya.screenshots import ScreenshotReader

logger = logging.getLogger(__name__)

_JSON_ARRAY = re.compile(r"\[.*\]", re.DOTALL)
_AUTHORS = {"me", "them"}


class VisionParseError(ValueError):
    """The model answered, but not with the expected list of messages."""


def parse_lines(answer: str) -> list[Line]:
    match = _JSON_ARRAY.search(answer)
    if match is None:
        raise VisionParseError("no JSON array in the answer")
    try:
        items: Any = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise VisionParseError("malformed JSON") from exc
    if not isinstance(items, list):
        raise VisionParseError("JSON is not a list")

    lines = []
    for item in items:
        if not isinstance(item, dict):
            continue
        author, text = item.get("author"), str(item.get("text") or "").strip()
        if author in _AUTHORS and text:
            lines.append(Line(author=author, text=text))
    return lines


class VisionReader:
    """Sends the screenshot to a vision model and parses the messages it lists.

    The image is sent inline and never stored by us; logs carry no content.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        model: str,
        timeout_s: float = 60.0,
        attempts: int = 2,
        client: httpx.AsyncClient | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self.model = model
        self._metrics = metrics
        self._base_url = base_url.rstrip("/")
        self._attempts = attempts
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s, headers={"Authorization": f"Bearer {api_key}"}
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def read(self, image: bytes) -> list[Line]:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": load_prompt("screenshot_extract.md")},
                        {"type": "image_url", "image_url": {"url": _data_url(image)}},
                    ],
                }
            ],
        }
        for attempt in range(1, self._attempts + 1):
            answer = await self._call(payload)
            try:
                return parse_lines(answer)
            except VisionParseError as exc:
                logger.warning("vision_parse_failed attempt=%s error=%s", attempt, exc)
                if attempt == self._attempts:
                    raise
        return []

    async def _call(self, payload: dict[str, Any]) -> str:
        started = time.monotonic()
        usage: dict[str, Any] = {}
        status = "error"
        try:
            response = await self._client.post(f"{self._base_url}/chat/completions", json=payload)
            response.raise_for_status()
            body = response.json()
            usage = body.get("usage") or {}
            status = "ok"
            return body["choices"][0]["message"].get("content") or ""
        finally:
            if self._metrics:
                self._metrics.record(
                    "llm_call",
                    purpose="vision",
                    status=status,
                    model=self.model,
                    tokens_in=usage.get("prompt_tokens"),
                    tokens_out=usage.get("completion_tokens"),
                    latency_ms=(time.monotonic() - started) * 1000,
                )


class FallbackReader:
    """Tries the primary reader and falls back to the secondary one on any failure."""

    def __init__(self, primary: ScreenshotReader, fallback: ScreenshotReader) -> None:
        self._primary = primary
        self._fallback = fallback

    async def read(self, image: bytes) -> list[Line]:
        try:
            return await self._primary.read(image)
        except Exception as exc:
            logger.warning("screenshot_primary_failed error=%s", type(exc).__name__)
            return await self._fallback.read(image)


def _data_url(image: bytes) -> str:
    mime = "image/png" if image.startswith(b"\x89PNG") else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(image).decode()}"
