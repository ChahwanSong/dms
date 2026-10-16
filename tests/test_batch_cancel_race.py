"""배치 취소 뒤 파드가 남는 경합(2026-10-16 사용자 보고: 「배치작업 Cancel 했는데 몇분 후에 k8s 을 직접 확인해보니 아직
돌아가고 있는 launcher/worker pod 들이 보여」, batch_cancel 모듈 docstring).

예전 경로(조사 때 스크래치 시험으로 재현): 취소가 자식 k8s 삭제·요청 마무리를 하는 동안 배치가 아직 활성이라 orchestrator
틱이 빈 슬롯에 Queued 항목의 새 자식을 만들고, 취소가 그 항목을 스냅숏 기준으로 Cancelled 로 덮어 자식 요청이 Pending 으로
남았다 -- planner·stepper 가 취소 뒤에 vcjob 을 만들어 끝까지 돌렸다. 여기서 고정하는 것:
  1) 취소 도중 틱이 와도 취소된 배치 밑에 비종단 자식이 없다(한 트랜잭션의 장벽 + 배치 Cancelling 먼저).
  2) 취소 직전 자연 종료한 형제는 Succeeded 로 기록된다(덮지 않는다) -- 집계도 한 번.
  3) planner 는 취소 중·취소된 배치의 Pending 자식을 계획하지 않고 끝낸다. stepper 는 그 자식의 실패(지워진 vcjob)를
     Cancelled 로 기록한다.
  4) 컨트롤러 그물(sweep_cancelled)이 유예 뒤 남은 자식을 끝낸다 -- k8s 종료 실패면 기록하지 않고 다음 틱, 자식마다 격리,
     기록 안 된 항목도 마저 기록하고 배치를 Cancelled 로.
  5) 잡 파드의 종료 유예가 짧다(launcher PID 1 이 SIGTERM 을 무시해 30초 Terminating 이던 것)."""
import pytest

from dms.batch_cancel import CANCEL_SWEEP_GRACE_SECONDS, record_item_terminal, settle_child, sweep_cancelled
from dms.db import iso_plus, utc_now_iso
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


def _later(seconds=CANCEL_SWEEP_GRACE_SECONDS + 5):
    return iso_plus(utc_now_iso(), seconds)


def test_failed_terminate_leaves_cancelling_and_the_sweep_finishes_after_the_grace(client, db):
    """종료 실패면 그 자식은 기록하지 않고(거짓 취소 금지) 배치는 Cancelling -- 그물이 유예 뒤 마저 끝낸다. 예전엔 장벽·
    상태를 되돌리고 500 이었는데, 이미 지운 형제는 되돌릴 수 없어 「아무것도 안 바뀜」이 거짓이었다."""
    _admin(client)
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    bid = _scan_batch(client, ["a", "b"], conc=1)
    BatchOrchestrator(repos, settings=_S()).run_once()
    it0 = [i for i in repos.batches.list_items(bid) if i["status"] == "Materialized"][0]
    jid, ref = _executing_child(repos, adapter, it0["request_id"], "a")
    adapter._fail_terminate_refs.add(ref)
    r = client.post(f"/api/admin/batches/{bid}:cancel")
    assert r.json() == {"status": "Cancelling", "pending": 1}
    assert sorted(i["status"] for i in repos.batches.list_items(bid)) == ["Cancelled", "Materialized"]

    BatchOrchestrator(repos, settings=_S(), execution_adapter=adapter).run_once()   # 유예 안 -- 손대지 않는다
    assert repos.data_jobs.get_job(jid)["state"] == "Executing"
    assert repos.batches.get(bid)["status"] == "Cancelling"
    assert sweep_cancelled(repos, adapter, now=_later()) == 0                         # 여전히 종료 실패
    assert repos.batches.get(bid)["status"] == "Cancelling"

    adapter._fail_terminate_refs.discard(ref)
    assert sweep_cancelled(repos, adapter, now=_later()) == 1
    assert repos.data_jobs.get_job(jid)["state"] == "Cancelled"
    assert {i["status"] for i in repos.batches.list_items(bid)} == {"Cancelled"}
    b = repos.batches.get(bid)
    assert (b["status"], b["succeeded_count"], b["failed_count"]) == ("Cancelled", 0, 0)
    assert len(_children(db, bid)) == 1


