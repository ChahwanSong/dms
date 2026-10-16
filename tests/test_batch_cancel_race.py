"""배치 취소 뒤 파드가 남는 경합(2026-10-16 사용자 보고: 「배치작업 Cancel 했는데 몇분 후에 k8s 을 직접 확인해보니 아직
돌아가고 있는 launcher/worker pod 들이 보여」, batch_cancel 모듈 docstring).

예전 경로(조사 때 스크래치 시험으로 재현): 취소가 자식 k8s 삭제·요청 마무리를 하는 동안 배치가 아직 활성이라 orchestrator
틱이 빈 슬롯에 Queued 항목의 새 자식을 만들고, 취소가 그 항목을 스냅숏 기준으로 Cancelled 로 덮어 자식 요청이 Pending 으로
남았다 -- planner·stepper 가 취소 뒤에 vcjob 을 만들어 끝까지 돌렸다. 여기서 고정하는 것:
  1) 취소 도중 틱이 와도 Cancelled 배치 밑에 비종단 자식이 없다(장벽 + 배치 Cancelled 먼저).
  2) 취소 직전 자연 종료한 형제는 Succeeded 로 기록된다(덮지 않는다) -- 집계도 한 번.
  3) planner 는 Cancelled 배치의 Pending 자식을 계획하지 않고 끝낸다.
  4) 컨트롤러 그물(sweep_cancelled)이 Cancelled 배치 밑의 살아 있는 자식을 끝낸다 -- k8s 종료 실패면 기록하지 않고 다음 틱.
  5) 잡 파드의 종료 유예가 짧다(launcher PID 1 이 SIGTERM 을 무시해 30초 Terminating 이던 것)."""
from dms.batch_cancel import record_item_terminal, sweep_cancelled
from dms.batch_orchestrator import BatchOrchestrator
from dms.domain import DataJobState, RequestState
from dms.execution import JobSpec
from dms.execution_manifests import (TASK_TERMINATION_GRACE_SECONDS, build_preflight_pod,
                                     build_volcano_job)
from dms.planner import Planner


class _S:
    preview_ttl_seconds = 900
    allow_privileged_requesters = False


def _admin(client):
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})


def _spec(job_id, phase="execution"):
    return JobSpec(job_id=job_id, phase=phase, operation="scan", tool="dscan",
                   dryrun=False, identity={"uid": 10001}, paths={"target": "a"},
                   options={}, candidates={"primary": ["n1"]}, process_count=8,
                   queue="dms-data", priority_class="dms-mid", artifact_base="file:///art")


def _executing_child(repos, adapter, rid, target):
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid", storage_name="s1",
                                     target=target, options={}, tool="dscan", worker_pool={}, precondition={},
                                     actor="planner")
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="planner")
    ref = adapter.submit(_spec(jid))
    repos.data_jobs.set_phase_ref(jid, "execution", ref)
    return jid, ref


def _children(db, bid):
    return db.query("SELECT request_id, state FROM requests WHERE batch_id = :b ORDER BY commit_order", {"b": bid})


def _scan_batch(client, targets, *, conc):
    return client.post("/api/admin/batches", json={
        "operation": "scan", "max_concurrency": conc, "options": {}, "note": None,
        "items": [{"storage": "s1", "target": t} for t in targets]}).json()["batch_id"]


def _no_live_children(db, bid):
    live = [k for k in _children(db, bid) if k["state"] not in {s.value for s in RequestState
                                                                if s.value in ("Succeeded", "Failed", "Rejected",
                                                                               "Conflict", "Cancelled")}]
    return live == []


