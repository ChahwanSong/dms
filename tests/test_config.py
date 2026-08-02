import pytest
from dms.config import Settings, SettingsError

VALID = {
    "DMS_DATABASE_URL": "sqlite:///tmp/dms.db",
    "DMS_SHARED_TOKEN": "tok-abc",
    "DMS_ADMIN_TOKEN": "adm-xyz",
    "DMS_SESSION_SECRET": "sess-123",
}


def test_valid_env():
    s = Settings.from_env(VALID)
    assert s.database_url == "sqlite:///tmp/dms.db"
    assert s.api_port == 8080


def test_missing_and_placeholder_collected():
    env = dict(VALID)
    env.pop("DMS_DATABASE_URL")
    env["DMS_SHARED_TOKEN"] = "CHANGE_ME"
    env["DMS_ADMIN_TOKEN"] = "REPLACE_WITH_TOKEN"
    with pytest.raises(SettingsError) as e:
        Settings.from_env(env)
    text = str(e.value)
    assert "DMS_DATABASE_URL" in text
    assert "DMS_SHARED_TOKEN" in text
    assert "DMS_ADMIN_TOKEN" in text


def test_port_parsing():
    s = Settings.from_env({**VALID, "DMS_API_PORT": "9000"})
    assert s.api_port == 9000
    with pytest.raises(SettingsError):
        Settings.from_env({**VALID, "DMS_API_PORT": "not-a-number"})