def test_begin_cancel_is_all_or_nothing(client, db):
    """CAS 패배(그 사이 완료)면 장벽까지 롤백 -- 장벽만 커밋되고 배치가 끝나 Queued 항목이 영영 안 도는 창이 없다."""
    _admin(client)
    repos = client.app.state.repos
    bid = _scan_batch(client, ["a", "b"], conc=1)
    assert repos.batches.begin_cancel(bid, from_states=("Previewing",)) is False      # scan 배치는 Running
    assert sorted(i["status"] for i in repos.batches.list_items(bid)) == ["Queued", "Queued"]
    assert repos.batches.get(bid)["status"] == "Running"
    assert repos.batches.begin_cancel(bid, from_states=("Running",)) is True
    assert repos.batches.get(bid)["status"] == "Cancelling"
    assert {(i["status"], i["reason_code"]) for i in repos.batches.list_items(bid)} == {
        ("Cancelled", "cancelled_by_batch")}


def _cancelling(client, targets=("a", "b")):
    _admin(client)
    repos = client.app.state.repos
    bid = _scan_batch(client, list(targets), conc=1)
    BatchOrchestrator(repos, settings=_S()).run_once()
    assert repos.batches.begin_cancel(bid, from_states=("Running",))
    return repos, bid


def test_cancelling_batch_rejects_requeue_and_terminal_only_routes(client, db):
    repos, bid = _cancelling(client)
    assert client.post(f"/api/admin/batches/{bid}:cancel").json()["detail"] == "batch_not_cancelable"
    r = client.post(f"/api/admin/batches/{bid}/items", json={"storage": "s1", "target": "c"})
    assert (r.status_code, r.json()["detail"]) == (409, "batch_cancelling")
    r = client.post(f"/api/admin/batches/{bid}/items:rerun", json={"seqs": [1]})
    assert (r.status_code, r.json()["detail"]) == (409, "batch_cancelling")
    r = client.post(f"/api/admin/batches/{bid}:rerun-failed")
    assert (r.status_code, r.json()["detail"]) == (409, "batch_cancelling")
    # 종단 전용 경로: 기록 삭제(남은 자식이 그물 밖으로 빠진다)·실행 설정 변경
    assert client.delete(f"/api/admin/batches/{bid}").json()["detail"] == "batch_not_deletable"
    assert client.patch(f"/api/admin/batches/{bid}/execution",
                        json={"max_concurrency": 2}).json()["detail"] == "batch_settings_locked"
    BatchOrchestrator(repos, settings=_S()).run_once()                # 굴리지 않는다(list_active 밖)
    assert repos.batches.get(bid)["status"] == "Cancelling"
    assert len(_children(db, bid)) == 1


def test_sweep_bars_a_queued_item_that_slipped_into_a_cancelling_batch(client, db):
    repos, bid = _cancelling(client)
    db.execute("UPDATE batch_items SET status = 'Queued', reason_code = NULL WHERE batch_id = :b AND seq = 1",
               {"b": bid})                                            # 경합으로 끼어든 Queued(가정)
    it0 = repos.batches.get_item(bid, 0)
    repos.requests.cancel_if_pending(it0["request_id"], reason_code="cancelled_by_batch", actor="t")
    sweep_cancelled(repos, client.app.state.execution_adapter, now=_later())
    assert repos.batches.get_item(bid, 1)["status"] == "Cancelled"
    assert repos.batches.get(bid)["status"] == "Cancelled"


