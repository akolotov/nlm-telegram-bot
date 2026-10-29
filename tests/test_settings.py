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


@pytest.fixture(autouse=True)
def configure_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-secret")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)


@pytest.fixture
def required_environment(monkeypatch):
    for name, value in {
        "TELEGRAM_BOT_TOKEN": "token",
        "ALLOWED_USER_IDS": "123",
        "WEBHOOK_PUBLIC_BASE_URL": "https://example.test",
        "WEBHOOK_PATH": "/telegram",
        "WEBHOOK_SECRET_TOKEN": "secret",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_settings_require_gemini_key(monkeypatch, required_environment, key):
    if key is None:
        monkeypatch.delenv("GEMINI_API_KEY")
    else:
        monkeypatch.setenv("GEMINI_API_KEY", key)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is required"):
        Settings.from_env()


@pytest.mark.parametrize("model", [None, "", "  ", " custom-model "])
def test_settings_select_gemini_model(monkeypatch, required_environment, model):
    if model is not None:
        monkeypatch.setenv("GEMINI_MODEL", model)
    settings = Settings.from_env()
    expected = "custom-model" if model and model.strip() else "gemini-3.8-flash"
    assert settings.gemini_model == expected
    assert settings.gemini_api_key == "test-gemini-secret"
    assert "test-gemini-secret" not in repr(settings)
