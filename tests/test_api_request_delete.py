"""작업(요청) 선택 삭제 API(routes_request_purge.py, 2026-10-08) -- 권한·입력 검증·부분 성공·삭제 후 읽기 경로.

판정·원자성 자체는 tests/test_repo_request_purges.py 가 본다. 여기서는 라우트 계약(세션 관리자만, 유지보수 503, 422 두 종,
항목별 skipped 사유, 응답 모양)과 "삭제 커밋 직후 모든 읽기 경로가 404·제외" 를 고정한다."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState, RequestState
from dms.migrations import migrate

TOKEN = {"Authorization": "Bearer tok-shared"}
OLD = "2026-01-01T00:00:00Z"
PATH = "/api/admin/requests:delete"


def _session(client, name="opadm", role="admin"):
    client.app.state.repos.accounts.create(name, "p", role, actor="t")
    assert client.post("/api/auth/login", json={"username": name, "password": "p"}).status_code == 200
    return name


@pytest.fixture
def session_admin(client):
    # 세션 관리자. 변경 호출엔 Bearer 헤더를 싣지 않는다(헤더가 세션 쿠키보다 우선).
    return _session(client)


def _age(db, rid):
    db.execute("UPDATE requests SET updated_at = :t WHERE request_id = :r", {"t": OLD, "r": rid})
    db.execute("UPDATE data_jobs SET updated_at = :t WHERE request_id = :r", {"t": OLD, "r": rid})


def _finished(repos, *, op="scan", job_state=DataJobState.SUCCEEDED, requester="alice", target="t",
              resource_key="k", age=True):
    rid = repos.requests.create(operation=op, requester_id=requester, actor=requester, resource_key=resource_key,
                                payload={"storage": "s1", "target": target, "run_as_root": True}, priority="mid",
                                auth_method="session")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation=op, priority="mid", storage_name="s1", target=target, options={},
        tool="dscan" if op == "scan" else "drm", precondition={}, actor="planner",
        worker_pool={"identity": {"username": requester, "uid": os.getuid(), "gid": os.getgid(),
                                  "privileged": False}})
    repos.data_jobs.set_phase_ref(jid, "execution", f"vcjob/dms-{op}-execution-{jid[:12]}")
    repos.data_jobs.set_job_state(jid, job_state, actor="stepper")
    repos.requests.finalize_from_job(rid, job_state, actor="stepper")
    repos.observability.record_event(component="stepper", severity="info", event_type="x", request_id=rid)
    if age:
        _age(repos.db, rid)
    return rid, jid


def _rows(db, rid, jid):
    one = lambda sql, p: db.query_one(sql, p)["n"]                     # noqa: E731
    return {
        "requests": one("SELECT COUNT(*) AS n FROM requests WHERE request_id = :r", {"r": rid}),
        "results": one("SELECT COUNT(*) AS n FROM results WHERE request_id = :r", {"r": rid}),
        "plans": one("SELECT COUNT(*) AS n FROM plans WHERE request_id = :r", {"r": rid}),
        "data_jobs": one("SELECT COUNT(*) AS n FROM data_jobs WHERE request_id = :r", {"r": rid}),
        "events": one("SELECT COUNT(*) AS n FROM events WHERE request_id = :r", {"r": rid}),
        "transitions": one("SELECT COUNT(*) AS n FROM state_transitions WHERE entity_id IN (:r, :j)",
                           {"r": rid, "j": jid}),
    }


# ---- 권한 ----

def test_requires_login(client):
    assert client.post(PATH, json={"request_ids": ["a" * 32]}).status_code == 401
    assert client.get("/api/admin/request-purges").status_code == 401


def test_regular_user_gets_admin_required(client):
    rid, jid = _finished(client.app.state.repos)
    _session(client, "alice", "user")
    r = client.post(PATH, json={"request_ids": [rid]})
    assert (r.status_code, r.json()["detail"]) == (403, "admin_required")
    assert client.get("/api/admin/request-purges").status_code == 403
    assert client.app.state.repos.requests.get(rid) is not None


@pytest.mark.parametrize("headers", [TOKEN, {**TOKEN, "x-dms-actor": "node:storage-01"}])
def test_shared_token_cannot_delete_and_nothing_changes(client, db, headers):
    # 공유 토큰은 모든 노드 에이전트가 쥔 role admin 자격이다 -- rm/sync 를 누가 컨펌·취소했는지의 유일한 기록과
    # root 실행 산출물을 그것으로 지울 수 있으면 안 된다(빌드·레지스트리 삭제와 같은 세션 전용 경계).
    rid, jid = _finished(client.app.state.repos)
    before = _rows(db, rid, jid)
    r = client.post(PATH, json={"request_ids": [rid]}, headers=headers)
    assert (r.status_code, r.json()["detail"]) == (403, "admin_session_required")
    assert _rows(db, rid, jid) == before
    assert db.query_one("SELECT COUNT(*) AS n FROM audit_log")["n"] == 0
    assert db.query_one("SELECT COUNT(*) AS n FROM request_purges")["n"] == 0
    # 조회는 토큰도 된다(포탈 밖 모니터링).
    assert client.get("/api/admin/request-purges", headers=headers).status_code == 200


def test_session_admin_deletes_and_audits_as_the_session_user(client, db, session_admin):
    repos = client.app.state.repos
    rid, jid = _finished(repos)
    r = client.post(PATH, json={"request_ids": [rid]})
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": [{"request_id": rid, "job_ids": [jid]}], "skipped": [], "purge_pending": 1}
    assert set(_rows(db, rid, jid).values()) == {0}
    audit = db.query("SELECT * FROM audit_log WHERE mutation_class = 'request'")
    assert [(a["operation"], a["target_key"], a["actor"]) for a in audit] == [("delete", rid, "opadm")]
    snap = json.loads(audit[0]["before_state"])
    assert snap["request"]["payload"]["run_as_root"] is True
    purge = repos.request_purges.get(rid)
    assert purge["stage"] == "k8s" and purge["requested_by"] == "opadm"
    assert purge["artifact_base"] == "/artifacts/dms"                       # Settings 기본 base(env), 스킴 제거
    assert purge["jobs"][0]["phase_refs"] == {"execution": f"vcjob/dms-scan-execution-{jid[:12]}"}


def test_outbox_records_the_db_artifact_base(client, session_admin):
    repos = client.app.state.repos
    repos.control.set_artifact_base("file:///cephfs/dms/artifacts", actor="t")
    rid, _ = _finished(repos)
    assert client.post(PATH, json={"request_ids": [rid]}).status_code == 200
    assert repos.request_purges.get(rid)["artifact_base"] == "/cephfs/dms/artifacts"


# ---- 입력 검증 ----

def test_maintenance_blocks_delete(client, db, session_admin):
    rid, jid = _finished(client.app.state.repos)
    assert client.put("/api/admin/control-state",
                      json={"maintenance": True, "drain": False, "reason": None}).status_code == 200
    r = client.post(PATH, json={"request_ids": [rid]})
    assert (r.status_code, r.json()["detail"]) == (503, "maintenance_mode")
    assert _rows(db, rid, jid)["requests"] == 1


def test_empty_selection_is_422(client, session_admin):
    r = client.post(PATH, json={"request_ids": []})
    assert (r.status_code, r.json()["detail"]) == (422, "empty_selection")


def test_more_than_200_is_422_and_nothing_is_deleted(client, db, session_admin):
    rid, jid = _finished(client.app.state.repos)
    ids = [rid] + [f"{i:032x}" for i in range(200)]
    r = client.post(PATH, json={"request_ids": ids})
    assert (r.status_code, r.json()["detail"]) == (422, "delete_selection_too_large")
    assert _rows(db, rid, jid)["requests"] == 1


def test_duplicates_are_folded_before_the_limit(client, session_admin):
    rid, jid = _finished(client.app.state.repos)
    r = client.post(PATH, json={"request_ids": [rid] * 250})
    assert r.status_code == 200
    assert r.json()["deleted"] == [{"request_id": rid, "job_ids": [jid]}] and r.json()["skipped"] == []


@pytest.mark.parametrize("bad", ["../etc", "ABCDEF" + "0" * 26, "a" * 31, "a" * 33, "", "g" * 32])
def test_malformed_id_is_skipped_as_not_found(client, bad, session_admin):
    r = client.post(PATH, json={"request_ids": [bad]})
    assert r.status_code == 200
    assert r.json() == {"deleted": [], "skipped": [{"request_id": bad, "reason": "request_not_found"}],
                        "purge_pending": 0}


def test_non_string_ids_are_framework_422(client, session_admin):
    assert client.post(PATH, json={"request_ids": [1, 2]}).status_code == 422
    assert client.post(PATH, json={}).status_code == 422


# ---- 부분 성공: 항목별 skipped 사유 ----

def test_partial_success_reports_each_skip_reason_in_order(client, db, session_admin):
    repos = client.app.state.repos
    ok, ok_job = _finished(repos)
    pending = repos.requests.create(operation="rm", requester_id="alice", actor="alice", resource_key="p",
                                    payload={}, priority="mid")
    _age(db, pending)
    confirm, _ = _finished(repos, op="rm", resource_key="c")
    db.execute("UPDATE requests SET state = 'Planned' WHERE request_id = :r", {"r": confirm})
    db.execute("UPDATE data_jobs SET state = 'ConfirmPending' WHERE request_id = :r", {"r": confirm})
    child, _ = _finished(repos, resource_key="b")
    db.execute("UPDATE requests SET batch_id = 'b1' WHERE request_id = :r", {"r": child})
    item_only, _ = _finished(repos, resource_key="i")
    db.execute("""INSERT INTO batch_items (batch_id, seq, payload, status, request_id, created_at, updated_at)
                  VALUES ('b2', 1, '{}', 'Succeeded', :r, :at, :at)""", {"r": item_only, "at": OLD})
    active, _ = _finished(repos, resource_key="a")
    db.execute("UPDATE data_jobs SET state = 'Executing' WHERE request_id = :r", {"r": active})
    recent, _ = _finished(repos, resource_key="q", age=False)
    missing = "f" * 32
    ids = [missing, child, item_only, pending, confirm, active, recent, ok]
    r = client.post(PATH, json={"request_ids": ids})
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == [{"request_id": ok, "job_ids": [ok_job]}]
    assert body["skipped"] == [
        {"request_id": missing, "reason": "request_not_found"},
        {"request_id": child, "reason": "batch_child_not_deletable"},
        {"request_id": item_only, "reason": "batch_child_not_deletable"},
        {"request_id": pending, "reason": "request_not_deletable"},
        {"request_id": confirm, "reason": "request_not_deletable"},
        {"request_id": active, "reason": "request_job_active"},
        {"request_id": recent, "reason": "request_recently_finished"},
    ]
    assert body["purge_pending"] == 1
    for rid in (child, item_only, pending, confirm, active, recent):
        assert repos.requests.get(rid) is not None, rid


def test_all_skipped_is_still_200(client, session_admin):
    r = client.post(PATH, json={"request_ids": ["f" * 32]})
    assert r.status_code == 200 and r.json()["deleted"] == []


def test_one_items_db_error_skips_only_that_item_and_keeps_going(client, db, session_admin, monkeypatch):
    # 2026-10-09 검증 지적: 항목마다 자기 트랜잭션이라 앞 항목은 이미 지워졌는데, 중간 항목의 DB 오류(교착·연결 끊김·
    # 변조 행)가 응답 전체를 500 으로 만들어 그 사실을 숨기고 뒤 항목은 시도조차 안 했다.
    repos = client.app.state.repos
    first, j1 = _finished(repos, resource_key="k1")
    bad, jb = _finished(repos, resource_key="k2")
    last, j3 = _finished(repos, resource_key="k3")
    real = repos.request_purges.delete_terminal

    def flaky(rid, **kw):
        if rid == bad:
            raise RuntimeError("deadlock detected")
        return real(rid, **kw)
    monkeypatch.setattr(repos.request_purges, "delete_terminal", flaky)
    r = client.post(PATH, json={"request_ids": [first, bad, last]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [d["request_id"] for d in body["deleted"]] == [first, last]
    assert body["skipped"] == [{"request_id": bad, "reason": "request_delete_failed"}]
    assert repos.requests.get(first) is None and repos.requests.get(last) is None
    assert repos.requests.get(bad) is not None and _rows(db, bad, jb)["data_jobs"] == 1   # 실패 항목은 그대로


def test_outage_after_a_committed_item_still_reports_what_was_deleted(client, session_admin, monkeypatch):
    # 2026-10-09 검증 지적: 첫 항목 커밋 직후 DB 가 끊기고(재연결 실패) 뒤 항목은 request_delete_failed 로 빠지는데, 응답의
    # 정리 대기 건수(pending_count)가 같은 죽은 연결에서 던져 응답 전체가 500 -- 이미 되돌릴 수 없이 지운 항목이 「전체
    # 실패」 뒤에 숨었다. 건수는 덤이다: 세지 못하면 None(모름)이고 지운·제외 목록은 200 으로 간다.
    repos = client.app.state.repos
    first, _ = _finished(repos, resource_key="k1")
    second, _ = _finished(repos, resource_key="k2")
    real = repos.request_purges.delete_terminal
    down = {"v": False}

    def flaky(rid, **kw):
        if down["v"]:
            raise RuntimeError("server closed the connection unexpectedly")
        r = real(rid, **kw)
        down["v"] = True                       # 첫 커밋 직후 DB 가 사라진다
        return r

    def count():
        raise RuntimeError("server closed the connection unexpectedly")
    monkeypatch.setattr(repos.request_purges, "delete_terminal", flaky)
    monkeypatch.setattr(repos.request_purges, "pending_count", count)
    c = TestClient(client.app, raise_server_exceptions=False)
    c.cookies = client.cookies
    r = c.post(PATH, json={"request_ids": [first, second]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [d["request_id"] for d in body["deleted"]] == [first]
    assert body["skipped"] == [{"request_id": second, "reason": "request_delete_failed"}]
    assert body["purge_pending"] is None                 # 모름 -- 0(정리할 것 없음)으로 접지 않는다


def test_deeply_nested_tampered_json_does_not_block_the_delete(client, db, session_admin):
    # 변조 행의 깊게 중첩된 JSON 은 디코더가 RecursionError 를 던진다 -- 깨진 JSON 과 같이 다룬다(감사엔 원문 앞부분).
    repos = client.app.state.repos
    rid, jid = _finished(repos)
    db.execute("UPDATE data_jobs SET phase_refs = :p WHERE job_id = :j", {"p": "[" * 100000, "j": jid})
    db.execute("UPDATE requests SET payload = :p WHERE request_id = :r", {"p": "[" * 100000, "r": rid})
    r = client.post(PATH, json={"request_ids": [rid]})
    assert r.status_code == 200 and [d["request_id"] for d in r.json()["deleted"]] == [rid], r.text
    audit = json.loads(db.query_one("SELECT before_state FROM audit_log WHERE target_key = :r", {"r": rid})
                       ["before_state"])
    assert audit["request"]["payload"] == {"unparsed": "[" * 1000}


def test_second_delete_reports_not_found(client, session_admin):
    rid, _ = _finished(client.app.state.repos)
    assert client.post(PATH, json={"request_ids": [rid]}).json()["deleted"]
    assert client.post(PATH, json={"request_ids": [rid]}).json()["skipped"] == [
        {"request_id": rid, "reason": "request_not_found"}]


# ---- 삭제 직후 읽기 경로 ----

def test_every_read_path_is_404_right_after_delete(client, session_admin):
    rid, jid = _finished(client.app.state.repos)
    assert client.get(f"/api/user/requests/{rid}").status_code == 200        # 전제: 지우기 전엔 보인다
    assert client.post(PATH, json={"request_ids": [rid]}).status_code == 200
    expect = {
        f"/api/user/requests/{rid}": "request_not_found",
        f"/api/user/requests/{rid}/jobs": "request_not_found",
        f"/api/admin/requests/{rid}/scan-stats": "request_not_found",
        f"/api/admin/requests/{rid}/events": "request_not_found",
        f"/api/user/jobs/{jid}/artifacts": "job_not_found",
        f"/api/user/jobs/{jid}/artifacts/execution/dscan-report.json": "job_not_found",
        f"/api/user/jobs/{jid}/artifacts/execution/dscan-report.json/download": "job_not_found",
        f"/api/user/jobs/{jid}/logs?phase=execution": "job_not_found",
    }
    for path, detail in expect.items():
        r = client.get(path)
        assert (r.status_code, r.json()["detail"]) == (404, detail), path
    listed = client.get("/api/user/requests?limit=200").json()
    assert rid not in {row["request_id"] for row in listed}


def _usage_client(tmp_path):
    from dms.api.app import create_app
    db = Database.connect(f"sqlite:///{tmp_path}/u.db")
    migrate(db)
    art = tmp_path / "artifacts"
    art.mkdir()
    settings = Settings(database_url="unused", shared_token="tok-shared", admin_token="tok-admin",
                        session_secret="sess-secret", account_verification_required=False,
                        artifact_base_uri=f"file://{art}")
    return TestClient(create_app(settings, db)), db, art


def test_usage_point_disappears_and_the_previous_scan_becomes_latest(tmp_path):
    client, db, art = _usage_client(tmp_path)
    repos = client.app.state.repos
    older, older_job = _finished(repos, resource_key="u1")
    newer, newer_job = _finished(repos, resource_key="u2")
    db.execute("UPDATE data_jobs SET updated_at = '2026-01-02T00:00:00Z' WHERE job_id = :j", {"j": newer_job})
    only, _ = _finished(repos, target="solo", resource_key="u3")
    _session(client)
    rows = {r["target"]: r for r in client.get("/api/admin/usage/scan-targets").json()}
    assert rows["t"]["scan_count"] == 2 and rows["t"]["latest"]["job_id"] == newer_job
    r = client.post(PATH, json={"request_ids": [newer, only]})
    assert len(r.json()["deleted"]) == 2
    rows = {r["target"]: r for r in client.get("/api/admin/usage/scan-targets").json()}
    assert rows["t"]["scan_count"] == 1 and rows["t"]["latest"]["job_id"] == older_job
    assert "solo" not in rows                                     # 유일한 성공 scan 이었던 대상은 목록에서 빠진다


# ---- 정리 현황 ----

def test_purge_status_shape(client, session_admin):
    repos = client.app.state.repos
    rid, _ = _finished(repos)
    client.post(PATH, json={"request_ids": [rid]})
    repos.request_purges.fail(rid, reason_code="purge_no_node", interval=15)
    body = client.get("/api/admin/request-purges").json()
    assert (body["pending"], body["stalled"]) == (1, 1)
    assert body["oldest_requested_at"] is not None
    (item,) = body["items"]
    assert item["request_id"] == rid and item["stage"] == "k8s" and item["last_error"] == "purge_no_node"
    assert item["requested_by"] == "opadm" and item["attempts"] == 1


def test_list_flags_requests_that_hold_a_usage_point(client, db, session_admin):
    # 확인 창의 사용량 경고는 **잡 상태**를 따라야 한다(2026-10-09 검증 지적): 취소 경합으로 요청은 Cancelled 인데 scan
    # 잡은 Succeeded 인 요청도 사용량 분석의 지점이다. 반대로 성공 scan 잡이 없으면 지점이 아니다.
    repos = client.app.state.repos
    ok, _ = _finished(repos, target="a", resource_key="k1")
    raced, _ = _finished(repos, target="b", resource_key="k2")
    db.execute("UPDATE requests SET state = 'Cancelled' WHERE request_id = :r", {"r": raced})
    failed, _ = _finished(repos, target="c", resource_key="k3", job_state=DataJobState.FAILED)
    rm, _ = _finished(repos, op="rm", target="d", resource_key="k4")
    no_target, nt_job = _finished(repos, target="e", resource_key="k5")
    db.execute("UPDATE data_jobs SET target = NULL WHERE job_id = :j", {"j": nt_job})   # 사용량 분석 조건 밖
    rows = {r["request_id"]: r for r in client.get("/api/user/requests?limit=200").json()}
    assert {rid: rows[rid]["has_succeeded_scan"] for rid in (ok, raced, failed, rm, no_target)} == {
        ok: True, raced: True, failed: False, rm: False, no_target: False}
    assert rows[raced]["state"] == "Cancelled"
    # 사용량 분석 목록의 판정과 같은 한 벌이다.
    assert {(t["storage_name"], t["target"]) for t in repos.data_jobs.scan_targets()} == {("s1", "a"), ("s1", "b")}
