import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_read_prefixed_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "production")
    monkeypatch.setenv("APP_SERVICE_NAME", "from-env")

    settings = Settings(_env_file=None)

    assert settings.environment == "production"
    assert settings.service_name == "from-env"


def test_settings_reject_unknown_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENVIRONMENT", "staging-typo")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
