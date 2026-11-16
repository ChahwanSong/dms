from dms.config import Settings

VALID = {"DMS_DATABASE_URL": "sqlite:///tmp/dms.db", "DMS_SHARED_TOKEN": "tok",
         "DMS_ADMIN_TOKEN": "adm", "DMS_SESSION_SECRET": "sess"}


def test_batch_interval_default():
    s = Settings.from_env(VALID)
    assert s.batch_orchestrator_interval_seconds == 5


def test_batch_interval_override():
    s = Settings.from_env({**VALID, "DMS_BATCH_ORCHESTRATOR_INTERVAL_SECONDS": "9"})
    assert s.batch_orchestrator_interval_seconds == 9


# 배치 스케줄 회차 보존 수(2026-11-16 사용자 결정 「최근 5회차만 남기고 정리」 -- batch_round_prune.py). 1–100(상한은 회차 경계
# 행 보존 상한 BATCH_ROUND_HISTORY_CAP 200 안에서 경계를 계산할 수 있어야 해서), 밖이면 기동 거부.
def test_batch_schedule_keep_rounds_default_is_five():
    assert Settings.from_env(VALID).batch_schedule_keep_rounds == 5
    assert Settings(database_url="x", shared_token="t", admin_token="a",
                    session_secret="s").batch_schedule_keep_rounds == 5


def test_batch_schedule_keep_rounds_bounds():
    import pytest
    from dms.config import SettingsError
    for ok in ("1", "100", "7"):
        assert Settings.from_env({**VALID, "DMS_BATCH_SCHEDULE_KEEP_ROUNDS": ok}).batch_schedule_keep_rounds == int(ok)
    for bad in ("0", "101", "-1", "x", "5.5"):
        with pytest.raises(SettingsError) as e:
            Settings.from_env({**VALID, "DMS_BATCH_SCHEDULE_KEEP_ROUNDS": bad})
        assert "DMS_BATCH_SCHEDULE_KEEP_ROUNDS" in str(e.value)


def test_batch_schedule_keep_rounds_is_in_the_site_neutral_config():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "deploy/k8s/20-config.yaml").read_text()
    assert 'DMS_BATCH_SCHEDULE_KEEP_ROUNDS: "5"' in text
