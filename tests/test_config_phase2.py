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
    assert s.net_dev_path == "/proc/net/dev"
    assert s.node_name  # hostname fallback은 비어있지 않다
    s2 = AgentSettings.from_env({**env, "DMS_AGENT_NODE_NAME": "node-7",
                                 "DMS_AGENT_INTERVAL_SECONDS": "10",
                                 "DMS_AGENT_NET_DEV_PATH": "/host/proc/1/net/dev"})
    assert s2.node_name == "node-7" and s2.interval_seconds == 10
    assert s2.net_dev_path == "/host/proc/1/net/dev"


def test_virtual_net_path_defaults_to_unset_not_the_pods_own_sysfs():
    # 함정(설계 §2.6): 파드 안에도 /sys/devices/virtual/net/ 이 있고 거기엔 파드
    # 인터페이스 eth0 이 들어 있다. 이 기본값을 그 경로로 두면 마운트가 없는 배포에서
    # 호스트 물리 eth0 을 가상으로 오판해 제외한다 -- 기본은 반드시 "미설정"이다.
    env = {"DMS_AGENT_API_URL": "http://dms-api:8080", "DMS_SHARED_TOKEN": "tok"}
    s = AgentSettings.from_env(env)
    assert not s.virtual_net_path
    assert s.virtual_net_path != "/sys/devices/virtual/net"
    s2 = AgentSettings.from_env(
        {**env, "DMS_AGENT_VIRTUAL_NET_PATH": "/host/sys/devices/virtual/net"})
    assert s2.virtual_net_path == "/host/sys/devices/virtual/net"


def test_agent_settings_fail_closed():
    with pytest.raises(SettingsError) as e:
        AgentSettings.from_env({"DMS_AGENT_API_URL": "CHANGE_ME",
                                "DMS_SHARED_TOKEN": ""})
    text = str(e.value)
    assert "DMS_AGENT_API_URL" in text and "DMS_SHARED_TOKEN" in text


def test_tool_names_constant():
    assert AGENT_TOOL_NAMES == ("dscan", "dsync", "nsync", "drm")
