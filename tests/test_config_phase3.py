from dms.config import Settings

VALID = {"DMS_DATABASE_URL": "sqlite:///tmp/dms.db", "DMS_SHARED_TOKEN": "tok",
         "DMS_ADMIN_TOKEN": "adm", "DMS_SESSION_SECRET": "sess"}


def test_planner_defaults():
    s = Settings.from_env(VALID)
    assert s.planner_interval_seconds == 10
    assert s.allow_privileged_requesters is False
    assert s.privileged_requesters == frozenset()


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
