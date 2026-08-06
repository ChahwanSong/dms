from dms.build_runner import StubBuildRunner
from dms.controller import Loop, build_loops, run_all_once
from dms.repositories import Repositories
from dms.rollout_runner import StubRolloutRunner


def test_build_loops_names_and_intervals(db, settings):
    loops = build_loops(settings, Repositories(db))
    assert [(l.name, l.interval_seconds) for l in loops] == [
        ("planner", settings.planner_interval_seconds),
        ("job-stepper", settings.stepper_interval_seconds),
        ("storage-reconciler", settings.reconcile_interval_seconds),
        ("retention", settings.retention_interval_seconds),
        ("batch-orchestrator", settings.batch_orchestrator_interval_seconds),
        ("pod-gc", settings.pod_gc_interval_seconds)]


def test_build_loops_includes_build_watcher_when_build_runner_is_given(db, settings):
    # I4: 이 슬라이스의 심장(빌드를 실제로 돌게 만드는 배선)이 회귀 가드 없이
    # 들어갔던 자리 -- controller.py의 `if build_runner is not None` 가드가
    # 지워지거나 망가져도 641개 테스트가 전부 초록불이었다. build_runner가
    # 주어지면 루프 목록에 "build-watcher"가 실제로 등록되는지 직접 확인한다.
    loops = build_loops(settings, Repositories(db), build_runner=StubBuildRunner())
    names_and_intervals = [(l.name, l.interval_seconds) for l in loops]
    assert ("build-watcher", settings.build_watcher_interval_seconds) in names_and_intervals


def test_build_watcher_loop_is_absent_when_build_runner_is_none(db, settings):
    loops = build_loops(settings, Repositories(db))
    assert "build-watcher" not in [l.name for l in loops]


def test_build_loops_includes_rollout_watcher_when_rollout_runner_is_given(db, settings):
    loops = build_loops(settings, Repositories(db), rollout_runner=StubRolloutRunner())
    assert ("rollout-watcher", settings.rollout_interval_seconds) in [
        (l.name, l.interval_seconds) for l in loops]


def test_rollout_watcher_loop_is_absent_when_rollout_runner_is_none(db, settings):
    loops = build_loops(settings, Repositories(db))
    assert "rollout-watcher" not in [l.name for l in loops]


def test_rollout_watcher_loop_actually_applies_a_release(db, settings):
    # 배선 회귀 가드: 루프 "이름"만 보는 위 테스트는 lambda 안이 잘못돼도(예: 다른
    # repos/runner를 넘기거나 run_once를 안 부르거나) 초록불이다. 여기선 실제로
    # Pending 릴리스가 패치되어 Applying이 되는지 행동으로 고정한다 -- 이 배선이
    # 빠지면 포탈이 만든 롤아웃이 영원히 Pending에 남는다.
    repos = Repositories(db)
    rows = repos.releases.create_batch(
        items=[{"component": "dms-agent", "image": "pkg-01:5000/dms-agent:v2",
                "tag": "v2"}], actor="ops")
    runner = StubRolloutRunner()
    loops = build_loops(settings, repos, rollout_runner=runner)
    run_all_once(loops, repos, holder="h1")
    assert runner.patched == [("DaemonSet", "dms-agent", "agent",
                               "pkg-01:5000/dms-agent:v2")]
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"


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


def test_retention_loop_actually_prunes_events(db, settings):
    # 배선 회귀 가드: test_build_loops_names_and_intervals는 루프 "이름"만 확인한다 --
    # _retention_step 안에서 prune_events_once 호출이 빠지거나 오타가 나도 그 테스트는
    # 초록불이다. 여기선 실제로 오래된 이벤트가 지워지는지 행동으로 고정한다.
    repos = Repositories(db)
    db.execute("""INSERT INTO events (component, severity, event_type, at)
                  VALUES ('planner', 'error', 'x', :at)""",
              {"at": "2000-01-01T00:00:00Z"})  # 어떤 기본 retention_days보다도 오래됨
    loops = build_loops(settings, repos)
    run_all_once(loops, repos, holder="h1")
    assert db.query("SELECT id FROM events") == []


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
