import uuid

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
        target="a", options={"broken_limit": 5}, tool="dscan",
        worker_pool={"tool": "dscan", "candidates": {"primary": ["n1"]}},
        precondition={"job_id": "x"}, actor="planner")
    job = repos.data_jobs.get_job(job_id)
    assert job["state"] == "Pending"
    assert job["tool"] == "dscan"
    assert job["options"] == {"broken_limit": 5}
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


def test_set_artifact_without_promotion_keeps_summary_but_leaves_counts_null(db):
    # d192: 미리보기 실패의 summary 는 화면에 보이되 files/bytes 는 할 예정인 수라 컬럼(지표 원천)에 올리지 않는다.
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    repos.data_jobs.set_artifact(job_id, artifact_uri="file:///a", promote_counts=False,
                                 result_summary={"returncode": 1, "files": 4, "bytes": 16})
    job = repos.data_jobs.get_job(job_id)
    assert job["result_summary"] == {"returncode": 1, "files": 4, "bytes": 16}
    assert (job["files_count"], job["bytes_count"]) == (None, None)
    assert job["artifact_uri"] == "file:///a"


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


def test_record_sched_wait_zero_survives_and_predicate_blocks_overwrite(db):
    # 0 은 결측이 아니라 "같은 틱 안에 스케줄됨"이라는 가장 건강한 값이다(설계
    # §2.4). 미래 앵커(시계 스큐)를 심어 max(0, 음수) -> 0 이 되는 **결정적**
    # 경로로 0 을 만든다 -- 기록 계층에 truthy 검사(`if wait:` 따위)가 끼면
    # 여기서 NULL 로 새는 것이 잡힌다. 이어서 낡은 스냅샷(선독 통과)으로 재호출해
    # SQL 술어(IS NULL)가 최종 방어선임을 고정한다: 값이 0 이어도 "기록됨"이라
    # 덮이면 안 된다(술어가 IS NULL 아닌 falsy 로 쓰였으면 여기서 덮인다).
    from dms.db import iso_plus, utc_now_iso
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    db.execute("UPDATE data_jobs SET exec_submitted_at = :t WHERE job_id = :j",
               {"t": iso_plus(utc_now_iso(), 3600), "j": job_id})
    repos.data_jobs.record_sched_wait(repos.data_jobs.get_job(job_id))
    assert repos.data_jobs.get_job(job_id)["sched_wait_seconds"] == 0  # NULL 아님
    db.execute("UPDATE data_jobs SET exec_submitted_at = '2020-01-01T00:00:00Z' "
               "WHERE job_id = :j", {"j": job_id})
    stale = dict(repos.data_jobs.get_job(job_id))
    stale["sched_wait_seconds"] = None            # 선독을 일부러 통과시킨다
    repos.data_jobs.record_sched_wait(stale)
    assert repos.data_jobs.get_job(job_id)["sched_wait_seconds"] == 0


def test_record_sched_wait_skips_update_when_snapshot_has_value(db, monkeypatch):
    # 클레임 스냅샷 선독(설계 §2.3): 이미 기록된 잡은 매 틱 0행 UPDATE 를 반복하지
    # 않는다 -- RUNNING 이 오래 지속되는 잡이 폴링마다 DB 쓰기를 만들면 안 된다.
    # 0 도 "기록됨"이다: 선독이 truthy(`if job[...]`)로 쓰이면 0 스냅샷에서
    # UPDATE 가 나가 여기서 잡힌다.
    repos = _repos(db)
    executed = []
    orig = db.execute
    monkeypatch.setattr(
        db, "execute",
        lambda sql, params=None: (executed.append(sql), orig(sql, params))[1])
    repos.data_jobs.record_sched_wait(
        {"job_id": "j-x", "sched_wait_seconds": 7,
         "exec_submitted_at": "2026-01-01T00:00:00Z"})
    repos.data_jobs.record_sched_wait(
        {"job_id": "j-x", "sched_wait_seconds": 0,
         "exec_submitted_at": "2026-01-01T00:00:00Z"})
    assert executed == []


