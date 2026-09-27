from collections.abc import Sequence
from dataclasses import asdict

import httpx

from intonaciya.llm.base import Completion, Message


class OpenAICompatibleProvider:
    """Client for OpenAI-compatible chat completions APIs (e.g. an LLM gateway).

    Never logs message contents: the provider handles private conversations.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        model: str,
        timeout_s: float = 120.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s, headers={"Authorization": f"Bearer {api_key}"}
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, messages: Sequence[Message]) -> Completion:
        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            json={"model": self.model, "messages": [asdict(m) for m in messages]},
        )
        response.raise_for_status()

        data = response.json()
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        return Completion(
            text=choice["message"].get("content") or "",
            finish_reason=choice.get("finish_reason") or "",
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )
