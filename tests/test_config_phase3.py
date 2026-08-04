from dms.config import Settings

VALID = {"DMS_DATABASE_URL": "sqlite:///tmp/dms.db", "DMS_SHARED_TOKEN": "tok",
         "DMS_ADMIN_TOKEN": "adm", "DMS_SESSION_SECRET": "sess"}


def test_planner_defaults():
    s = Settings.from_env(VALID)
    assert s.planner_interval_seconds == 10
    # 기본값: root/admin이 특권 요청자, allow=True (미설정 시 적용).
    assert s.allow_privileged_requesters is True
    assert s.privileged_requesters == frozenset({"root", "admin"})


def test_privileged_can_be_disabled_explicitly():
    # 명시적 빈값/false로 특권을 끌 수 있다(기본값을 덮어씀).
    s = Settings.from_env({**VALID, "DMS_PRIVILEGED_REQUESTERS": ""})
    assert s.privileged_requesters == frozenset()
    s2 = Settings.from_env({**VALID, "DMS_ALLOW_PRIVILEGED_REQUESTERS": "false"})
    assert s2.allow_privileged_requesters is False


def test_privileged_settings_parsed():
    s = Settings.from_env({**VALID, "DMS_ALLOW_PRIVILEGED_REQUESTERS": "true",
                           "DMS_PRIVILEGED_REQUESTERS": " ops , backup , ",
                           "DMS_PLANNER_INTERVAL_SECONDS": "5"})
    assert s.allow_privileged_requesters is True
    assert s.privileged_requesters == frozenset({"ops", "backup"})
    assert s.planner_interval_seconds == 5


def test_allow_privileged_false_for_other_values():
    s = Settings.from_env({**VALID, "DMS_ALLOW_PRIVILEGED_REQUESTERS": "yes"})
    assert s.allow_privileged_requesters is False  # "true"/"1"만 True