def test_record_sched_wait_without_anchor_or_broken_anchor_stays_null(db):
    # 앵커가 없으면(마이그레이션 이전 잡 등) 값을 지어내지 않는다(설계 §2.5).
    # 깨진 시각은 iso_epoch ValueError -> NULL 유지 -- set_job_state 의
    # submit_wait 강등 선례(data_jobs.py)와 같은 규칙(설계 §4).
    repos = _repos(db)
    job_id = _mk_job(repos, _mk_request(repos))
    repos.data_jobs.record_sched_wait(repos.data_jobs.get_job(job_id))  # 앵커 None
    assert repos.data_jobs.get_job(job_id)["sched_wait_seconds"] is None
    db.execute("UPDATE data_jobs SET exec_submitted_at = 'not-a-time' "
               "WHERE job_id = :j", {"j": job_id})
    repos.data_jobs.record_sched_wait(repos.data_jobs.get_job(job_id))
    assert repos.data_jobs.get_job(job_id)["sched_wait_seconds"] is None


# ---- 사용량 분석 스토리지별 조회(2026-10-20) ----

def _usage_scan(repos, db, *, storage, target, at, state=DataJobState.SUCCEEDED):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
                                resource_key=f"k:{uuid.uuid4().hex}", payload={"storage": storage, "target": target},
                                priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid", storage_name=storage,
                                     target=target, options={}, tool="dscan", worker_pool={}, precondition={},
                                     actor="planner")
    repos.data_jobs.set_job_state(jid, state, actor="stepper")
    db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j", {"t": at, "j": jid})
    return jid


def test_recent_scans_for_targets_is_per_storage_chunked_and_newest_first(db, monkeypatch):
    # 저장소가 스스로 _IN_CHUNK 로 자른다(호출자가 묶음을 잊어도) -- 경계에서 빠지거나 겹치는 타깃이 없고, 타깃마다 최신순
    # per_target 개, 다른 스토리지의 같은 이름 타깃은 섞이지 않는다. 실패 scan 은 사용량 밖이다.
    from dms.repositories import data_jobs
    monkeypatch.setattr(data_jobs, "_IN_CHUNK", 2)
    repos = _repos(db)
    jobs = {}
    for t in ("a", "b", "c", "d", "e"):
        jobs[t] = [_usage_scan(repos, db, storage="s1", target=t, at=f"2026-09-0{d}T00:00:00Z") for d in (1, 2, 3)]
    _usage_scan(repos, db, storage="s1", target="a", at="2026-09-09T00:00:00Z", state=DataJobState.FAILED)
    other = _usage_scan(repos, db, storage="s2", target="a", at="2026-09-08T00:00:00Z")
    got = repos.data_jobs.recent_scans_for_targets("s1", ["a", "b", "c", "d", "e", "a", "zz"], per_target=2)
    assert set(got) == {"a", "b", "c", "d", "e"}
    assert all([j["job_id"] for j in got[t]] == [jobs[t][2], jobs[t][1]] for t in got)
    assert got["a"][0]["worker_pool"] == {} and "diag_logs" not in got["a"][0]       # 수화된 행, diag 제외
    assert repos.data_jobs.recent_scans_for_targets("s2", ["a"])["a"][0]["job_id"] == other
    assert repos.data_jobs.recent_scans_for_targets(None, ["a"]) == {}               # NULL 은 아무것도 맞지 않는다
    assert repos.data_jobs.recent_scans_for_targets("s1", []) == {}
    # 빈 이름은 「필터 없음」이 아니라 빈 이름이다(truthy 로 접지 않는다)
    assert repos.data_jobs.recent_scans_for_targets("", ["a"]) == {}
    db.execute("UPDATE data_jobs SET storage_name = '' WHERE job_id = :j", {"j": other})
    assert repos.data_jobs.recent_scans_for_targets("", ["a"])["a"][0]["job_id"] == other