def test_sweep_records_leftover_materialized_items_by_the_child_result(client, db):
    """라우트가 중간에 멈춘 취소(API 재시작): 항목이 Materialized 로 남고 자식은 이미 끝났다 -- 그물이 그 결과로 기록한다."""
    repos, bid = _cancelling(client, targets=("a",))
    it0 = repos.batches.get_item(bid, 0)
    repos.requests.set_state(it0["request_id"], RequestState.PLANNED, actor="t")
    repos.requests.set_state(it0["request_id"], RequestState.RUNNING, actor="t")
    repos.requests.finalize_from_job(it0["request_id"], DataJobState.SUCCEEDED, actor="stepper")
    assert repos.batches.get_item(bid, 0)["status"] == "Materialized"
    sweep_cancelled(repos, client.app.state.execution_adapter, now=_later())
    b = repos.batches.get(bid)
    assert (repos.batches.get_item(bid, 0)["status"], b["status"], b["succeeded_count"]) == (
        "Succeeded", "Cancelled", 1)


def test_sweep_isolates_a_poison_row(client, db, monkeypatch):
    """한 자식의 예외가 그물 전체를 멈추지 않는다(LIMIT 의 맨 앞 독 행)."""
    _admin(client)
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    rids = []
    for t in ("a", "b"):
        bid = _scan_batch(client, [t], conc=1)
        BatchOrchestrator(repos, settings=_S()).run_once()
        it = repos.batches.list_items(bid)[0]
        _executing_child(repos, adapter, it["request_id"], t)
        repos.batches.set_status(bid, "Cancelled")
        rids.append(it["request_id"])
    real = repos.data_jobs.list_jobs

    def poisoned(*a, **kw):
        if kw.get("request_id") == rids[0]:
            raise ValueError("corrupt row")
        return real(*a, **kw)
    monkeypatch.setattr(repos.data_jobs, "list_jobs", poisoned)
    assert sweep_cancelled(repos, adapter) == 1
    assert repos.requests.get(rids[1])["state"] == "Cancelled"
    assert repos.requests.get(rids[0])["state"] != "Cancelled"
    assert db.query("SELECT 1 AS x FROM events WHERE event_type = 'cancel_sweep_failed' AND request_id = :r",
                    {"r": rids[0]})
    sweep_cancelled(repos, adapter)                                    # 쿨다운 -- 이벤트가 틱마다 쌓이지 않는다
    assert len(db.query("SELECT 1 AS x FROM events WHERE event_type = 'cancel_sweep_failed'")) == 1


def test_child_killed_by_the_cancel_is_a_cancelled_item_not_a_failure(client, db, monkeypatch):
    """종료 도중 stepper 가 (취소 전 읽은 배치 상태로) 지워진 vcjob 을 FAILED 로 마무리한 경합 -- 항목은 Cancelled·
    cancelled_by_batch, 실패 수 0(「실패분 재실행」 대상이 아니다)."""
    _admin(client)
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    bid = _scan_batch(client, ["a"], conc=1)
    BatchOrchestrator(repos, settings=_S()).run_once()
    it0 = repos.batches.list_items(bid)[0]
    jid, ref = _executing_child(repos, adapter, it0["request_id"], "a")
    real_term = adapter.terminate

    def terminate_then_stepper_fails_it(r):
        real_term(r)
        repos.data_jobs.set_job_state(jid, DataJobState.FAILED, reason_code="execution_failed", actor="stepper")
        repos.requests.finalize_from_job(it0["request_id"], DataJobState.FAILED, reason_code="execution_failed",
                                         actor="stepper")
    monkeypatch.setattr(adapter, "terminate", terminate_then_stepper_fails_it)
    assert client.post(f"/api/admin/batches/{bid}:cancel").json() == {"status": "Cancelled"}
    item = repos.batches.list_items(bid)[0]
    assert (item["status"], item["reason_code"]) == ("Cancelled", "cancelled_by_batch")
    assert repos.batches.get(bid)["failed_count"] == 0


