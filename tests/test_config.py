import pytest

from intonaciya.config import Settings


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)


def test_empty_allowlist_is_closed() -> None:
    settings = _settings(allowed_user_ids="")
    assert not settings.is_open_to_everyone
    assert settings.allowed_user_id_set == frozenset()


def test_allowlist_parses_ids() -> None:
    assert _settings(allowed_user_ids="1, 2,,3").allowed_user_id_set == {1, 2, 3}


def test_star_opens_bot() -> None:
    assert _settings(allowed_user_ids="*").is_open_to_everyone


@pytest.mark.parametrize("field", ["telegram_bot_token", "gigachat_auth_key"])
def test_empty_secret_is_none(field: str) -> None:
    assert getattr(_settings(**{field: ""}), field) is None
