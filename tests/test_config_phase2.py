import pytest
from dms.config import AGENT_TOOL_NAMES, AgentSettings, Settings, SettingsError

VALID = {
    "DMS_DATABASE_URL": "sqlite:///tmp/dms.db",
    "DMS_SHARED_TOKEN": "tok",
    "DMS_ADMIN_TOKEN": "adm",
    "DMS_SESSION_SECRET": "sess",
}


def test_server_phase2_defaults_and_overrides():
    s = Settings.from_env(VALID)
    assert s.agent_report_stale_seconds == 300
    assert s.agent_report_interval_seconds == 60
    assert s.reconcile_interval_seconds == 30
    assert s.retention_interval_seconds == 3600
    assert s.agent_report_retention_days == 30
    assert s.identity_probe_ttl_seconds == 3600
    s2 = Settings.from_env({**VALID, "DMS_RECONCILE_INTERVAL_SECONDS": "5"})
    assert s2.reconcile_interval_seconds == 5
    with pytest.raises(SettingsError) as e:
        Settings.from_env({**VALID, "DMS_AGENT_REPORT_STALE_SECONDS": "soon"})
    assert "DMS_AGENT_REPORT_STALE_SECONDS" in str(e.value)


def test_agent_settings_required_and_defaults(monkeypatch):
    env = {"DMS_AGENT_API_URL": "http://dms-api:8080", "DMS_SHARED_TOKEN": "tok"}
    s = AgentSettings.from_env(env)
    assert s.api_url == "http://dms-api:8080"
    assert s.interval_seconds == 60
    assert s.mountinfo_path == "/proc/1/mountinfo"
    assert s.node_name  # hostname fallback은 비어있지 않다
    s2 = AgentSettings.from_env({**env, "DMS_AGENT_NODE_NAME": "node-7",
                                 "DMS_AGENT_INTERVAL_SECONDS": "10"})
    assert s2.node_name == "node-7" and s2.interval_seconds == 10


def test_agent_settings_fail_closed():
    with pytest.raises(SettingsError) as e:
        AgentSettings.from_env({"DMS_AGENT_API_URL": "CHANGE_ME",
                                "DMS_SHARED_TOKEN": ""})
    text = str(e.value)
    assert "DMS_AGENT_API_URL" in text and "DMS_SHARED_TOKEN" in text


def test_tool_names_constant():
    assert AGENT_TOOL_NAMES == ("dscan", "dsync", "nsync", "drm")