def test_finalize_from_job_is_idempotent_under_a_second_caller(client, db):
    """취소 라우트와 그물·stepper 가 같은 요청을 함께 마무리해도 results PK 충돌이 없다(잠근 행으로 다시 본다)."""
    _admin(client)
    repos = client.app.state.repos
    bid = _scan_batch(client, ["a"], conc=1)
    BatchOrchestrator(repos, settings=_S()).run_once()
    rid = repos.batches.list_items(bid)[0]["request_id"]
    repos.requests.set_state(rid, RequestState.PLANNED, actor="t")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="t")
    repos.requests.finalize_from_job(rid, DataJobState.CANCELLED, reason_code="cancelled_by_batch", actor="api")
    repos.requests.finalize_from_job(rid, DataJobState.FAILED, reason_code="execution_failed", actor="stepper")
    assert repos.requests.get(rid)["state"] == "Cancelled"
    assert len(db.query("SELECT 1 AS x FROM results WHERE request_id = :r", {"r": rid})) == 1


def test_settle_child_without_jobs_cancels_a_pending_child(client, db):
    _admin(client)
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    bid = _scan_batch(client, ["a"], conc=1)
    BatchOrchestrator(repos, settings=_S()).run_once()
    rid = repos.batches.list_items(bid)[0]["request_id"]
    assert settle_child(repos, adapter, rid, [], actor="t", component="api") == ("Cancelled", None)
    assert repos.requests.get(rid)["state"] == "Cancelled"


@pytest.mark.parametrize("status", ["Cancelling", "Cancelled"])
def test_planner_ends_pending_children_of_a_cancelled_batch(db, status):
    from dms.repositories import Repositories
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin", max_concurrency=1,
                               options={}, note=None, items=[{"storage": "s1", "target": "a"}], status=status,
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


def test_sweep_is_not_starved_by_children_that_keep_failing(client, db):
    """종료가 계속 실패하는 자식이 limit 칸을 늘 차지하지 않는다(무작위 순서, 검증 지적) -- 뒤에 취소된 다른 배치의 자식도
    끝난다."""
    _admin(client)
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    bad = _scan_batch(client, ["a", "b", "c"], conc=3)
    BatchOrchestrator(repos, settings=_S()).run_once()
    for it in repos.batches.list_items(bad):
        _, ref = _executing_child(repos, adapter, it["request_id"], it["payload"]["target"])
        adapter._fail_terminate_refs.add(ref)
    repos.batches.set_status(bad, "Cancelled")
    good = _scan_batch(client, ["d"], conc=1)
    BatchOrchestrator(repos, settings=_S()).run_once()
    git = repos.batches.list_items(good)[0]
    _executing_child(repos, adapter, git["request_id"], "d")
    repos.batches.set_status(good, "Cancelled")
    for _ in range(30):
        sweep_cancelled(repos, adapter, limit=2)
        if repos.requests.get(git["request_id"])["state"] == "Cancelled":
            break
    assert repos.requests.get(git["request_id"])["state"] == "Cancelled"


def test_route_keeps_the_sweep_grace_alive_and_reports_the_real_status(client, db, monkeypatch):
    """라우트가 자식마다 updated_at 을 밀어(touch_cancelling) 그물 유예가 begin_cancel 시각에 묶이지 않는다. 그물이 그 사이
    마저 끝냈으면 응답은 실제 상태(Cancelled)다."""
    _admin(client)
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    bid = _scan_batch(client, ["a", "b"], conc=2)
    BatchOrchestrator(repos, settings=_S()).run_once()
    for it in repos.batches.list_items(bid):
        _executing_child(repos, adapter, it["request_id"], it["payload"]["target"])
    seen = []
    real_term = adapter.terminate

    def spy(ref):
        seen.append(repos.batches.get(bid)["updated_at"])
        db.execute("UPDATE batches SET updated_at = '2000-01-01T00:00:00Z' WHERE batch_id = :b", {"b": bid})
        real_term(ref)
    monkeypatch.setattr(adapter, "terminate", spy)
    assert client.post(f"/api/admin/batches/{bid}:cancel").json() == {"status": "Cancelled"}
    assert len(seen) == 2 and seen[1] != "2000-01-01T00:00:00Z"     # 둘째 자식 전에 다시 밀었다
    # 이벤트 증거: 취소가 지우러 온 자식마다 하나
    assert len(db.query("SELECT 1 AS x FROM events WHERE event_type = 'batch_cancel_terminate'")) == 2

