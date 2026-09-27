import asyncio
import logging
import shutil

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession

from intonaciya.bot import texts
from intonaciya.bot.handlers import AllowlistMiddleware, router
from intonaciya.bot.network import RetryOnNetworkError
from intonaciya.config import Settings
from intonaciya.llm import LLMProvider
from intonaciya.llm.budget import crossed_threshold, fetch_key_budget
from intonaciya.llm.factory import build_provider
from intonaciya.llm.stub import StubProvider
from intonaciya.screenshots import ScreenshotReader, TesseractReader
from intonaciya.sessions import SessionStore
from intonaciya.vision import FallbackReader, VisionReader

logger = logging.getLogger(__name__)

BUDGET_THRESHOLDS = (0.5, 0.8, 0.95)


def build_llm(settings: Settings) -> LLMProvider:
    try:
        provider = build_provider(settings)
    except RuntimeError:
        logger.warning("No LLM credentials configured, using the stub LLM")
        return StubProvider()
    logger.info("LLM backend %s, model %s", type(provider).__name__, provider.model)
    return provider


def build_screenshot_reader(settings: Settings) -> ScreenshotReader:
    tesseract = TesseractReader()
    if not (settings.llm_api_key and settings.llm_base_url and settings.vision_model):
        logger.info("Screenshots: local Tesseract")
        return tesseract
    logger.info("Screenshots: %s with Tesseract fallback", settings.vision_model)
    vision = VisionReader(
        settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        model=settings.vision_model,
    )
    return FallbackReader(vision, tesseract)


async def watch_budget(bot: Bot, settings: Settings) -> None:
    """Alert the admin once per threshold as the gateway key budget gets used up."""
    if not (settings.admin_user_id and settings.llm_api_key and settings.llm_base_url):
        return
    alerted: set[float] = set()
    while True:
        try:
            budget = await fetch_key_budget(
                settings.llm_api_key.get_secret_value(), settings.llm_base_url
            )
            logger.info("llm_budget spend=%.2f max=%s", budget.spend, budget.max_budget)
            threshold = crossed_threshold(budget, BUDGET_THRESHOLDS, alerted)
            if threshold is not None:
                alerted.update(t for t in BUDGET_THRESHOLDS if t <= threshold)
                await bot.send_message(
                    settings.admin_user_id,
                    texts.BUDGET_ALERT.format(
                        share=budget.used_share,
                        spend=budget.spend,
                        max_budget=budget.max_budget,
                        remaining=budget.remaining,
                    ),
                )
        except Exception as exc:
            logger.warning("llm_budget_check_failed error=%s", type(exc).__name__)
        await asyncio.sleep(settings.budget_check_interval_s)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    if settings.telegram_bot_token is None:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")

    # A short timeout plus retries rides out a flaky path to the Bot API.
    session = AiohttpSession(timeout=settings.telegram_timeout_s)
    session.middleware(RetryOnNetworkError())
    bot = Bot(settings.telegram_bot_token.get_secret_value(), session=session)
    if shutil.which("tesseract") is None:
        logger.warning("tesseract is not installed, screenshots will fail")
    dp = Dispatcher(
        sessions=SessionStore(),
        llm=build_llm(settings),
        screenshots=build_screenshot_reader(settings),
    )
    allowlist = AllowlistMiddleware(
        settings.allowed_user_id_set, open_to_everyone=settings.is_open_to_everyone
    )
    dp.message.outer_middleware(allowlist)
    dp.callback_query.outer_middleware(allowlist)
    dp.include_router(router)

    budget_task = asyncio.create_task(watch_budget(bot, settings))
    try:
        await dp.start_polling(bot)
    finally:
        budget_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