def test_jobs_by_ids_chunks_and_skips_missing(db, monkeypatch):
    from dms.repositories import data_jobs
    monkeypatch.setattr(data_jobs, "_IN_CHUNK", 2)
    repos = _repos(db)
    ids = [_usage_scan(repos, db, storage="s1", target=t, at="2026-09-01T00:00:00Z") for t in "abcde"]
    got = repos.data_jobs.jobs_by_ids(ids + ["0" * 32, ids[0]])
    assert set(got) == set(ids) and got[ids[0]]["target"] == "a"
    assert repos.data_jobs.jobs_by_ids([]) == {}


def test_scan_target_heads_carry_the_latest_job_and_old_group_by_values(db):
    # 머리 = 옛 GROUP BY 의 값(scan_count·first·last) + 최신 성공 scan 의 job_id. 같은 시각이면 job_id 가 큰 쪽이 최신
    # (succeeded_scans_for_target 과 같은 근거). 필터(path)가 걸려도 파티션은 잘리지 않는다.
    repos = _repos(db)
    j1 = _usage_scan(repos, db, storage="s1", target="team", at="2026-09-01T00:00:00Z")
    j2 = _usage_scan(repos, db, storage="s1", target="team", at="2026-09-05T00:00:00Z")
    j3 = _usage_scan(repos, db, storage="s1", target="team", at="2026-09-05T00:00:00Z")
    _usage_scan(repos, db, storage="s1", target="team", at="2026-09-09T00:00:00Z", state=DataJobState.FAILED)
    _usage_scan(repos, db, storage="s2", target="team", at="2026-09-07T00:00:00Z")
    [head] = repos.data_jobs.scan_target_heads(storage="s1", path="tea")
    assert (head["scan_count"], head["first_scan_at"], head["last_scan_at"]) == (
        3, "2026-09-01T00:00:00Z", "2026-09-05T00:00:00Z")
    assert head["job_id"] == max(j2, j3) and head["job_id"] != j1
    assert [h["storage_name"] for h in repos.data_jobs.scan_target_heads()] == ["s2", "s1"]   # 최근 스캔 내림차순


# ---- 사용량 분석 필터(2026-10-29): 요청자는 대상 단위, 맞는 대상 수, PV 주소 scan 기록 ----

def _scan_by(repos, db, *, requester, storage="s1", target="team", at, pv=None, state=DataJobState.SUCCEEDED):
    payload = {"storage": storage, "target": target, **({"pv": pv} if pv is not None else {})}
    rid = repos.requests.create(operation="scan", requester_id=requester, actor=requester,
                                resource_key=f"k:{uuid.uuid4().hex}", payload=payload, priority="mid",
                                target_label=f"{pv['pvc']} — {pv['namespace']} ({pv['storage_class']})" if pv else None)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid", storage_name=storage,
                                     target=target, options={}, tool="dscan", worker_pool={}, precondition={},
                                     actor="planner")
    repos.data_jobs.set_job_state(jid, state, actor="stepper")
    db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j", {"t": at, "j": jid})
    return jid


def _heads(repos, **kw):
    return [(h["target"], h["scan_count"], h["first_scan_at"], h["last_scan_at"], h["job_id"])
            for h in repos.data_jobs.scan_target_heads(storage="s1", **kw)]


def test_requester_filter_is_per_target_and_keeps_the_whole_partition(db):
    # 옛 scan 은 alice, 최신은 mason -- alice 로 걸러도 그 대상의 전체 이력(scan_count·first·최신 잡)이 그대로 나온다(잡 단위로
    # 거르면 scan_count 1·최신 = alice 의 옛 잡이 되어 정렬 값과 화면의 latest 가 다른 잡에서 나온다).
    repos = _repos(db)
    _scan_by(repos, db, requester="alice", target="team", at="2026-09-01T00:00:00Z")
    newest = _scan_by(repos, db, requester="mason", target="team", at="2026-09-05T00:00:00Z")
    _scan_by(repos, db, requester="mason", target="solo", at="2026-09-03T00:00:00Z")
    _scan_by(repos, db, requester="alice", target="failed-only", at="2026-09-04T00:00:00Z", state=DataJobState.FAILED)
    _scan_by(repos, db, requester="alice", storage="s2", target="team", at="2026-09-06T00:00:00Z")
    everything = _heads(repos)
    assert [h[0] for h in everything] == ["team", "solo"]
    assert _heads(repos, requester="alice") == [everything[0]]
    assert everything[0] == ("team", 2, "2026-09-01T00:00:00Z", "2026-09-05T00:00:00Z", newest)
    assert _heads(repos, requester="mason") == everything
    assert _heads(repos, requester="nobody") == []
    assert _heads(repos, requester="alice", path="sol") == []                  # path 와 함께 걸린다
    assert _heads(repos, requester="", sort="scans") == []                     # 빈 문자열도 값이다(「필터 없음」이 아니다)


