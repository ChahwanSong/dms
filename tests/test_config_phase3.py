import pytest
from dms.config import Settings, SettingsError

VALID = {"DMS_DATABASE_URL": "sqlite:///tmp/dms.db", "DMS_SHARED_TOKEN": "tok",
         "DMS_ADMIN_TOKEN": "adm", "DMS_SESSION_SECRET": "sess"}


def test_planner_defaults():
    s = Settings.from_env(VALID)
    assert s.planner_interval_seconds == 10
    # 기본값: root/admin이 특권 요청자, allow=True (미설정 시 적용).
    assert s.allow_privileged_requesters is True
    assert s.privileged_requesters == frozenset({"root", "admin"})


def test_privileged_list_can_be_emptied_but_the_switch_cannot_be_turned_off():
    # 명시적 빈값으로 특권 목록을 비울 수 있다(기본값을 덮어씀 -- 맵핑 면제자가 없어진다). 허용 스위치를 끄는 것은 dms-k8s
    # 에서 기동 거부다(모든 잡이 root -- 끄면 아무것도 돌지 않는다, config).
    s = Settings.from_env({**VALID, "DMS_PRIVILEGED_REQUESTERS": ""})
    assert s.privileged_requesters == frozenset()
    with pytest.raises(SettingsError) as e:
        Settings.from_env({**VALID, "DMS_ALLOW_PRIVILEGED_REQUESTERS": "false"})
    assert any("must not be false" in p for p in e.value.problems)


def test_privileged_settings_parsed():
    s = Settings.from_env({**VALID, "DMS_ALLOW_PRIVILEGED_REQUESTERS": "true",
                           "DMS_PRIVILEGED_REQUESTERS": " ops , backup , ",
                           "DMS_PLANNER_INTERVAL_SECONDS": "5"})
    assert s.allow_privileged_requesters is True
    assert s.privileged_requesters == frozenset({"ops", "backup"})
    assert s.planner_interval_seconds == 5


def test_allow_privileged_other_values_mean_off_and_refuse_to_start():
    # "true"/"1"만 True -- 그 밖의 값은 꺼짐이라 기동 거부(오타가 조용히 모든 제출 403 이 되지 않게).
    with pytest.raises(SettingsError):
        Settings.from_env({**VALID, "DMS_ALLOW_PRIVILEGED_REQUESTERS": "yes"})
