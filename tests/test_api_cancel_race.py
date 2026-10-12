"""취소 경합(2026-10-12 -- 사용자 요청 「Pending 과 ConfirmPending 에 취소 버튼」으로 취소가 더 자주 눌리게 되어 함께 막는다).

1) 커밋 뒤 회수: 취소가 잡을 읽은(스냅숏) 뒤 stepper 가 다음 단계를 제출하면 그 ref 는 스냅숏에 없어 아무도 끝내지 않았다
   -- 실행 vcjob 이 Cancelled 잡 밑에서 끝까지 돌았다. 이제 Cancelled 커밋 뒤 다시 읽어 새 ref 를 끝낸다(cancel.sweep_after_cancel).
2) 정직한 응답: stepper 가 먼저 종단시켰으면(종단 가드가 Cancelled 쓰기를 삼킨다) 200 Cancelled 대신 실제 결과로 맞추고 409.
3) 잡 없는 요청 취소 vs planner: 빈 잡 목록을 읽은 직후 planner 가 잡을 커밋하면 요청만 Cancelled 로 덮이고 잡은 Pending 으로
   남아 실행됐다. 이제 Pending CAS(cancel_if_pending) -- 지면 다시 읽어 잡 경로로 취소한다.
4) ConfirmPending 잡도 취소된다(포탈 버튼이 새로 생긴 상태).
"""
from dms.domain import DataJobState, RequestState


def _login(client, name="alice"):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _request(repos, requester="alice"):
    return repos.requests.create(operation="rm", requester_id=requester, actor=requester,
                                 resource_key="k-race", payload={"storage": "s", "target": "a"}, priority="mid")


def _job(repos, rid, state=DataJobState.PREVIEW_RUNNING):
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="rm", priority="mid", storage_name="s", target="a",
                                     options={}, tool="drm", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_job_state(jid, state, actor="stepper")
    return jid


def _register(adapter, ref):
    adapter._jobs[ref] = {"terminated": False}


def _hook_before_cancel_write(monkeypatch, repos, action):
    """첫 Cancelled 쓰기 직전(= 취소가 스냅숏의 ref 를 끝낸 뒤, 커밋 전)에 action 을 끼워 넣는다 -- stepper 의 끼어들기."""
    real = repos.data_jobs.set_job_state
    fired = {"n": 0}

    def wrapped(job_id, state, **kw):
        if state == DataJobState.CANCELLED and fired["n"] == 0:
            fired["n"] += 1
            action(job_id)
        return real(job_id, state, **kw)
    monkeypatch.setattr(repos.data_jobs, "set_job_state", wrapped)


def test_a_phase_submitted_during_cancel_is_terminated_after_the_commit(client, monkeypatch):
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    rid = _request(repos)
    jid = _job(repos, rid)
    repos.data_jobs.set_phase_ref(jid, "preview", "ref-preview"); _register(adapter, "ref-preview")

    def stepper_submits(job_id):                                   # 스냅숏 뒤 stepper 가 실행 단계를 제출
        repos.data_jobs.set_phase_ref(job_id, "execution", "ref-exec"); _register(adapter, "ref-exec")
    _hook_before_cancel_write(monkeypatch, repos, stepper_submits)
    _login(client)
    r = client.post(f"/api/user/jobs/{jid}:cancel")
    assert r.status_code == 200 and r.json() == {"state": "Cancelled"}
    assert adapter._jobs["ref-exec"]["terminated"] is True       # 예전엔 아무도 끝내지 않았다
    assert adapter._jobs["ref-preview"]["terminated"] is True
    assert repos.requests.get(rid)["state"] == "Cancelled"


def test_request_cancel_also_sweeps_late_phases(client, monkeypatch):
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    rid = _request(repos)
    jid = _job(repos, rid)

    def stepper_submits(job_id):
        repos.data_jobs.set_phase_ref(job_id, "execution", "ref-exec2"); _register(adapter, "ref-exec2")
    _hook_before_cancel_write(monkeypatch, repos, stepper_submits)
    _login(client)
    r = client.post(f"/api/user/requests/{rid}:cancel")
    assert r.status_code == 200
    assert adapter._jobs["ref-exec2"]["terminated"] is True


