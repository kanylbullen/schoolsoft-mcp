import pytest

from schoolsoft_mcp.config import ConfigError, Settings


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key in (
        "SCHOOLSOFT_SCHOOL",
        "SCHOOLSOFT_USERNAME",
        "SCHOOLSOFT_PASSWORD",
        "SCHOOLSOFT_USERTYPE",
        "SCHOOLSOFT_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    for k, v in values.items():
        monkeypatch.setenv(k, v)


def test_loads_minimal_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(
        monkeypatch,
        SCHOOLSOFT_SCHOOL="yourschool",
        SCHOOLSOFT_USERNAME="alice",
        SCHOOLSOFT_PASSWORD="secret",
    )
    s = Settings.from_env()
    assert s.school == "yourschool"
    assert s.username == "alice"
    assert s.password == "secret"
    assert s.usertype == 2
    assert s.base_url == "https://sms.schoolsoft.se"


def test_missing_fields_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, SCHOOLSOFT_SCHOOL="yourschool")
    with pytest.raises(ConfigError) as exc:
        Settings.from_env()
    assert "SCHOOLSOFT_USERNAME" in str(exc.value)
    assert "SCHOOLSOFT_PASSWORD" in str(exc.value)


def test_invalid_usertype(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(
        monkeypatch,
        SCHOOLSOFT_SCHOOL="yourschool",
        SCHOOLSOFT_USERNAME="alice",
        SCHOOLSOFT_PASSWORD="secret",
        SCHOOLSOFT_USERTYPE="9",
    )
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_base_url_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(
        monkeypatch,
        SCHOOLSOFT_SCHOOL="yourschool",
        SCHOOLSOFT_USERNAME="alice",
        SCHOOLSOFT_PASSWORD="secret",
        SCHOOLSOFT_BASE_URL="https://example.com/",
    )
    assert Settings.from_env().base_url == "https://example.com"
