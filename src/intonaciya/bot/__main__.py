import asyncio
import logging
import shutil

from aiogram import Bot, Dispatcher

from intonaciya.bot.handlers import AllowlistMiddleware, router
from intonaciya.config import Settings
from intonaciya.llm import LLMProvider
from intonaciya.llm.factory import build_provider
from intonaciya.llm.stub import StubProvider
from intonaciya.screenshots import ScreenshotReader, TesseractReader
from intonaciya.sessions import SessionStore
from intonaciya.vision import FallbackReader, VisionReader

logger = logging.getLogger(__name__)


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


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    if settings.telegram_bot_token is None:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")

    bot = Bot(settings.telegram_bot_token.get_secret_value())
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

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
