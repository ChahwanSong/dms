from dms.domain import DataJobState
from dms.repositories import Repositories


def _repos(db):
    return Repositories(db)


def _mk_request(repos):
    return repos.requests.create(
        operation="scan", requester_id="alice", actor="alice",
        resource_key="data.scan:s1:a:ff", payload={"storage": "s1", "target": "a"},
        priority="mid")


def test_create_plan_and_job_links(db):
    repos = _repos(db)
    rid = _mk_request(repos)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={"top_k": 5}, tool="dscan",
        worker_pool={"tool": "dscan", "candidates": {"primary": ["n1"]}},
        precondition={"job_id": "x"}, actor="planner")
    job = repos.data_jobs.get_job(job_id)
    assert job["state"] == "Pending"
    assert job["tool"] == "dscan"
    assert job["options"] == {"top_k": 5}
    assert job["worker_pool"]["candidates"]["primary"] == ["n1"]
    plan = db.query_one("SELECT job_id, state FROM plans WHERE plan_id = :p", {"p": plan_id})
    assert plan["job_id"] == job_id and plan["state"] == "Planned"


def test_job_state_transitions_recorded(db):
    repos = _repos(db)
    rid = _mk_request(repos)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool="dscan", worker_pool={}, precondition={},
        actor="planner")
    repos.data_jobs.set_job_state(job_id, DataJobState.PREFLIGHT, actor="stepper")
    repos.data_jobs.set_job_state(job_id, DataJobState.REJECTED,
                                 reason_code="posix_permission_denied", actor="stepper")
    ts = repos.data_jobs.job_transitions(job_id)
    assert [(t["from_state"], t["to_state"]) for t in ts] == [
        (None, "Pending"), ("Pending", "Preflight"), ("Preflight", "Rejected")]
    assert ts[2]["reason_code"] == "posix_permission_denied"
    assert repos.data_jobs.get_job(job_id)["reason_code"] == "posix_permission_denied"


def test_list_jobs_by_request(db):
    repos = _repos(db)
    rid = _mk_request(repos)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool="dscan", worker_pool={}, precondition={},
        actor="planner")
    assert [j["job_id"] for j in repos.data_jobs.list_jobs(request_id=rid)] == [job_id]
    assert repos.data_jobs.list_jobs(request_id="nope") == []


def _job(repos, *, operation="scan", storage_name="s1", target="a", state=None):
    rid = repos.requests.create(
        operation=operation, requester_id="alice", actor="alice",
        resource_key=f"data.{operation}:{storage_name}:{target}:{state}",
        payload={"storage": storage_name, "target": target}, priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation=operation, priority="mid", storage_name=storage_name,
        target=target, options={}, tool="dscan", worker_pool={}, precondition={},
        actor="planner")
    if state is not None:
        repos.data_jobs.set_job_state(job_id, state, actor="stepper")
    return job_id


def test_succeeded_scans_only_succeeded_scan_jobs_on_that_storage(db):
    """커버링 scan 후보는 '이 스토리지에서 **성공한 scan**'뿐이다 — 실패했거나 진행
    중인 잡, 다른 연산(sync/rm)은 통계의 출처가 될 수 없다."""
    repos = _repos(db)
    wanted = _job(repos, state=DataJobState.SUCCEEDED)
    _job(repos, target="b", state=DataJobState.FAILED)
    _job(repos, target="c", state=DataJobState.RUNNING)
    _job(repos, target="d", state=None)                      # Pending
    _job(repos, target="e", state=DataJobState.CANCELLED)
    _job(repos, operation="sync", target="f", state=DataJobState.SUCCEEDED)
    _job(repos, operation="rm", target="g", state=DataJobState.SUCCEEDED)
    _job(repos, storage_name="s2", target="h", state=DataJobState.SUCCEEDED)

    rows = repos.data_jobs.succeeded_scans("s1")
    assert [r["job_id"] for r in rows] == [wanted]


def test_succeeded_scans_orders_by_completion_not_creation(db):
    """'최신 **완료** scan'이다 — 나중에 만들어졌지만 먼저 끝난 잡보다, 먼저
    만들어졌지만 나중에 성공한 잡이 앞선다(set_job_state가 updated_at을 찍는다)."""
    repos = _repos(db)
    old_creation = _job(repos, target="a")
    new_creation = _job(repos, target="b")
    db.execute("UPDATE data_jobs SET created_at = :c WHERE job_id = :j",
               {"c": "2020-01-01T00:00:00Z", "j": old_creation})
    db.execute("UPDATE data_jobs SET created_at = :c WHERE job_id = :j",
               {"c": "2020-06-01T00:00:00Z", "j": new_creation})
    repos.data_jobs.set_job_state(new_creation, DataJobState.SUCCEEDED, actor="stepper")
    repos.data_jobs.set_job_state(old_creation, DataJobState.SUCCEEDED, actor="stepper")
    db.execute("UPDATE data_jobs SET updated_at = :u WHERE job_id = :j",
               {"u": "2021-01-01T00:00:00Z", "j": new_creation})
    db.execute("UPDATE data_jobs SET updated_at = :u WHERE job_id = :j",
               {"u": "2021-06-01T00:00:00Z", "j": old_creation})

    assert [r["job_id"] for r in repos.data_jobs.succeeded_scans("s1")] == [
        old_creation, new_creation]
    assert [r["job_id"] for r in repos.data_jobs.succeeded_scans("s1", limit=1)] == [
        old_creation]


def test_terminal_jobs_older_than_drains_oldest_first(db):
    """GC는 오래된 것부터 비워야 한다 — 대상이 limit보다 많을 때 최신순으로 잘라내면
    가장 오래된(가장 급한) 잡이 윈도우 밖으로 밀려나 영원히 재방문되지 않는다
    (컨트롤러가 오래 멈췄다 돌아온 뒤 특히 치명적). limit=2, 대상 3건일 때
    가장 오래된 두 건이 나와야 한다."""
    repos = _repos(db)
    oldest = _job(repos, target="a", state=DataJobState.SUCCEEDED)
    middle = _job(repos, target="b", state=DataJobState.FAILED)
    newest = _job(repos, target="c", state=DataJobState.CANCELLED)
    db.execute("UPDATE data_jobs SET updated_at = :u WHERE job_id = :j",
               {"u": "2020-01-01T00:00:00Z", "j": oldest})
    db.execute("UPDATE data_jobs SET updated_at = :u WHERE job_id = :j",
               {"u": "2020-06-01T00:00:00Z", "j": middle})
    db.execute("UPDATE data_jobs SET updated_at = :u WHERE job_id = :j",
               {"u": "2021-01-01T00:00:00Z", "j": newest})

    rows = repos.data_jobs.terminal_jobs_older_than(
        3600, limit=2, now_iso="2030-01-01T00:00:00Z")

    assert [r["job_id"] for r in rows] == [oldest, middle]