def test_orphan_jobs_drop_out_only_under_the_requester_filter(db):
    # 요청 행이 없는 data_jobs(DB 가 신뢰 경계 -- 무검증 INSERT·지워진 요청): 요청자를 모르므로 어느 요청자의 것도 아니다.
    repos = _repos(db)
    _scan_by(repos, db, requester="alice", target="team", at="2026-09-01T00:00:00Z")
    orphan = _scan_by(repos, db, requester="alice", target="orphan", at="2026-09-02T00:00:00Z")
    rid = db.query_one("SELECT request_id FROM data_jobs WHERE job_id = :j", {"j": orphan})["request_id"]
    db.execute("DELETE FROM requests WHERE request_id = :r", {"r": rid})
    assert [h[0] for h in _heads(repos)] == ["orphan", "team"]
    assert [h[0] for h in _heads(repos, requester="alice")] == ["team"]
    assert repos.data_jobs.scan_requester_counts("s1") == [{"requester_id": "alice", "target_count": 1}]


def test_matched_count_is_the_filtered_total_not_the_page(db):
    repos = _repos(db)
    for i, t in enumerate(["a", "b", "c", "d"]):
        _scan_by(repos, db, requester="alice" if t != "d" else "mason", target=t, at=f"2026-09-0{i + 1}T00:00:00Z")
    for limit in (1, 2, 100):
        rows = repos.data_jobs.scan_target_heads(storage="s1", limit=limit, with_matched=True)
        assert {r["matched_count"] for r in rows} == {4} and len(rows) == min(limit, 4)
        rows = repos.data_jobs.scan_target_heads(storage="s1", requester="alice", limit=limit, with_matched=True)
        assert {r["matched_count"] for r in rows} == {3}
    assert repos.data_jobs.scan_target_heads(storage="s1", path="zz", with_matched=True) == []
    assert "matched_count" not in repos.data_jobs.scan_target_heads(storage="s1")[0]


# 새 인자가 기본값일 때의 머리 문장 -- 2026-10-20 의 것 그대로(계획 테스트·tests/test_usage_equivalence.py 가 이 모양에 기댄다).
_HEADS_SQL_2026_10_20 = (
    "SELECT storage_name, target, scan_count, first_scan_at, last_scan_at, job_id, request_id, bytes_count "
    "FROM (SELECT storage_name, target, job_id, request_id, bytes_count, updated_at AS last_scan_at, "
    "ROW_NUMBER() OVER (PARTITION BY storage_name, target ORDER BY updated_at DESC, job_id DESC) AS rn, "
    "COUNT(*) OVER (PARTITION BY storage_name, target) AS scan_count, "
    "MIN(updated_at) OVER (PARTITION BY storage_name, target) AS first_scan_at "
    "FROM data_jobs WHERE operation = 'scan' AND state = :s AND target IS NOT NULL AND storage_name = :sn) heads "
    "WHERE rn = 1 ORDER BY last_scan_at DESC, storage_name, target LIMIT :n")


def test_default_arguments_leave_the_heads_statement_unchanged(db, monkeypatch):
    repos = _repos(db)
    seen = []
    real = db.query
    monkeypatch.setattr(db, "query", lambda sql, params=None: (seen.append((sql, params)), real(sql, params))[1])
    repos.data_jobs.scan_target_heads(storage="s1")
    repos.data_jobs.scan_target_heads(storage="s1", requester=None, with_matched=False)
    assert seen[0][0] == seen[1][0]
    assert " ".join(seen[0][0].split()) == _HEADS_SQL_2026_10_20
    assert set(seen[0][1]) == {"s", "sn", "n"}
    repos.data_jobs.scan_target_heads(storage="s1", requester="alice", with_matched=True)
    assert "requester_hit" in seen[2][0] and "matched_count" in seen[2][0] and seen[2][1]["rq"] == "alice"


