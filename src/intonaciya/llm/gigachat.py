import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict

import httpx

from intonaciya.llm.base import Completion, Message

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_URL = "https://api.giga.chat/v1"

# Refresh the token slightly before it actually expires.
TOKEN_EXPIRY_MARGIN_S = 60


class GigaChatProvider:
    """Minimal async client for the GigaChat chat completions API.

    Never logs message contents: the provider handles private conversations.
    """

    def __init__(
        self,
        auth_key: str,
        *,
        scope: str = "GIGACHAT_API_PERS",
        model: str = "GigaChat",
        ca_bundle: str | None = None,
        timeout_s: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._auth_key = auth_key
        self._scope = scope
        self.model = model
        self._client = client or httpx.AsyncClient(
            verify=ca_bundle if ca_bundle else True, timeout=timeout_s
        )
        self._token: str | None = None
        self._token_expires_at = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, messages: Sequence[Message]) -> Completion:
        response = await self._post_completion(messages)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            self._token = None
            response = await self._post_completion(messages)
        response.raise_for_status()

        data = response.json()
        choice = data["choices"][0]
        usage = data.get("usage", {})
        return Completion(
            text=choice["message"]["content"],
            finish_reason=choice["finish_reason"],
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    async def _post_completion(self, messages: Sequence[Message]) -> httpx.Response:
        token = await self._get_token()
        return await self._client.post(
            f"{API_URL}/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": self.model, "messages": [asdict(m) for m in messages]},
        )

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - TOKEN_EXPIRY_MARGIN_S:
            return self._token

        response = await self._client.post(
            OAUTH_URL,
            headers={
                "Authorization": f"Basic {self._auth_key}",
                "RqUID": str(uuid.uuid4()),
                "Accept": "application/json",
            },
            data={"scope": self._scope},
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        # expires_at is a unix timestamp in milliseconds.
        self._token_expires_at = payload["expires_at"] / 1000
        return self._token
