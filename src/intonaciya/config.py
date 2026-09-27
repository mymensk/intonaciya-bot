from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: SecretStr | None = None
    # Comma-separated Telegram user IDs allowed to use the bot, or "*" for everyone.
    # Empty keeps the bot closed: it only tells users their ID.
    allowed_user_ids: str = ""
    # Telegram ID that receives service alerts (e.g. LLM budget running low).
    admin_user_id: int | None = None
    budget_check_interval_s: int = 3600

    # OpenAI-compatible LLM gateway. Takes precedence over direct GigaChat access.
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str = "gigachat3.5-432b-a28b"
    # Screenshots are read by this multimodal model through the gateway ("" to use
    # local Tesseract only). Tesseract stays as a fallback either way.
    vision_model: str = "qwen3-vl-8b-instruct"

    # Direct GigaChat API access (OAuth with an Authorization Key).
    # Without any LLM credentials the bot falls back to a stub that echoes the prompt.
    gigachat_auth_key: SecretStr | None = None
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-2"
    gigachat_ca_bundle: str | None = None

    @field_validator(
        "telegram_bot_token", "llm_api_key", "gigachat_auth_key", "admin_user_id", mode="before"
    )
    @classmethod
    def _empty_secret_is_none(cls, value: object) -> object:
        return value or None

    @property
    def default_model(self) -> str:
        return self.llm_model if self.llm_api_key else self.gigachat_model

    @property
    def is_open_to_everyone(self) -> bool:
        return self.allowed_user_ids.strip() == "*"

    @property
    def allowed_user_id_set(self) -> frozenset[int]:
        if self.is_open_to_everyone:
            return frozenset()
        return frozenset(int(part) for part in self.allowed_user_ids.split(",") if part.strip())