def test_tick_during_request_finalize_does_not_leave_a_new_child(client, db, monkeypatch):
    _admin(client)
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    bid = _scan_batch(client, ["a", "b"], conc=1)
    BatchOrchestrator(repos, settings=_S()).run_once()
    it0 = [i for i in repos.batches.list_items(bid) if i["status"] == "Materialized"][0]
    _executing_child(repos, adapter, it0["request_id"], "a")

    real = repos.requests.finalize_from_job
    fired = {"n": 0}

    def finalize_then_tick(*a, **kw):
        out = real(*a, **kw)
        if fired["n"] == 0:
            fired["n"] += 1
            BatchOrchestrator(repos, settings=_S()).run_once()   # 동시 컨트롤러 틱(5초 루프)
        return out
    monkeypatch.setattr(repos.requests, "finalize_from_job", finalize_then_tick)

    r = client.post(f"/api/admin/batches/{bid}:cancel")
    assert r.status_code == 200 and repos.batches.get(bid)["status"] == "Cancelled"
    assert _no_live_children(db, bid)
    assert [p["request_id"] for p in repos.requests.list_pending()] == []
    assert len(_children(db, bid)) == 1                    # 새 자식이 생기지 않았다
    assert {i["status"] for i in repos.batches.list_items(bid)} == {"Cancelled"}


def test_tick_during_terminate_keeps_the_finished_sibling_and_makes_no_new_child(client, db, monkeypatch):
    _admin(client)
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    bid = _scan_batch(client, ["a", "b", "c"], conc=2)
    BatchOrchestrator(repos, settings=_S()).run_once()
    mats = [i for i in repos.batches.list_items(bid) if i["status"] == "Materialized"]
    assert len(mats) == 2
    _executing_child(repos, adapter, mats[0]["request_id"], "a")
    j1, _ = _executing_child(repos, adapter, mats[1]["request_id"], "b")
    # 취소 직전 stepper 가 자연 종료시킨 형제(orchestrator 는 아직 기록 전)
    repos.data_jobs.set_job_state(j1, DataJobState.SUCCEEDED, actor="stepper")
    repos.requests.finalize_from_job(mats[1]["request_id"], DataJobState.SUCCEEDED, actor="stepper")

    real_term = adapter.terminate
    fired = {"n": 0}

    def terminate_then_tick(ref):
        real_term(ref)
        if fired["n"] == 0:
            fired["n"] += 1
            BatchOrchestrator(repos, settings=_S()).run_once()   # 취소가 k8s 를 지우는 도중의 틱
    monkeypatch.setattr(adapter, "terminate", terminate_then_tick)

    r = client.post(f"/api/admin/batches/{bid}:cancel")
    assert r.status_code == 200 and repos.batches.get(bid)["status"] == "Cancelled"
    assert _no_live_children(db, bid)
    assert len(_children(db, bid)) == 2
    items = {i["seq"]: i for i in repos.batches.list_items(bid)}
    assert items[mats[1]["seq"]]["status"] == "Succeeded"           # 거짓 취소로 덮지 않는다
    assert items[mats[0]["seq"]]["status"] == "Cancelled"
    b = repos.batches.get(bid)
    assert (b["succeeded_count"], b["failed_count"]) == (1, 0)


def test_failed_terminate_rolls_back_the_barrier(client, db):
    """종료 실패(500)면 장벽·상태를 되돌린다 -- 아무것도 바꾸지 않은 것으로(거짓 취소 금지)."""
    _admin(client)
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    bid = _scan_batch(client, ["a", "b"], conc=1)
    BatchOrchestrator(repos, settings=_S()).run_once()
    it0 = [i for i in repos.batches.list_items(bid) if i["status"] == "Materialized"][0]
    _, ref = _executing_child(repos, adapter, it0["request_id"], "a")
    adapter._fail_terminate_refs.add(ref)
    r = client.post(f"/api/admin/batches/{bid}:cancel")
    assert (r.status_code, r.json()["detail"]) == (500, "cancel_failed")
    assert repos.batches.get(bid)["status"] == "Running"
    assert sorted(i["status"] for i in repos.batches.list_items(bid)) == ["Materialized", "Queued"]


def test_planner_ends_pending_children_of_a_cancelled_batch(db):
    from dms.repositories import Repositories
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin", max_concurrency=1,
                               options={}, note=None, items=[{"storage": "s1", "target": "a"}], status="Cancelled",
                               name="c", auth_method="session")
    rid = repos.requests.create(operation="scan", requester_id="admin", actor="admin", resource_key="k:pl",
                                payload={"storage": "s1", "target": "a"}, priority="mid", batch_id=bid,
                                auth_method="session")
    out = Planner(repos, None, settings=_S()).run_once()
    assert out[rid] == "cancelled:cancelled_by_batch"
    assert repos.requests.get(rid)["state"] == "Cancelled"
    assert repos.data_jobs.list_jobs(request_id=rid) == []


