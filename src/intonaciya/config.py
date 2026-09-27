from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: SecretStr | None = None

    # OpenAI-compatible LLM gateway. Takes precedence over direct GigaChat access.
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str = "gigachat3.5-432b-a28b"

    # Direct GigaChat API access (OAuth with an Authorization Key).
    gigachat_auth_key: SecretStr | None = None
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-2"
    gigachat_ca_bundle: str | None = None

    @field_validator("telegram_bot_token", "llm_api_key", "gigachat_auth_key", mode="before")
    @classmethod
    def _empty_secret_is_none(cls, value: object) -> object:
        return value or None

    @property
    def default_model(self) -> str:
        return self.llm_model if self.llm_api_key else self.gigachat_model
