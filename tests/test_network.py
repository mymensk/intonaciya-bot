import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import GetMe

from intonaciya.bot.network import RetryOnNetworkError, retry


class Flaky:
    def __init__(self, failures: int, error: Exception) -> None:
        self.failures = failures
        self.error = error
        self.calls = 0

    async def __call__(self, *args: object) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return "ok"


def _network_error() -> TelegramNetworkError:
    return TelegramNetworkError(method=GetMe(), message="timeout")


async def test_retry_recovers_after_network_errors() -> None:
    flaky = Flaky(2, _network_error())
    assert await retry(flaky, attempts=3, delay_s=0) == "ok"
    assert flaky.calls == 3


async def test_retry_gives_up_after_attempts() -> None:
    flaky = Flaky(5, _network_error())
    with pytest.raises(TelegramNetworkError):
        await retry(flaky, attempts=3, delay_s=0)
    assert flaky.calls == 3


async def test_api_errors_are_not_retried() -> None:
    flaky = Flaky(1, TelegramBadRequest(method=GetMe(), message="bad"))
    with pytest.raises(TelegramBadRequest):
        await retry(flaky, attempts=3, delay_s=0)
    assert flaky.calls == 1


async def test_request_middleware_retries() -> None:
    flaky = Flaky(1, _network_error())
    middleware = RetryOnNetworkError(attempts=2, delay_s=0)
    assert await middleware(flaky, object(), GetMe()) == "ok"