def test_cancel_that_loses_to_a_finishing_job_tells_the_truth(client, monkeypatch):
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    rid = _request(repos)
    jid = _job(repos, rid, state=DataJobState.EXECUTING)
    repos.data_jobs.set_phase_ref(jid, "execution", "ref-done"); _register(adapter, "ref-done")

    real = repos.data_jobs.set_job_state
    fired = {"n": 0}

    def wrapped(job_id, state, **kw):
        if state == DataJobState.CANCELLED and fired["n"] == 0:
            fired["n"] += 1
            real(job_id, DataJobState.SUCCEEDED, actor="stepper")   # 그 사이 stepper 가 성공으로 종단
        return real(job_id, state, **kw)
    monkeypatch.setattr(repos.data_jobs, "set_job_state", wrapped)
    _login(client)
    r = client.post(f"/api/user/jobs/{jid}:cancel")
    assert r.status_code == 409 and r.json()["detail"] == "already_terminal"
    assert repos.data_jobs.get_job(jid)["state"] == "Succeeded"
    assert repos.requests.get(rid)["state"] == "Succeeded"        # 예전엔 「요청 Cancelled · 잡 Succeeded」


def test_terminate_failure_after_commit_is_reported_not_hidden(client, monkeypatch):
    repos, adapter, db = client.app.state.repos, client.app.state.execution_adapter, client.app.state.repos.db
    rid = _request(repos)
    jid = _job(repos, rid)

    def stepper_submits(job_id):
        repos.data_jobs.set_phase_ref(job_id, "execution", "ref-stuck"); _register(adapter, "ref-stuck")
        adapter._fail_terminate_refs.add("ref-stuck")
    _hook_before_cancel_write(monkeypatch, repos, stepper_submits)
    _login(client)
    r = client.post(f"/api/user/jobs/{jid}:cancel")
    assert r.status_code == 200                                     # 이미 커밋됐다 -- 되돌릴 수 없다
    rows = db.query("SELECT severity, payload FROM events WHERE event_type = 'terminate_failed'")
    assert len(rows) == 1 and rows[0]["severity"] == "error" and "ref-stuck" in rows[0]["payload"]


def test_cancel_of_a_jobless_request_races_the_planner_safely(client, monkeypatch):
    repos = client.app.state.repos
    rid = _request(repos)
    real_list = repos.data_jobs.list_jobs
    made = {}

    def list_then_planner_emits(*, request_id=None, **kw):
        rows = real_list(request_id=request_id, **kw)
        if not made:                                                # 빈 목록을 읽은 직후 planner 가 잡을 커밋
            made["jid"] = repos.data_jobs.create_plan_and_job(
                rid, operation="rm", priority="mid", storage_name="s", target="a", options={}, tool="drm",
                worker_pool={}, precondition={}, actor="planner")
            assert made["jid"] is not None
        return rows
    monkeypatch.setattr(repos.data_jobs, "list_jobs", list_then_planner_emits)
    _login(client)
    r = client.post(f"/api/user/requests/{rid}:cancel")
    assert r.status_code == 200 and r.json() == {"state": "Cancelled"}
    jobs = real_list(request_id=rid)
    assert len(jobs) == 1 and jobs[0]["state"] == "Cancelled"      # 예전엔 Pending 으로 남아 실행됐다
    assert repos.requests.get(rid)["state"] == "Cancelled"


def test_cancel_if_pending_only_cancels_a_pending_request(client, db):
    repos = client.app.state.repos
    rid = _request(repos)
    assert repos.requests.cancel_if_pending(rid, reason_code="cancelled_by_user", actor="alice") is True
    assert repos.requests.get(rid)["state"] == "Cancelled"
    assert db.query_one("SELECT terminal_state, reason_code FROM results WHERE request_id = :r", {"r": rid}) == {
        "terminal_state": "Cancelled", "reason_code": "cancelled_by_user"}
    rid2 = _request(repos)
    repos.requests.set_state(rid2, RequestState.PLANNED, actor="planner")
    assert repos.requests.cancel_if_pending(rid2, reason_code="cancelled_by_user", actor="alice") is False
    assert repos.requests.get(rid2)["state"] == "Planned"
    assert repos.requests.cancel_if_pending("nope", reason_code="cancelled_by_user", actor="alice") is False


def _admin(client):
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})


