"""env 기반 설정. 기동 시 전부 검증하고, placeholder가 통과하는 구멍을 만들지 않는다."""
from dataclasses import dataclass
from typing import Mapping


class SettingsError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


def _is_placeholder(value: str | None) -> bool:
    return not value or value == "CHANGE_ME" or value.startswith("REPLACE_WITH_")


@dataclass(frozen=True)
class Settings:
    database_url: str
    shared_token: str
    admin_token: str
    session_secret: str
    api_host: str = "0.0.0.0"
    api_port: int = 8080

    @classmethod
    def from_env(cls, environ: Mapping) -> "Settings":
        problems: list[str] = []
        required = ("DMS_DATABASE_URL", "DMS_SHARED_TOKEN",
                    "DMS_ADMIN_TOKEN", "DMS_SESSION_SECRET")
        values: dict = {}
        for key in required:
            value = environ.get(key)
            if _is_placeholder(value):
                problems.append(f"{key} is missing or a placeholder")
            values[key] = value
        port_raw = environ.get("DMS_API_PORT", "8080")
        try:
            port = int(port_raw)
        except ValueError:
            problems.append(f"DMS_API_PORT is not an integer: {port_raw!r}")
            port = 0
        if problems:
            raise SettingsError(problems)
        return cls(
            database_url=values["DMS_DATABASE_URL"],
            shared_token=values["DMS_SHARED_TOKEN"],
            admin_token=values["DMS_ADMIN_TOKEN"],
            session_secret=values["DMS_SESSION_SECRET"],
            api_host=environ.get("DMS_API_HOST", "0.0.0.0"),
            api_port=port,
        )
