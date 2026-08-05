"""배치 취소가 실제로 종료하는지 검증한다. 상위 스펙 §5: 종료가 전부 성공한
뒤에만 DB를 Cancelled로 기록한다 — 거짓 취소 금지. terminate가 하나라도
ExecutionError면 500 cancel_failed이고 배치·item·자식 잡·자식 요청 상태는
호출 전과 동일해야 한다."""
from dms.batch_orchestrator import BatchOrchestrator
from dms.domain import DataJobState
from dms.execution import ExecStatus, JobSpec


class _S:  # 최소 settings 더미(BatchOrchestrator 생성자용)
    preview_ttl_seconds = 900


def _admin(client):
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})


def _orch(repos):
    return BatchOrchestrator(repos, settings=_S())


def _spec(job_id, phase="execution"):
    return JobSpec(job_id=job_id, phase=phase, operation="scan", tool="dscan",
                   dryrun=False, identity={"uid": 10001}, paths={"target": "a"},
                   options={}, candidates={"primary": ["n1"]}, process_count=8,
                   queue="dms-data", priority_class="dms-mid",
                   artifact_base="file:///art")


def _make_batch_with_one_executing_child(client):
    """max_concurrency=1, item 2개짜리 scan 배치를 만들고 오케스트레이터를 한 번
    돌려 item[0]만 materialize한다(item[1]은 Queued로 남는다). materialize된 자식
    request 위에 플래너를 우회해 직접 data_job을 만들고 Executing 상태 + 실제
    어댑터에 submit된 phase_ref를 부여해 '지금 클러스터에서 도는 자식'을 흉내낸다.
    """
    _admin(client)
    repos = client.app.state.repos
    adapter = client.app.state.execution_adapter
    bid = client.post("/api/admin/batches", json={
        "operation": "scan", "max_concurrency": 1, "options": {}, "note": None,
        "items": [{"storage": "s1", "target": "a"}, {"storage": "s1", "target": "b"}],
    }).json()["batch_id"]
    _orch(repos).run_once()
    items = repos.batches.list_items(bid)
    materialized = [it for it in items if it["status"] == "Materialized"]
    queued = [it for it in items if it["status"] == "Queued"]
    assert len(materialized) == 1 and len(queued) == 1
    item = materialized[0]
    rid = item["request_id"]
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool="dscan", worker_pool={}, precondition={},
        actor="planner")
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="planner")
    ref = adapter.submit(_spec(jid))
    repos.data_jobs.set_phase_ref(jid, "execution", ref)
    return bid, item, queued[0], jid, rid, ref


def test_cancel_terminates_materialized_children(client):
    bid, item, queued_item, jid, rid, ref = _make_batch_with_one_executing_child(client)
    adapter = client.app.state.execution_adapter

    r = client.post(f"/api/admin/batches/{bid}:cancel")

    assert r.status_code == 200
    assert adapter.poll(ref) == ExecStatus.FAILED   # terminate가 그 phase ref로 호출됐다


def test_cancel_flips_child_job_and_request_to_cancelled(client):
    bid, item, queued_item, jid, rid, ref = _make_batch_with_one_executing_child(client)
    repos = client.app.state.repos

    client.post(f"/api/admin/batches/{bid}:cancel")

    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Cancelled" and job["reason_code"] == "cancelled_by_batch"
    assert repos.requests.get(rid)["state"] == "Cancelled"


def test_cancel_marks_materialized_item_cancelled(client):
    bid, item, queued_item, jid, rid, ref = _make_batch_with_one_executing_child(client)
    repos = client.app.state.repos

    client.post(f"/api/admin/batches/{bid}:cancel")

    by_seq = {it["seq"]: it for it in repos.batches.list_items(bid)}
    assert by_seq[item["seq"]]["status"] == "Cancelled"


def test_cancel_marks_queued_item_cancelled(client):
    bid, item, queued_item, jid, rid, ref = _make_batch_with_one_executing_child(client)
    repos = client.app.state.repos

    client.post(f"/api/admin/batches/{bid}:cancel")

    by_seq = {it["seq"]: it for it in repos.batches.list_items(bid)}
    assert by_seq[queued_item["seq"]]["status"] == "Cancelled"


def test_cancel_marks_batch_cancelled(client):
    bid, item, queued_item, jid, rid, ref = _make_batch_with_one_executing_child(client)
    repos = client.app.state.repos

    r = client.post(f"/api/admin/batches/{bid}:cancel")

    assert r.json()["status"] == "Cancelled"
    assert repos.batches.get(bid)["status"] == "Cancelled"


def test_cancel_failed_terminate_leaves_everything_unchanged(client):
    bid, item, queued_item, jid, rid, ref = _make_batch_with_one_executing_child(client)
    repos = client.app.state.repos
    adapter = client.app.state.execution_adapter
    adapter.fail_terminate(ref)

    # 호출 전 스냅샷
    before_batch = repos.batches.get(bid)
    before_items = repos.batches.list_items(bid)
    before_job = repos.data_jobs.get_job(jid)
    before_req = repos.requests.get(rid)

    r = client.post(f"/api/admin/batches/{bid}:cancel")

    assert r.status_code == 500
    assert r.json()["detail"] == "cancel_failed"
    # 거짓 취소 금지 — 배치·item·자식 잡·자식 요청 전부 호출 전과 동일해야 한다
    assert repos.batches.get(bid) == before_batch
    assert repos.batches.list_items(bid) == before_items
    assert repos.data_jobs.get_job(jid) == before_job
    assert repos.requests.get(rid) == before_req


def test_cancel_non_previewing_or_running_batch_409(client):
    _admin(client)
    repos = client.app.state.repos
    bid = client.post("/api/admin/batches", json={
        "operation": "scan", "max_concurrency": 1, "options": {}, "note": None,
        "items": [{"storage": "s1", "target": "a"}],
    }).json()["batch_id"]
    repos.batches.set_status(bid, "Completed")

    r = client.post(f"/api/admin/batches/{bid}:cancel")

    assert r.status_code == 409
    assert r.json()["detail"] == "batch_not_cancelable"
