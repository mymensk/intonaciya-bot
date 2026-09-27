import json

import httpx
import pytest

from intonaciya.config import Settings
from intonaciya.llm import Completion, GigaChatProvider, Message, RefusalKind, classify_refusal
from intonaciya.llm.factory import build_provider
from intonaciya.llm.openai_compat import OpenAICompatibleProvider

BASE_URL = "https://gateway.example/v1"


async def test_complete_posts_openai_request_and_parses_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "Привет"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider("key", base_url=BASE_URL + "/", model="m", client=client)

    completion = await provider.complete([Message("user", "Привет")])

    assert completion == Completion("Привет", "stop", 7, 2)
    assert str(requests[0].url) == f"{BASE_URL}/chat/completions"
    assert json.loads(requests[0].content) == {
        "model": "m",
        "messages": [{"role": "user", "content": "Привет"}],
    }


async def test_http_error_is_raised() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(401)))
    provider = OpenAICompatibleProvider("key", base_url=BASE_URL, model="m", client=client)

    with pytest.raises(httpx.HTTPStatusError):
        await provider.complete([Message("user", "Привет")])


def test_content_filter_is_hard_refusal() -> None:
    completion = Completion(
        text="", finish_reason="content_filter", prompt_tokens=0, completion_tokens=0
    )
    assert classify_refusal(completion) is RefusalKind.HARD


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)


def test_factory_prefers_gateway() -> None:
    settings = _settings(llm_base_url=BASE_URL, llm_api_key="k", gigachat_auth_key="g")
    provider = build_provider(settings)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == settings.llm_model


def test_factory_falls_back_to_gigachat() -> None:
    provider = build_provider(_settings(gigachat_auth_key="g"), "GigaChat-2-Pro")
    assert isinstance(provider, GigaChatProvider)
    assert provider.model == "GigaChat-2-Pro"


def test_factory_without_credentials_fails() -> None:
    with pytest.raises(RuntimeError):
        build_provider(_settings())
