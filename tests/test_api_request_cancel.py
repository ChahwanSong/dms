from dms.domain import DataJobState, RequestState

ADMIN = {"Authorization": "Bearer tok-shared"}


def _login(client, name="alice"):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _pending_request(repos, requester="alice"):
    return repos.requests.create(
        operation="rm", requester_id=requester, actor=requester,
        resource_key="k", payload={"storage": "s", "target": "a"}, priority="mid")


def _running_request_with_job(repos, requester="alice"):
    rid = _pending_request(repos, requester)
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation="rm", priority="mid", storage_name="s", target="a",
        options={}, tool="drm", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="stepper")
    return rid, jid


def test_cancel_pending_request_without_jobs(client, db):
    repos = client.app.state.repos
    rid = _pending_request(repos)
    _login(client, "alice")
    r = client.post(f"/api/user/requests/{rid}:cancel")
    assert r.status_code == 200 and r.json()["state"] == "Cancelled"
    assert repos.requests.get(rid)["state"] == "Cancelled"
    transitions = repos.requests.transitions(rid)
    assert transitions[-1]["reason_code"] == "cancelled_by_user"
    # 다른 모든 종단 전이와 동일하게 결과 행을 남긴다(set_state는 남기지 않는다).
    result = db.query_one(
        "SELECT terminal_state, reason_code FROM results WHERE request_id = :r",
        {"r": rid})
    assert result == {"terminal_state": "Cancelled", "reason_code": "cancelled_by_user"}


def test_cancel_terminates_nonterminal_job_then_request(client):
    repos = client.app.state.repos
    rid, jid = _running_request_with_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "ref-ex")
    _login(client, "alice")
    r = client.post(f"/api/user/requests/{rid}:cancel")
    assert r.status_code == 200 and r.json()["state"] == "Cancelled"
    assert repos.data_jobs.get_job(jid)["state"] == "Cancelled"
    assert repos.requests.get(rid)["state"] == "Cancelled"


def test_cancel_already_terminal_request_409(client):
    repos = client.app.state.repos
    rid = _pending_request(repos)
    repos.requests.set_state(rid, RequestState.REJECTED, actor="test")
    _login(client, "alice")
    r = client.post(f"/api/user/requests/{rid}:cancel")
    assert r.status_code == 409 and r.json()["detail"] == "already_terminal"
    assert repos.requests.get(rid)["state"] == "Rejected"


def test_cancel_other_users_request_404_admin_ok(client):
    repos = client.app.state.repos
    rid = _pending_request(repos, requester="alice")
    _login(client, "eve")
    r = client.post(f"/api/user/requests/{rid}:cancel")
    assert r.status_code == 404 and r.json()["detail"] == "request_not_found"
    client.post("/api/auth/logout")

    r = client.post(f"/api/user/requests/{rid}:cancel", headers=ADMIN)
    assert r.status_code == 200 and r.json()["state"] == "Cancelled"
    assert repos.requests.get(rid)["state"] == "Cancelled"


def test_cancel_request_whose_only_job_is_already_terminal_reconciles_instead(client, db):
    """잡은 이미 Succeeded인데 요청만 아직 비종단인 창(stepper의 finalize 누락 —
    컨트롤러 크래시 후엔 재시작까지 지속된다). 여기서 Cancelled를 기록하면 이미 끝난
    작업을 "취소됨"으로 보고하는 거짓 취소가 되고, 결과 행 없이 요청이 종단이 되어
    고아 리컨실러(terminal_jobs_with_live_request)의 시야에서도 사라진다."""
    repos = client.app.state.repos
    rid, jid = _running_request_with_job(repos)
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    _login(client, "alice")

    r = client.post(f"/api/user/requests/{rid}:cancel")

    assert r.status_code == 409 and r.json()["detail"] == "already_terminal"
    assert repos.requests.get(rid)["state"] == "Succeeded"
    assert repos.data_jobs.get_job(jid)["state"] == "Succeeded"
    result = db.query_one(
        "SELECT terminal_state, reason_code FROM results WHERE request_id = :r",
        {"r": rid})
    assert result == {"terminal_state": "Succeeded", "reason_code": "orphan_recovery"}


def test_cancel_terminate_failure_reports_500_and_leaves_state_unchanged(client):
    repos = client.app.state.repos
    rid, jid = _running_request_with_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "ref-boom")
    client.app.state.execution_adapter.fail_terminate("ref-boom")
    _login(client, "alice")
    r = client.post(f"/api/user/requests/{rid}:cancel")
    assert r.status_code == 500 and r.json()["detail"] == "cancel_failed"
    # 거짓 취소 금지 — 요청·잡 상태 모두 그대로
    assert repos.requests.get(rid)["state"] == "Running"
    assert repos.data_jobs.get_job(jid)["state"] == "Executing"
