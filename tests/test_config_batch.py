from dms.config import Settings

VALID = {"DMS_DATABASE_URL": "sqlite:///tmp/dms.db", "DMS_SHARED_TOKEN": "tok",
         "DMS_ADMIN_TOKEN": "adm", "DMS_SESSION_SECRET": "sess"}


def test_batch_interval_default():
    s = Settings.from_env(VALID)
    assert s.batch_orchestrator_interval_seconds == 5


def test_batch_interval_override():
    s = Settings.from_env({**VALID, "DMS_BATCH_ORCHESTRATOR_INTERVAL_SECONDS": "9"})
    assert s.batch_orchestrator_interval_seconds == 9