def _batch_child(repos, *, with_job=True):
    bid = repos.batches.create(operation="rm", requester_id="admin", actor="admin", max_concurrency=1, options={},
                               note=None, items=[{"storage": "s", "target": "a"}], status="Running",
                               auth_method="session")
    rid = repos.requests.create(operation="rm", requester_id="admin", actor="admin", resource_key=f"k-{bid}",
                                payload={"storage": "s", "target": "a"}, priority="mid", batch_id=bid,
                                auth_method="session")
    repos.batches.set_item_materialized(bid, 0, rid)
    jid = _job(repos, rid, state=DataJobState.EXECUTING) if with_job else None
    return bid, rid, jid


def test_batch_cancel_sweeps_a_phase_submitted_during_the_cancel(client, monkeypatch):
    # 검증 지적(2026-10-12): 배치 취소는 자식마다 k8s 삭제를 해 창이 가장 긴데 커밋 뒤 회수가 없었다 -- 그 사이 제출된 실행
    # vcjob 이 Cancelled 배치 밑에서 끝까지 돌았다.
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    bid, rid, jid = _batch_child(repos)

    def stepper_submits(job_id):
        repos.data_jobs.set_phase_ref(job_id, "execution", "ref-bexec"); _register(adapter, "ref-bexec")
    _hook_before_cancel_write(monkeypatch, repos, stepper_submits)
    _admin(client)
    r = client.post(f"/api/admin/batches/{bid}:cancel")
    assert r.status_code == 200
    assert adapter._jobs["ref-bexec"]["terminated"] is True
    assert repos.requests.get(rid)["state"] == "Cancelled"


def test_batch_cancel_does_not_call_a_child_that_finished_meanwhile_cancelled(client, monkeypatch):
    repos = client.app.state.repos
    bid, rid, jid = _batch_child(repos)
    real = repos.data_jobs.set_job_state
    fired = {"n": 0}

    def wrapped(job_id, state, **kw):
        if state == DataJobState.CANCELLED and fired["n"] == 0:
            fired["n"] += 1
            real(job_id, DataJobState.SUCCEEDED, actor="stepper")    # 그 사이 stepper 가 성공으로 끝냈다
        return real(job_id, state, **kw)
    monkeypatch.setattr(repos.data_jobs, "set_job_state", wrapped)
    _admin(client)
    assert client.post(f"/api/admin/batches/{bid}:cancel").status_code == 200
    assert repos.data_jobs.get_job(jid)["state"] == "Succeeded"
    assert repos.requests.get(rid)["state"] == "Succeeded"           # 예전엔 스냅숏 기준으로 Cancelled 로 덮었다


def test_batch_cancel_of_a_jobless_child_races_the_planner_safely(client, monkeypatch):
    repos = client.app.state.repos
    bid, rid, _ = _batch_child(repos, with_job=False)
    real_cancel = repos.requests.cancel_if_pending

    def planner_wins_first(request_id, **kw):                         # CAS 직전 planner 가 잡을 커밋
        repos.data_jobs.create_plan_and_job(request_id, operation="rm", priority="mid", storage_name="s", target="a",
                                            options={}, tool="drm", worker_pool={}, precondition={}, actor="planner")
        return real_cancel(request_id, **kw)
    monkeypatch.setattr(repos.requests, "cancel_if_pending", planner_wins_first)
    _admin(client)
    assert client.post(f"/api/admin/batches/{bid}:cancel").status_code == 200
    jobs = repos.data_jobs.list_jobs(request_id=rid)
    assert len(jobs) == 1 and jobs[0]["state"] == "Cancelled"         # 예전엔 Pending 으로 남아 실행됐다
    assert repos.requests.get(rid)["state"] == "Cancelled"


def test_confirm_pending_job_can_be_cancelled(client):
    repos, adapter = client.app.state.repos, client.app.state.execution_adapter
    rid = _request(repos)
    jid = _job(repos, rid, state=DataJobState.CONFIRM_PENDING)
    repos.data_jobs.set_phase_ref(jid, "preview", "ref-pv"); _register(adapter, "ref-pv")
    _login(client)
    r = client.post(f"/api/user/jobs/{jid}:cancel")
    assert r.status_code == 200
    assert repos.data_jobs.get_job(jid)["state"] == "Cancelled"
    assert repos.requests.get(rid)["state"] == "Cancelled"