def _cancelled_batch_with_live_child(client):
    _admin(client)
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    bid = _scan_batch(client, ["a"], conc=1)
    BatchOrchestrator(repos, settings=_S()).run_once()
    it0 = repos.batches.list_items(bid)[0]
    jid, ref = _executing_child(repos, adapter, it0["request_id"], "a")
    repos.batches.set_status(bid, "Cancelled")       # 부분 실패·옛 경합이 남긴 모양: 배치는 Cancelled, 자식은 실행 중
    return repos, adapter, bid, it0, jid, ref


def test_controller_sweep_ends_live_children_of_a_cancelled_batch(client, db):
    repos, adapter, bid, it0, jid, ref = _cancelled_batch_with_live_child(client)
    orch = BatchOrchestrator(repos, settings=_S(), execution_adapter=adapter)
    orch.run_once()
    assert adapter._jobs[ref]["terminated"] is True
    assert repos.data_jobs.get_job(jid)["state"] == "Cancelled"
    assert repos.requests.get(it0["request_id"])["state"] == "Cancelled"
    assert repos.batches.list_items(bid)[0]["status"] == "Cancelled"
    ev = db.query("SELECT event_type FROM events WHERE event_type = 'cancelled_batch_child_swept'")
    assert len(ev) == 1
    orch.run_once()                                      # 멱등 -- 다음 틱은 할 일이 없다
    assert len(db.query("SELECT 1 AS x FROM events WHERE event_type = 'cancelled_batch_child_swept'")) == 1


def test_controller_sweep_does_not_record_when_terminate_fails(client, db):
    repos, adapter, bid, it0, jid, ref = _cancelled_batch_with_live_child(client)
    adapter._fail_terminate_refs.add(ref)
    assert sweep_cancelled(repos, adapter) == 0
    assert repos.data_jobs.get_job(jid)["state"] == "Executing"       # 파드가 돌 수 있다 -- Cancelled 로 쓰지 않는다
    assert db.query("SELECT event_type FROM events WHERE event_type = 'terminate_failed'")
    adapter._fail_terminate_refs.discard(ref)
    assert sweep_cancelled(repos, adapter) == 1                       # 다음 틱이 끝낸다


def test_item_terminal_record_is_counted_once(client, db):
    _admin(client)
    repos = client.app.state.repos
    bid = _scan_batch(client, ["a"], conc=1)
    BatchOrchestrator(repos, settings=_S()).run_once()
    it0 = repos.batches.list_items(bid)[0]
    assert record_item_terminal(repos, bid, it0["seq"], it0["request_id"], "Succeeded") is True
    assert record_item_terminal(repos, bid, it0["seq"], it0["request_id"], "Succeeded") is False
    assert repos.batches.get(bid)["succeeded_count"] == 1


def test_job_pods_have_a_short_termination_grace():
    job = build_volcano_job(_spec("a" * 32), job_image="img", namespace="dms", volumes=[])
    assert {t["template"]["spec"]["terminationGracePeriodSeconds"] for t in job["spec"]["tasks"]} == {
        TASK_TERMINATION_GRACE_SECONDS}
    nsync = _spec("b" * 32)
    nsync = JobSpec(**{**nsync.__dict__, "tool": "nsync", "operation": "sync",
                       "paths": {"source": "a", "destination": "b"},
                       "candidates": {"source": ["n1"], "destination": ["n2"]}})
    njob = build_volcano_job(nsync, job_image="img", namespace="dms", volumes=[])
    assert {t["template"]["spec"]["terminationGracePeriodSeconds"] for t in njob["spec"]["tasks"]} == {
        TASK_TERMINATION_GRACE_SECONDS}
    pod = build_preflight_pod(_spec("c" * 32, phase="preflight"), job_image="img", namespace="dms", volumes=[],
                              node="n1")
    assert pod["spec"]["terminationGracePeriodSeconds"] == TASK_TERMINATION_GRACE_SECONDS
    assert TASK_TERMINATION_GRACE_SECONDS <= 10
