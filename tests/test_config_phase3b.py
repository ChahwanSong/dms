from dms.config import Settings

VALID = {"DMS_DATABASE_URL": "sqlite:///tmp/dms.db", "DMS_SHARED_TOKEN": "tok",
         "DMS_ADMIN_TOKEN": "adm", "DMS_SESSION_SECRET": "sess"}


def test_stepper_defaults():
    s = Settings.from_env(VALID)
    assert s.stepper_interval_seconds == 5
    assert s.preview_ttl_seconds == 86400
    assert s.artifact_base_uri == "file:///artifacts/dms"


def test_stepper_overrides():
    s = Settings.from_env({**VALID, "DMS_STEPPER_INTERVAL_SECONDS": "2",
                           "DMS_PREVIEW_TTL_SECONDS": "3600",
                           "DMS_ARTIFACT_BASE_URI": "file:///cephfs/dms/artifacts"})
    assert s.stepper_interval_seconds == 2
    assert s.preview_ttl_seconds == 3600
    assert s.artifact_base_uri == "file:///cephfs/dms/artifacts"
