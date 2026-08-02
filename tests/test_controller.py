from dms.controller import Loop, build_loops, run_all_once
from dms.repositories import Repositories


def test_build_loops_names_and_intervals(db, settings):
    loops = build_loops(settings, Repositories(db))
    assert [(l.name, l.interval_seconds) for l in loops] == [
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
