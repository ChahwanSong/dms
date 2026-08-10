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


def _mk_job(repos, rid):
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    return repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool="dscan", worker_pool={}, precondition={},
        actor="planner")


def test_set_artifact_promotes_files_bytes_to_columns(db):
    # 설계 §2.3: runner가 언젠가 summary에 files/bytes를 쓰면 마이그레이션 없이
    # typed 컬럼으로 흘러들어 대시보드 SQL 집계에 잡힌다 -- 그 파이프라인의 계약.
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    repos.data_jobs.set_artifact(
        job_id, artifact_uri="file:///a",
        result_summary={"returncode": 0, "files": 12, "bytes": 3456})
    job = repos.data_jobs.get_job(job_id)
    assert (job["files_count"], job["bytes_count"]) == (12, 3456)
    assert job["result_summary"] == {"returncode": 0, "files": 12, "bytes": 3456}


def test_set_artifact_round_trips_counts_beyond_int4(db):
    # 3 GiB. PostgreSQL의 int4 천장(2147483647)을 넘는 실 바이트가 승격 경로를
    # 손상 없이 통과해야 한다 -- 스키마가 BIGINT인 이유(migrations.py). 구형 PG DB를
    # 넓히는 ALTER COLUMN TYPE 경로는 여기서 검증할 수 없다: SQLite는 INTEGER가
    # 애초에 동적 64비트라 좁은 컬럼을 만들 수도, ALTER COLUMN TYPE을 지원하지도
    # 않는다. 이 테스트가 고정하는 건 "파이프라인이 큰 정수를 클램프/절단하지
    # 않는다"까지다(_as_count는 의도적으로 클램프하지 않는다 -- 조용한 오염 금지).
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    repos.data_jobs.set_artifact(
        job_id, artifact_uri=None,
        result_summary={"files": 3, "bytes": 3221225472})
    job = repos.data_jobs.get_job(job_id)
    assert (job["files_count"], job["bytes_count"]) == (3, 3221225472)


def test_set_artifact_without_counts_leaves_null(db):
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    repos.data_jobs.set_artifact(job_id, artifact_uri=None,
                                 result_summary={"returncode": 0})
    job = repos.data_jobs.get_job(job_id)
    assert (job["files_count"], job["bytes_count"]) == (None, None)


def test_set_artifact_rejects_non_int_counts(db):
    # summary는 신뢰 경계 밖(runner 산출물) -- 문자열·bool·음수가 컬럼을 오염시키면 안 된다
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    repos.data_jobs.set_artifact(
        job_id, artifact_uri=None,
        result_summary={"files": "12", "bytes": True})
    job = repos.data_jobs.get_job(job_id)
    assert (job["files_count"], job["bytes_count"]) == (None, None)


def test_submit_wait_recorded_once_on_first_pickup(db):
    # 슬라이스 17 설계 §2.3: Pending -> 첫 비-Pending 엣지에서 한 번만(write-once).
    # 이름 그대로 "제출 대기"다 -- Volcano 큐 대기가 아니라 DMS 내부 픽업 지연
    # (스테퍼 틱 간격 포함)이고, 스키마·API·화면 라벨이 모두 같은 말을 한다(§2.4).
    from dms.db import iso_plus, utc_now_iso
    repos = _repos(db)
    rid = _mk_request(repos)
    job_id = _mk_job(repos, rid)
    db.execute("UPDATE data_jobs SET created_at = :c WHERE job_id = :j",
               {"c": iso_plus(utc_now_iso(), -120), "j": job_id})
    repos.data_jobs.set_job_state(job_id, DataJobState.PREFLIGHT, actor="stepper")
    wait = repos.data_jobs.get_job(job_id)["submit_wait_seconds"]
    assert 120 <= wait <= 122            # 1초 해상도 + 실행 지연 여유
    # 이후 전이는 덮어쓰지 않는다 -- created_at 을 더 과거로 밀어도 값이 그대로여야
    # "재계산 없음"이 증명된다(비터미널 재전이의 덮어쓰기 금지, 설계 §2.3).
    db.execute("UPDATE data_jobs SET created_at = :c WHERE job_id = :j",
               {"c": "2020-01-01T00:00:00Z", "j": job_id})
    repos.data_jobs.set_job_state(job_id, DataJobState.EXECUTING, actor="stepper")
    assert repos.data_jobs.get_job(job_id)["submit_wait_seconds"] == wait


def test_submit_wait_stays_null_while_pending(db):
    # 아직 Pending 인 잡은 "대기 미확정"이다 -- 0 이나 지금까지의 경과로 채우면
    # 집계가 진행 중인 대기를 완료된 대기처럼 오염시킨다(NULL = 집계 제외).
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    assert repos.data_jobs.get_job(job_id)["submit_wait_seconds"] is None


def test_mark_exec_submitted_is_write_once(db):
    # 슬라이스 20 설계 §2.2: 앵커는 "첫 execution 제출" 시각이다. 재호출(크래시 후
    # 재제출 등)이 앵커를 뒤로 밀면 sched_wait 이 실제보다 짧게 측정된다 --
    # write-once 는 호출자 선의가 아니라 SQL 술어(IS NULL)가 강제한다.
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    assert repos.data_jobs.get_job(job_id)["exec_submitted_at"] is None
    repos.data_jobs.mark_exec_submitted(job_id)
    assert repos.data_jobs.get_job(job_id)["exec_submitted_at"] is not None
    # 이미 기록된 앵커를 식별 가능한 값으로 바꿔 두고 재호출 -- 술어가 막는다
    db.execute("UPDATE data_jobs SET exec_submitted_at = '2020-01-01T00:00:00Z' "
               "WHERE job_id = :j", {"j": job_id})
    repos.data_jobs.mark_exec_submitted(job_id)
    assert (repos.data_jobs.get_job(job_id)["exec_submitted_at"]
            == "2020-01-01T00:00:00Z")
