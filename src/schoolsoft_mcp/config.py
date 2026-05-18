"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    school: str
    username: str
    password: str
    usertype: int = 2
    base_url: str = "https://sms.schoolsoft.se"
    request_timeout: float = 20.0

    @classmethod
    def from_env(cls) -> Settings:
        school = os.environ.get("SCHOOLSOFT_SCHOOL", "").strip()
        username = os.environ.get("SCHOOLSOFT_USERNAME", "").strip()
        password = os.environ.get("SCHOOLSOFT_PASSWORD", "")
        usertype_raw = os.environ.get("SCHOOLSOFT_USERTYPE", "2").strip()
        base_url = os.environ.get("SCHOOLSOFT_BASE_URL", "https://sms.schoolsoft.se").rstrip("/")

        missing = [
            name
            for name, value in (
                ("SCHOOLSOFT_SCHOOL", school),
                ("SCHOOLSOFT_USERNAME", username),
                ("SCHOOLSOFT_PASSWORD", password),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing required environment variables: " + ", ".join(missing)
            )

        try:
            usertype = int(usertype_raw)
        except ValueError as err:
            raise ConfigError(
                f"SCHOOLSOFT_USERTYPE must be an integer, got {usertype_raw!r}"
            ) from err
        if usertype not in (1, 2, 3):
            raise ConfigError(
                f"SCHOOLSOFT_USERTYPE must be 1 (student), 2 (parent), or 3 (staff); got {usertype}"
            )

        return cls(
            school=school,
            username=username,
            password=password,
            usertype=usertype,
            base_url=base_url,
        )
