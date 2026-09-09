import pytest

from nlm_telegram_bot.runtime import Settings


def test_settings_require_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "ALLOWED_USER_IDS",
        "WEBHOOK_PUBLIC_BASE_URL",
        "WEBHOOK_PATH",
        "WEBHOOK_SECRET_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN is required"):
        Settings.from_env()


def test_settings_parse_allowed_user_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("ALLOWED_USER_IDS", "123, 456,123")
    monkeypatch.setenv("WEBHOOK_PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("WEBHOOK_PATH", "/telegram")
    monkeypatch.setenv("WEBHOOK_SECRET_TOKEN", "secret")

    settings = Settings.from_env()

    assert settings.allowed_user_ids == frozenset({123, 456})


@pytest.mark.parametrize("value", ["", " ", "123,not-an-id"])
def test_settings_reject_invalid_allowed_user_ids(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("ALLOWED_USER_IDS", value)
    monkeypatch.setenv("WEBHOOK_PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("WEBHOOK_PATH", "/telegram")
    monkeypatch.setenv("WEBHOOK_SECRET_TOKEN", "secret")

    with pytest.raises(RuntimeError, match="ALLOWED_USER_IDS"):
        Settings.from_env()
