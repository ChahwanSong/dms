from dms.controller import Loop, build_loops, run_all_once
from dms.repositories import Repositories


def test_build_loops_names_and_intervals(db, settings):
    loops = build_loops(settings, Repositories(db))
    assert [(l.name, l.interval_seconds) for l in loops] == [
        ("planner", settings.planner_interval_seconds),
        ("job-stepper", settings.stepper_interval_seconds),
        ("storage-reconciler", settings.reconcile_interval_seconds),
        ("retention", settings.retention_interval_seconds)]


def test_run_all_once_runs_and_isolates_errors(db, capsys):
    repos = Repositories(db)
    calls = []

    def ok():
        calls.append("ok")

    def boom():
        raise RuntimeError("loop crashed")

    loops = [Loop("good", 30, ok), Loop("bad", 30, boom)]
    result = run_all_once(loops, repos, holder="h1")
    assert result == {"good": "ok", "bad": "error:RuntimeError"}
    assert calls == ["ok"]
    assert "loop crashed" in capsys.readouterr().err


def test_run_all_once_respects_leases(db):
    repos = Repositories(db)
    loops = [Loop("solo", 30, lambda: None)]
    assert run_all_once(loops, repos, holder="h1") == {"solo": "ok"}
    # 다른 holder는 리스 만료 전 skip
    assert run_all_once(loops, repos, holder="h2") == {"solo": "skipped_lease"}
    # 같은 holder는 갱신되어 계속 실행
    assert run_all_once(loops, repos, holder="h1") == {"solo": "ok"}


def test_reconciler_loop_wired_end_to_end(db, settings):
    repos = Repositories(db)
    repos.storages.create(storage_name="s1", mount_path="/mnt/s",
                          managed_root="/mnt/s/dms", backend_type="cephfs",
                          actor="admin")
    loops = build_loops(settings, repos)
    run_all_once(loops, repos, holder="h1")
    assert repos.storages.get("s1")["status"] == "Unknown"


def test_run_forever_ticks_and_stops(db, settings):
    from dms.controller import run_forever
    repos = Repositories(db)
    ticks = []

    def fake_sleep(seconds):
        ticks.append(seconds)
        if len(ticks) >= 3:
            raise KeyboardInterrupt  # 테스트 종료 장치

    try:
        run_forever(settings, repos, holder="h1", sleep=fake_sleep)
    except KeyboardInterrupt:
        pass
    assert ticks == [1, 1, 1]
    # 첫 틱에서 루프들이 실제 실행됐는지 (리스가 잡혀 있음)
    assert db.query_one("SELECT holder FROM component_leases WHERE component = 'loop:storage-reconciler'")["holder"] == "h1"
