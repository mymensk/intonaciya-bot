"""Resilience to a flaky network path to the Telegram Bot API."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramNetworkError

logger = logging.getLogger(__name__)


class RetryOnNetworkError(BaseRequestMiddleware):
    """Retries Bot API calls that failed on the network (timeouts, resets).

    API errors (bad request, forbidden, ...) are not retried: only transport failures.
    """

    def __init__(self, attempts: int = 3, delay_s: float = 0.5) -> None:
        self._attempts = attempts
        self._delay_s = delay_s

    async def __call__(self, make_request: Any, bot: Any, method: Any) -> Any:
        return await retry(
            lambda: make_request(bot, method),
            attempts=self._attempts,
            delay_s=self._delay_s,
            label=type(method).__name__,
        )


async def retry(
    call: Callable[[], Awaitable[Any]],
    *,
    attempts: int = 3,
    delay_s: float = 0.5,
    label: str = "request",
) -> Any:
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except TelegramNetworkError:
            if attempt == attempts:
                raise
            logger.warning("telegram_retry call=%s attempt=%s", label, attempt)
            await asyncio.sleep(delay_s * attempt)
    raise AssertionError("unreachable")
