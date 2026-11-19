from dms.config import Settings

VALID = {"DMS_DATABASE_URL": "sqlite:///tmp/dms.db", "DMS_SHARED_TOKEN": "tok",
         "DMS_ADMIN_TOKEN": "adm", "DMS_SESSION_SECRET": "sess"}


def test_batch_interval_default():
    s = Settings.from_env(VALID)
    assert s.batch_orchestrator_interval_seconds == 5


def test_batch_interval_override():
    s = Settings.from_env({**VALID, "DMS_BATCH_ORCHESTRATOR_INTERVAL_SECONDS": "9"})
    assert s.batch_orchestrator_interval_seconds == 9


# 배치 스케줄 회차 보존 수(2026-11-16 사용자 결정 「배치 회차가 계속 누적되는걸 원하지 않아 최근 10회차만」 -- batch_round_prune.py).
# 1–100(상한은 회차 경계 행 보존 상한 BATCH_ROUND_HISTORY_CAP 200 안에서 경계를 계산할 수 있어야 해서), 밖이면 기동 거부.
def test_batch_schedule_keep_rounds_default_is_ten():
    assert Settings.from_env(VALID).batch_schedule_keep_rounds == 10
    assert Settings(database_url="x", shared_token="t", admin_token="a",
                    session_secret="s").batch_schedule_keep_rounds == 10


def test_batch_schedule_keep_rounds_bounds():
    import pytest
    from dms.config import SettingsError
    for ok in ("1", "100", "7"):
        assert Settings.from_env({**VALID, "DMS_BATCH_SCHEDULE_KEEP_ROUNDS": ok}).batch_schedule_keep_rounds == int(ok)
    for bad in ("0", "101", "-1", "x", "5.5"):
        with pytest.raises(SettingsError) as e:
            Settings.from_env({**VALID, "DMS_BATCH_SCHEDULE_KEEP_ROUNDS": bad})
        assert "DMS_BATCH_SCHEDULE_KEEP_ROUNDS" in str(e.value)


def test_batch_schedule_keep_rounds_in_the_shared_base_never_differs_from_the_code_default():
    # deploy/k8s 는 dms-k8s 라인과 바이트 동일을 목표로 하는 공용 base 다(CLAUDE.md 브랜치 모델) -- 이 라인의 기본 10 은 Settings
    # 에 두고 base 20-config 에는 싣지 않는다. dms-k8s 의 base 는 자기 라인 값을 싣는다(9166b8e2 "5" → 0b5e7357 "10"): 공용 base 를
    # 맞추려고 그 20-config 를 그대로 가져와도 되지만, 그 값이 이 라인의 기본과 다르면 보존 수가 조용히 바뀐다(ConfigMap 이 코드
    # 기본보다 우선) -- 키가 없거나, 있으면 코드 기본과 같아야 한다. 여기가 빨개지면 그 줄을 빼거나 오버레이에서 덮는다.
    import re
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "deploy/k8s/20-config.yaml").read_text()
    values = re.findall(r'^\s*DMS_BATCH_SCHEDULE_KEEP_ROUNDS:\s*"?([^"\s#]*)"?', text, re.M)
    assert all(v == str(Settings.batch_schedule_keep_rounds) for v in values), values
    assert ("DMS_BATCH_SCHEDULE_KEEP_ROUNDS" in text) == bool(values)     # 다른 모양으로 실린 키를 놓치지 않는다


# 배치 단위 삭제 상한·동기 문턱(2026-11-19 사용자 결정 「배치 단위 삭제 상한 기본 50000개로」 -- dms-k8s 14ee820c 이식).
def test_batch_delete_limits_default_and_bounds():
    import pytest
    from dms.config import SettingsError
    s = Settings.from_env(VALID)
    assert (s.batch_delete_max_children, s.batch_delete_sync_max_children) == (50000, 500)
    assert (Settings.batch_delete_max_children, Settings.batch_delete_sync_max_children) == (50000, 500)
    for key, ok, attr in (("DMS_BATCH_DELETE_MAX_CHILDREN", "1", "batch_delete_max_children"),
                          ("DMS_BATCH_DELETE_MAX_CHILDREN", "50000", "batch_delete_max_children"),
                          ("DMS_BATCH_DELETE_SYNC_MAX_CHILDREN", "0", "batch_delete_sync_max_children"),
                          ("DMS_BATCH_DELETE_SYNC_MAX_CHILDREN", "1000", "batch_delete_sync_max_children")):
        assert getattr(Settings.from_env({**VALID, key: ok}), attr) == int(ok)
    for key, bad in (("DMS_BATCH_DELETE_MAX_CHILDREN", "0"), ("DMS_BATCH_DELETE_MAX_CHILDREN", "50001"),
                     ("DMS_BATCH_DELETE_MAX_CHILDREN", "x"), ("DMS_BATCH_DELETE_SYNC_MAX_CHILDREN", "-1"),
                     ("DMS_BATCH_DELETE_SYNC_MAX_CHILDREN", "1001"), ("DMS_BATCH_DELETE_SYNC_MAX_CHILDREN", "5.5")):
        with pytest.raises(SettingsError) as e:
            Settings.from_env({**VALID, key: bad})
        assert key in str(e.value)


def test_batch_delete_keys_in_the_shared_base_never_differ_from_the_code_defaults():
    # 위 KEEP_ROUNDS 그물과 같은 판단: base 20-config 는 dms-k8s 와 바이트 동일을 목표로 하는 공용 base 라 dms-k8s 가 실은 두 키
    # (14ee820c -- "50000"·"500")를 그대로 옮겼다. 그 값이 이 라인의 코드 기본과 다르면 ConfigMap 이 코드 기본을 조용히 덮는다 --
    # 키가 없거나, 있으면 코드 기본과 같아야 한다(여기가 빨개지면 그 줄을 빼거나 오버레이에서 덮는다).
    import re
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "deploy/k8s/20-config.yaml").read_text()
    for key, attr in (("DMS_BATCH_DELETE_MAX_CHILDREN", "batch_delete_max_children"),
                      ("DMS_BATCH_DELETE_SYNC_MAX_CHILDREN", "batch_delete_sync_max_children")):
        values = re.findall(rf'^\s*{key}:\s*"?([^"\s#]*)"?', text, re.M)
        assert all(v == str(getattr(Settings, attr)) for v in values), (key, values)
        assert (re.search(rf"\b{key}\b", text) is not None) == bool(values), key   # 다른 모양으로 실린 키를 놓치지 않는다