def _pv(ns, pvc, root, cluster="dmsk8s"):
    return {"cluster": cluster, "namespace": ns, "pvc": pvc, "volume": f"pvc-{pvc}", "storage_class": "tb-nfs",
            "root": root, "path": ""}


def test_recorded_pv_blocks_are_newest_first_and_only_pv_address_scans(db):
    import json
    repos = _repos(db)
    assert repos.data_jobs.has_recorded_pv_scans("s1") is False
    _scan_by(repos, db, requester="mason", target="team-a/data/x", at="2026-09-09T00:00:00Z")           # 직접 경로
    _scan_by(repos, db, requester="alice", target="team-a/data/x", at="2026-09-01T00:00:00Z",
             pv=_pv("team-a", "old", "team-a/data"))
    _scan_by(repos, db, requester="alice", target="team-a/data/x", at="2026-09-05T00:00:00Z",
             pv=_pv("team-a", "new", "team-a/data"))
    _scan_by(repos, db, requester="alice", target="team-a/data/x", at="2026-09-07T00:00:00Z",
             pv=_pv("team-a", "failed", "team-a/data"), state=DataJobState.FAILED)
    _scan_by(repos, db, requester="alice", storage="s2", target="team-a/data/x", at="2026-09-08T00:00:00Z",
             pv=_pv("team-a", "other-storage", "team-a/data"))
    assert repos.data_jobs.has_recorded_pv_scans("s1") is True
    assert repos.data_jobs.has_recorded_pv_scans("s3") is False and repos.data_jobs.has_recorded_pv_scans(None) is False
    got = repos.data_jobs.recorded_pv_blocks("s1", ["team-a/data/x", "nope", "team-a/data/x"])
    assert list(got) == ["team-a/data/x"]
    assert [json.loads(p)["pv"]["pvc"] for p in got["team-a/data/x"]] == ["new", "old"]


def test_recorded_pv_blocks_chunk_their_placeholders_and_skip_empty_input(db, monkeypatch):
    import re
    repos = _repos(db)
    _scan_by(repos, db, requester="alice", target="t00007", at="2026-09-01T00:00:00Z", pv=_pv("ns", "p", "t00007"))
    statements = []
    real = db.query

    def spy(sql, params=None):
        statements.append(len(set(re.findall(r":t\d+", sql))))
        return real(sql, params)
    monkeypatch.setattr(db, "query", spy)
    got = repos.data_jobs.recorded_pv_blocks("s1", [f"t{i:05d}" for i in range(1100)])
    assert statements == [500, 500, 100] and list(got) == ["t00007"]
    statements.clear()
    assert repos.data_jobs.recorded_pv_blocks(None, ["t00007"]) == {}
    assert repos.data_jobs.recorded_pv_blocks("s1", []) == {}
    assert statements == []                                                    # 문장 없음


def test_scan_requester_counts_count_distinct_targets_per_requester(db):
    repos = _repos(db)
    for requester, target in (("alice", "a"), ("alice", "a"), ("alice", "b"), ("mason", "a"), ("junwoo.song", "c")):
        _scan_by(repos, db, requester=requester, target=target, at="2026-09-01T00:00:00Z")
    _scan_by(repos, db, requester="zed", target="z", at="2026-09-01T00:00:00Z", state=DataJobState.FAILED)
    _scan_by(repos, db, requester="other", storage="s2", target="a", at="2026-09-01T00:00:00Z")
    assert repos.data_jobs.scan_requester_counts("s1") == [
        {"requester_id": "alice", "target_count": 2}, {"requester_id": "junwoo.song", "target_count": 1},
        {"requester_id": "mason", "target_count": 1}]
    assert repos.data_jobs.scan_requester_counts(None) == [] and repos.data_jobs.scan_requester_counts("s9") == []
