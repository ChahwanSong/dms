import dataclasses

from dms.controller import build_loops, run_all_once
from dms.db import utc_now_iso
from dms.repositories import Repositories


def test_batch_orchestrator_loop_registered(db, settings):
    names = [l.name for l in build_loops(settings, Repositories(db))]
    assert "batch-orchestrator" in names


def test_batch_scheduler_loop_runs_right_before_the_orchestrator(db, settings):
    """배치 스케줄러(2026-11-16): 오케스트레이터 **바로 앞**, 같은 간격 -- 같은 패스에서 회차가 되살린 배치를 오케스트레이터가
    바로 집는다. DB 전용이라 runner 없이도 들어간다."""
    loops = build_loops(settings, Repositories(db))
    names = [l.name for l in loops]
    assert names.index("batch-scheduler") == names.index("batch-orchestrator") - 1
    sched = loops[names.index("batch-scheduler")]
    assert sched.interval_seconds == settings.batch_orchestrator_interval_seconds


def test_batch_scheduler_loop_tick_actually_fires_a_due_schedule(db, settings):
    """이름만 보는 위 테스트는 람다 안이 잘못돼도 초록이다(artifact-base-check 의 같은 교훈) -- 한 틱이 due 스케줄을 실제로
    쏘는지(배치 Running·회차 이력 fired) 행동으로 고정한다."""
    settings = dataclasses.replace(settings, allow_privileged_requesters=True, privileged_requesters=frozenset({"ops"}))
    repos = Repositories(db)
    repos.accounts.create("ops", "p", "admin", actor="t")
    repos.storages.create(storage_name="s1", mount_path="/mnt/s1", managed_root="/mnt/s1/m", backend_type="nfs",
                          actor="t")
    repos.storages.set_status("s1", "Ready")
    bid = repos.batches.create(operation="scan", requester_id="ops", actor="ops", max_concurrency=1, options={},
                               items=[{"storage": "s1", "target": "a"}], note=None, status="Completed",
                               auth_method="session")
    repos.batches.set_item_status(bid, 0, "Succeeded")
    sid = repos.batch_schedules.create(batch_id=bid, kind="daily", spec={"hour": 2, "minute": 0}, enabled=True,
                                       next_run_at="2000-01-01T00:00:00Z", actor="ops", arm=lambda b, i: None)
    # 예정 시각이 아주 옛날이라 그대로면 놓침(missed) -- 지금 시각으로 당겨 유예 안에서 판정되게 한다.
    repos.db.execute("UPDATE batch_schedules SET next_run_at = :n WHERE schedule_id = :s", {"n": utc_now_iso(), "s": sid})
    loops = build_loops(settings, repos)
    run_all_once([l for l in loops if l.name == "batch-scheduler"], repos, holder="h1")
    assert repos.batches.get(bid)["status"] == "Running"
    runs = repos.batch_schedules.runs(sid, limit=5)
    assert [r["outcome"] for r in runs] == ["fired"] and runs[0]["requeued"] == 1
