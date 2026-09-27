from intonaciya.config import Settings
from intonaciya.llm.gigachat import GigaChatProvider
from intonaciya.llm.openai_compat import OpenAICompatibleProvider


def build_provider(
    settings: Settings, model: str | None = None
) -> OpenAICompatibleProvider | GigaChatProvider:
    """Pick the LLM backend from settings: the gateway if configured, else direct GigaChat."""
    model = model or settings.default_model
    if settings.llm_api_key and settings.llm_base_url:
        return OpenAICompatibleProvider(
            settings.llm_api_key.get_secret_value(), base_url=settings.llm_base_url, model=model
        )
    if settings.gigachat_auth_key:
        return GigaChatProvider(
            settings.gigachat_auth_key.get_secret_value(),
            scope=settings.gigachat_scope,
            model=model,
            ca_bundle=settings.gigachat_ca_bundle,
        )
    raise RuntimeError("No LLM credentials: set LLM_BASE_URL and LLM_API_KEY, or GIGACHAT_AUTH_KEY")
