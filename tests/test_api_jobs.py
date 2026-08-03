from dms.domain import DataJobState, RequestState
from dms.execution import StubExecutionAdapter


def _login(client, name="alice"):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _confirmpending_job(app_repos, requester="alice"):
    repos = app_repos
    rid = repos.requests.create(operation="sync", requester_id=requester, actor=requester,
        resource_key="k", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint="sha256:abc",
        expires_at="2099-01-01T00:00:00Z", artifact_uri="file:///art/j")
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    return rid, jid


def test_list_jobs_isolation(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos, requester="alice")
    _login(client, "eve")
    assert client.get(f"/api/user/requests/{rid}/jobs").status_code == 404
    client.post("/api/auth/logout")
    _login(client, "alice")
    jobs = client.get(f"/api/user/requests/{rid}/jobs").json()
    assert jobs[0]["job_id"] == jid and jobs[0]["state"] == "ConfirmPending"


def test_confirm_happy_and_mismatch(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    _login(client, "alice")
    assert client.post(f"/api/user/jobs/{jid}:confirm",
                       json={"fingerprint": "sha256:wrong"}).status_code == 409
    r = client.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": "sha256:abc"})
    assert r.status_code == 200 and r.json()["state"] == "Executing"
    assert repos.data_jobs.get_job(jid)["confirmed_fingerprint"] == "sha256:abc"


def test_confirm_not_confirmpending_409(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    _login(client, "alice")
    assert client.post(f"/api/user/jobs/{jid}:confirm",
                       json={"fingerprint": "sha256:abc"}).status_code == 409


def test_cancel_terminates_then_records(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    repos.data_jobs.set_phase_ref(jid, "execution", "ref-ex")
    adapter = client.app.state.execution_adapter
    _login(client, "alice")
    r = client.post(f"/api/user/jobs/{jid}:cancel")
    assert r.status_code == 200 and r.json()["state"] == "Cancelled"
    assert repos.requests.get(rid)["state"] == "Cancelled"


def test_cancel_terminated_ref_reports_failure_not_false_cancel(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    repos.data_jobs.set_phase_ref(jid, "execution", "ref-boom")
    client.app.state.execution_adapter.fail_terminate("ref-boom")
    _login(client, "alice")
    r = client.post(f"/api/user/jobs/{jid}:cancel")
    assert r.status_code == 500 and r.json()["detail"] == "cancel_failed"
    # 거짓 취소 금지 — 상태 그대로
    assert repos.data_jobs.get_job(jid)["state"] == "Executing"
