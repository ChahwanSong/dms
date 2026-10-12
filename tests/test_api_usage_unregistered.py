"""사용량 분석의 미등록 스토리지(2026-10-12 사용자 요청 두 가지).

1. 「미등록 스토리지 항목들은 스토리지 항목별로 전체 clean up」 -- GET /api/admin/usage/unregistered-storages(목록·규모)와
   POST …:purge(한 회차 정리). 삭제 몸통은 일반 작업 삭제(request_purges.delete_terminal·delete_batch)를 scan_storage= 로
   부른다 -- 여기서는 라우트 계약(세션 관리자, 409·503, 대상 범위, 배치째·섞인 배치 거부, 회차·remaining, 감사)과 트랜잭션
   안 재판정을 고정한다.
2. 「사용량 csv 두번째 행부터 backend type, mount path 등 누락」 -- 내보내기 행마다 storage(등록이면 지금 값, 미등록이면
   감사 기록의 마지막 설정)."""
import os

import pytest

from dms.domain import DataJobState, RequestState

TOKEN = {"Authorization": "Bearer tok-shared"}
OLD = "2026-01-01T00:00:00Z"
LIST = "/api/admin/usage/unregistered-storages"
PURGE = "/api/admin/usage/unregistered-storages:purge"


def _session(client, name="opadm", role="admin"):
    client.app.state.repos.accounts.create(name, "p", role, actor="t")
    assert client.post("/api/auth/login", json={"username": name, "password": "p"}).status_code == 200
    return name


@pytest.fixture
def session_admin(client):
    return _session(client)


def _age(db, rid):
    db.execute("UPDATE requests SET updated_at = :t WHERE request_id = :r", {"t": OLD, "r": rid})
    db.execute("UPDATE data_jobs SET updated_at = :t WHERE request_id = :r", {"t": OLD, "r": rid})


def _job(repos, *, storage, target, op="scan", state=DataJobState.SUCCEEDED, batch_id=None, age=True):
    rid = repos.requests.create(operation=op, requester_id="opadm", actor="opadm",
                                resource_key=f"k:{storage}:{target}:{op}:{batch_id}",
                                payload={"storage": storage, "target": target, "run_as_root": True}, priority="mid",
                                auth_method="session", batch_id=batch_id)
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation=op, priority="mid", storage_name=storage, target=target, options={},
        tool="dscan" if op == "scan" else "drm", precondition={}, actor="planner",
        worker_pool={"identity": {"username": "opadm", "uid": os.getuid(), "gid": os.getgid(), "privileged": False}})
    repos.data_jobs.set_job_state(jid, state, actor="stepper")
    if state in (DataJobState.SUCCEEDED, DataJobState.FAILED, DataJobState.CANCELLED):
        repos.requests.finalize_from_job(rid, state, actor="stepper")
    if age:
        _age(repos.db, rid)
    return rid, jid


def _batch(repos, *, storage, n=2, item_storage=None):
    """종단 scan 배치(자식 n개, 잡은 storage). item_storage 를 주면 남은 항목의 payload 가 그 스토리지(종단 배치의 항목
    교체로 옛 자식과 지금 항목의 스토리지가 갈린 모양)."""
    istore = item_storage or storage
    bid = repos.batches.create(operation="scan", requester_id="opadm", actor="opadm", max_concurrency=1, options={},
                               note=None, items=[{"storage": istore, "target": f"t{i}"} for i in range(n)],
                               status="Completed", name="옛 배치", auth_method="session")
    kids = []
    for i in range(n):
        rid, jid = _job(repos, storage=storage, target=f"{bid[:6]}-t{i}", batch_id=bid)
        if item_storage is None:
            repos.db.execute("UPDATE batch_items SET status = 'Succeeded', request_id = :r "
                             "WHERE batch_id = :b AND seq = :s", {"r": rid, "b": bid, "s": i})
        kids.append((rid, jid))
    return bid, kids


def _register(repos, name, *, mount="/old", root="/old/managed", backend="gpfs"):
    return repos.storages.create(storage_name=name, mount_path=mount, managed_root=root, backend_type=backend,
                                 actor="t")


def _usage_names(client):
    return {r["storage_name"] for r in client.get("/api/admin/usage/scan-targets").json()}


# ---- 목록 ----

def test_list_shows_only_unregistered_names_with_scale_and_last_known(client, session_admin):
    repos = client.app.state.repos
    _register(repos, "s1", mount="/s1", root="/s1/m", backend="cephfs")
    _register(repos, "old", mount="/old", root="/old/managed", backend="gpfs")
    repos.storages.delete("old", actor="t")                              # 감사 delete 행 = 마지막 설정
    _job(repos, storage="s1", target="a")
    _job(repos, storage="old", target="a")
    _job(repos, storage="old", target="a")
    _job(repos, storage="old", target="b")
    _job(repos, storage="old", target="c", state=DataJobState.FAILED)  # 사용량엔 없지만 정리 대상
    _job(repos, storage="old", target="d", op="rm")                    # 정리 대상 아님
    bid, kids = _batch(repos, storage="old", n=2)
    _job(repos, storage="ghost", target="x")                           # 감사 기록 없는 미등록
    rows = {r["storage_name"]: r for r in client.get(LIST).json()}
    assert set(rows) == {"old", "ghost"}
    old = rows["old"]
    # 사용량 기준(성공 scan): a×2, b, 배치 자식 2(서로 다른 대상)
    assert (old["target_count"], old["succeeded_scan_count"]) == (4, 5)
    assert old["single_count"] == 4                                    # 성공 3 + 실패 1(rm 제외)
    assert old["batches"] == [{"batch_id": bid, "request_count": 2}]
    assert old["mixed_batches"] == []
    assert old["request_count"] == 6
    assert old["last_known"] == {"backend_type": "gpfs", "mount_path": "/old", "managed_root": "/old/managed"}
    assert rows["ghost"]["last_known"] is None


def test_list_keeps_names_with_only_failed_scans_and_separates_mixed_batches(client, session_admin):
    """정리는 실패·취소 scan 도 지운다 -- 성공 scan 이 다 지워져도 남은 것이 있으면 행이 남아 다시 정리할 수 있어야
    한다(리뷰: 성공 기준 목록이면 일시적 사유로 건너뛴 실패 scan 이 남은 채 행과 버튼이 사라졌다). 다른 스토리지 자식이 섞인
    배치는 두 이름 아래에 같은 수로 세어 「지울 수」를 부풀렸다 -- mixed_batches 로 따로."""
    repos = client.app.state.repos
    failed, _ = _job(repos, storage="old", target="a", state=DataJobState.FAILED)
    bid, kids = _batch(repos, storage="old", n=1)
    other, _ = _job(repos, storage="s9", target="z", batch_id=bid)          # 같은 배치의 다른 스토리지 자식
    rows = {r["storage_name"]: r for r in client.get(LIST).json()}
    assert set(rows) == {"old", "s9"}
    old = rows["old"]
    assert (old["target_count"], old["succeeded_scan_count"], old["last_scan_at"]) == (1, 1, OLD)
    assert (old["single_count"], old["batches"], old["request_count"]) == (1, [], 1)
    assert old["mixed_batches"] == [{"batch_id": bid, "request_count": 2}]
    assert rows["s9"]["mixed_batches"] == [{"batch_id": bid, "request_count": 2}]
    assert rows["s9"]["request_count"] == 0
    # 실패 scan 만 남은 이름
    client.post(PURGE, json={"storage_name": "old"})
    rows = {r["storage_name"]: r for r in client.get(LIST).json()}
    assert rows["old"]["single_count"] == 0 and rows["old"]["mixed_batches"] != []
    assert repos.requests.get(failed) is None


def test_list_and_purge_include_jobless_scan_requests_of_that_storage(client, db, session_admin):
    """지운 스토리지로 낸 scan 은 planner 가 storage_missing 으로 거부해 잡 없이 끝난다 -- payload storage 로 그
    스토리지의 기록이다(확인 창: 「성공·실패·취소된 scan 을 모두 지웁니다」). 잡이 없는 이름만 있는 경우(등록된 적 없는 오타
    이름)는 목록에 올리지 않는다."""
    repos = client.app.state.repos
    _job(repos, storage="old", target="a")
    jobless = repos.requests.create(operation="scan", requester_id="opadm", actor="opadm", resource_key="k:jl",
                                    payload={"storage": "old", "target": "b"}, priority="mid", auth_method="session")
    repos.requests.set_state(jobless, RequestState.REJECTED, actor="planner")
    typo = repos.requests.create(operation="scan", requester_id="opadm", actor="opadm", resource_key="k:typo",
                                 payload={"storage": "nevr", "target": "b"}, priority="mid", auth_method="session")
    repos.requests.set_state(typo, RequestState.REJECTED, actor="planner")
    for rid in (jobless, typo):
        db.execute("UPDATE requests SET updated_at = :t WHERE request_id = :r", {"t": OLD, "r": rid})
    rows = {r["storage_name"]: r for r in client.get(LIST).json()}
    assert set(rows) == {"old"}
    assert rows["old"]["single_count"] == 2
    r = client.post(PURGE, json={"storage_name": "old"}).json()
    assert jobless in {d["request_id"] for d in r["deleted"]}
    assert repos.requests.get(jobless) is None and repos.requests.get(typo) is not None
    # 다음 회차(잡 보유 요청이 이미 없다)도 같은 이름의 잡 없는 요청을 찾는다
    j2 = repos.requests.create(operation="scan", requester_id="opadm", actor="opadm", resource_key="k:jl2",
                               payload={"storage": "old", "target": "c"}, priority="mid", auth_method="session")
    repos.requests.set_state(j2, RequestState.REJECTED, actor="planner")
    db.execute("UPDATE requests SET updated_at = :t WHERE request_id = :r", {"t": OLD, "r": j2})
    assert repos.request_purges.scan_storage_selection("old")["singles"] == [j2]


def test_list_is_admin_only(client):
    _session(client, "alice", "user")
    r = client.get(LIST)
    assert (r.status_code, r.json()["detail"]) == (403, "admin_required")


# ---- 내보내기 스토리지 정보(CSV 열 누락) ----

def test_export_rows_carry_storage_info_registered_last_known_and_unknown(client, session_admin):
    repos = client.app.state.repos
    _register(repos, "s1", mount="/s1", root="/s1/m", backend="cephfs")
    _register(repos, "old", mount="/old", root="/old/v1", backend="gpfs")
    repos.storages.update("old", mount_path="/old", managed_root="/old/v2", backend_type="wekafs", enabled=True,
                          actor="t")
    repos.storages.delete("old", actor="t")
    _job(repos, storage="s1", target="a")
    _job(repos, storage="old", target="b")
    _job(repos, storage="ghost", target="c")
    rows = {r["storage_name"]: r["storage"] for r in client.get("/api/admin/usage/export").json()["rows"]}
    assert rows["s1"] == {"registered": True, "source": "registered", "backend_type": "cephfs",
                          "mount_path": "/s1", "managed_root": "/s1/m"}
    # 마지막 설정 = 삭제 직전(수정 뒤) 값
    assert rows["old"] == {"registered": False, "source": "last_known", "backend_type": "wekafs",
                           "mount_path": "/old", "managed_root": "/old/v2"}
    assert rows["ghost"] == {"registered": False, "source": None, "backend_type": None, "mount_path": None,
                             "managed_root": None}


def test_last_known_ignores_history_purge_audit_and_garbage(client, db):
    repos = client.app.state.repos
    _register(repos, "old", mount="/old", root="/old/m", backend="gpfs")
    repos.storages.delete("old", actor="t")
    repos.request_purges.audit_scan_history_purge("old", actor="t", summary={"deleted_request_ids": []})
    assert repos.storages.last_known_config("old")["managed_root"] == "/old/m"
    # 변조된 감사 행(값이 문자열이 아님·깨진 JSON)은 값을 지어내지 않는다
    db.execute("""INSERT INTO audit_log (mutation_class, operation, target_key, actor, before_state, after_state, at)
                  VALUES ('storage', 'delete', 'old', 't', '{"mount_path": ["x"], "managed_root": 1}', NULL, :at)""",
               {"at": OLD})
    assert repos.storages.last_known_config("old") is None
    db.execute("""INSERT INTO audit_log (mutation_class, operation, target_key, actor, before_state, after_state, at)
                  VALUES ('storage', 'delete', 'old', 't', '[[[[', NULL, :at)""", {"at": OLD})
    assert repos.storages.last_known_config("old") is None


# ---- 정리: 권한·빠른 거절 ----

def test_purge_regular_user_gets_admin_required(client):
    repos = client.app.state.repos
    rid, _ = _job(repos, storage="old", target="a")
    _session(client, "alice", "user")
    r = client.post(PURGE, json={"storage_name": "old"})
    assert (r.status_code, r.json()["detail"]) == (403, "admin_required")
    assert repos.requests.get(rid) is not None


def test_purge_shared_token_gets_admin_session_required(client, db):
    repos = client.app.state.repos
    rid, _ = _job(repos, storage="old", target="a")
    r = client.post(PURGE, json={"storage_name": "old"}, headers=TOKEN)
    assert (r.status_code, r.json()["detail"]) == (403, "admin_session_required")
    assert repos.requests.get(rid) is not None


def test_purge_registered_storage_is_409(client, session_admin):
    repos = client.app.state.repos
    _register(repos, "s1")
    rid, _ = _job(repos, storage="s1", target="a")
    r = client.post(PURGE, json={"storage_name": "s1"})
    assert (r.status_code, r.json()["detail"]) == (409, "storage_registered")
    assert repos.requests.get(rid) is not None


def test_purge_blocked_in_maintenance(client, session_admin):
    repos = client.app.state.repos
    rid, _ = _job(repos, storage="old", target="a")
    assert client.put("/api/admin/control-state",
                      json={"maintenance": True, "drain": False, "reason": None}).status_code == 200
    r = client.post(PURGE, json={"storage_name": "old"})
    assert (r.status_code, r.json()["detail"]) == (503, "maintenance_mode")
    assert repos.requests.get(rid) is not None


@pytest.mark.parametrize("body", [{}, {"storage_name": ""}, {"storage_name": "x" * 513}])
def test_purge_body_validation(client, session_admin, body):
    assert client.post(PURGE, json=body).status_code == 422


# ---- 정리: 범위·결과 ----

def test_purge_deletes_scan_history_of_that_storage_only(client, db, session_admin):
    repos = client.app.state.repos
    _register(repos, "s1")
    keep_s1, _ = _job(repos, storage="s1", target="a")
    ok1, _ = _job(repos, storage="old", target="a")
    ok2, _ = _job(repos, storage="old", target="b")
    failed, _ = _job(repos, storage="old", target="c", state=DataJobState.FAILED)
    keep_rm, _ = _job(repos, storage="old", target="d", op="rm")
    bid, kids = _batch(repos, storage="old", n=2)
    assert _usage_names(client) == {"s1", "old"}
    r = client.post(PURGE, json={"storage_name": "old"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert {d["request_id"] for d in body["deleted"]} == {ok1, ok2, failed}
    assert [d["batch_id"] for d in body["deleted_batches"]] == [bid]
    assert sorted(body["deleted_batches"][0]["request_ids"]) == sorted(k[0] for k in kids)
    assert (body["skipped"], body["skipped_batches"]) == ([], [])
    assert body["remaining"] == {"requests": 0, "batches": 0}
    assert body["purge_pending"] == 5
    # 사용량·목록에서 사라지고, 다른 스토리지 scan 과 같은 스토리지의 rm 은 남는다
    assert _usage_names(client) == {"s1"}
    assert client.get(LIST).json() == []
    for rid in (ok1, ok2, failed, *(k[0] for k in kids)):
        assert repos.requests.get(rid) is None
    assert repos.requests.get(keep_s1) is not None and repos.requests.get(keep_rm) is not None
    assert repos.batches.get(bid) is None
    # 감사: 요청·배치마다(삭제 몸통) + 회차 묶음 1행
    rows = db.query("SELECT mutation_class, target_key, actor FROM audit_log "
                    "WHERE operation IN ('delete', 'purge') AND mutation_class <> 'storage'")
    assert {(r["mutation_class"], r["target_key"]) for r in rows} == (
        {("request", x) for x in (ok1, ok2, failed, *(k[0] for k in kids))}
        | {("batch", bid), ("storage_history", "old")})
    assert {r["actor"] for r in rows} == {"opadm"}
    summary = db.query_one("SELECT before_state FROM audit_log WHERE mutation_class = 'storage_history'")
    assert '"deleted_batch_request_count":2' in summary["before_state"].replace(" ", "")


def test_purge_skips_mixed_batch_and_reports(client, db, session_admin):
    repos = client.app.state.repos
    # 옛 자식은 old, 지금 항목은 s1(종단 배치의 항목 교체) -- 배치째 지우면 s1 의 배치 정의가 사라진다
    bid, kids = _batch(repos, storage="old", n=2, item_storage="s1")
    r = client.post(PURGE, json={"storage_name": "old"}).json()
    assert r["deleted_batches"] == []
    assert r["skipped_batches"] == [{"batch_id": bid, "reason": "storage_history_batch_mixed", "request_id": None}]
    assert r["remaining"] == {"requests": 0, "batches": 1}
    assert repos.batches.get(bid) is not None
    assert all(repos.requests.get(rid) is not None for rid, _ in kids)


def test_purge_batch_with_child_of_other_operation_is_mixed(client, db, session_admin):
    repos = client.app.state.repos
    bid, kids = _batch(repos, storage="old", n=1)
    other, _ = _job(repos, storage="old", target="z", op="rm", batch_id=bid)     # 변조·비정상: scan 배치의 rm 자식
    r = client.post(PURGE, json={"storage_name": "old"}).json()
    assert r["skipped_batches"] == [{"batch_id": bid, "reason": "storage_history_batch_mixed", "request_id": other}]
    assert repos.batches.get(bid) is not None


def test_purge_rounds_and_remaining(client, session_admin, monkeypatch):
    import dms.api.routes_usage as ru
    monkeypatch.setattr(ru, "MAX_DELETE", 2)
    repos = client.app.state.repos
    rids = [_job(repos, storage="old", target=f"t{i}")[0] for i in range(5)]
    seen = []
    for expect_left in (3, 1, 0):
        r = client.post(PURGE, json={"storage_name": "old"}).json()
        seen += [d["request_id"] for d in r["deleted"]]
        assert r["remaining"] == {"requests": expect_left, "batches": 0}
        assert r["remaining_untried"] == {"requests": expect_left, "batches": 0}
    assert sorted(seen) == sorted(rids)
    # 지울 것이 없는 회차: 200, 빈 결과, 감사 행 없음
    r = client.post(PURGE, json={"storage_name": "old"})
    assert r.status_code == 200
    assert (r.json()["deleted"], r.json()["remaining"]) == ([], {"requests": 0, "batches": 0})


def test_purge_excludes_previously_skipped_so_later_rows_are_reached(client, session_admin, monkeypatch):
    import dms.api.routes_usage as ru
    monkeypatch.setattr(ru, "MAX_DELETE", 1)
    monkeypatch.setattr(ru, "MAX_DELETE_BATCHES", 1)
    repos = client.app.state.repos
    stuck, _ = _job(repos, storage="old", target="a", age=False)        # 앞쪽의 거부(조용한 창)
    later, _ = _job(repos, storage="old", target="b")
    mixed, _ = _batch(repos, storage="old", n=1, item_storage="s1")     # 앞쪽의 영구 거부
    pure, _ = _batch(repos, storage="old", n=1)
    r1 = client.post(PURGE, json={"storage_name": "old"}).json()
    assert [s["request_id"] for s in r1["skipped"]] == [stuck]
    assert [s["batch_id"] for s in r1["skipped_batches"]] == [mixed]
    r2 = client.post(PURGE, json={"storage_name": "old", "exclude_request_ids": [stuck],
                                  "exclude_batch_ids": [mixed]}).json()
    assert [d["request_id"] for d in r2["deleted"]] == [later]
    assert [d["batch_id"] for d in r2["deleted_batches"]] == [pure]
    assert r2["remaining"] == {"requests": 1, "batches": 1}            # 건너뛴 것은 남은 수에 그대로 센다


def test_remaining_untried_ignores_excluded_ids_that_vanished(client, session_admin, monkeypatch):
    """리뷰: 포탈이 건너뛴 id 의 **개수**와 remaining 을 비교해 멈추면, 건너뛴 뒤 다른 곳에서 지워진 id 만큼 시도하지 않은
    기록을 남긴 채 멈췄다. 서버가 「다음 회차가 시도할 수」를 직접 센다."""
    import dms.api.routes_usage as ru
    monkeypatch.setattr(ru, "MAX_DELETE", 1)
    repos = client.app.state.repos
    a, _ = _job(repos, storage="old", target="a", age=False)              # 조용한 창 → 건너뜀
    b, _ = _job(repos, storage="old", target="b")
    r = client.post(PURGE, json={"storage_name": "old"}).json()
    assert [s_["request_id"] for s_ in r["skipped"]] == [a]
    assert (r["remaining"], r["remaining_untried"]) == ({"requests": 2, "batches": 0}, {"requests": 1, "batches": 0})
    _age(repos.db, a)
    repos.request_purges.delete_terminal(a, actor="other-admin", artifact_base=None, quiet_seconds=0)  # 다른 곳에서 지움
    r = client.post(PURGE, json={"storage_name": "old", "exclude_request_ids": [a]}).json()
    assert [d["request_id"] for d in r["deleted"]] == [b]
    assert r["remaining_untried"] == {"requests": 0, "batches": 0}


def test_purge_skips_recently_finished_and_active(client, session_admin):
    repos = client.app.state.repos
    fresh, _ = _job(repos, storage="old", target="a", age=False)
    running, _ = _job(repos, storage="old", target="b", state=DataJobState.RUNNING, age=False)
    r = client.post(PURGE, json={"storage_name": "old"}).json()
    assert {(s["request_id"], s["reason"]) for s in r["skipped"]} == {
        (fresh, "request_recently_finished"), (running, "request_not_deletable")}
    assert r["remaining"] == {"requests": 2, "batches": 0}


# ---- 트랜잭션 안 재판정(저장소) ----

def test_repo_rejects_when_storage_registered_inside_the_transaction(client):
    repos = client.app.state.repos
    rid, _ = _job(repos, storage="old", target="a")
    bid, _ = _batch(repos, storage="old", n=1)
    _register(repos, "old")                    # 대상을 고른 뒤 같은 이름으로 다시 등록된 경합
    r = repos.request_purges.delete_terminal(rid, actor="t", artifact_base=None, quiet_seconds=60,
                                             scan_storage="old")
    assert r == {"deleted": False, "reason": "storage_registered"}
    r = repos.request_purges.delete_batch(bid, expected_request_count=1, actor="t", artifact_base=None,
                                          quiet_seconds=60, max_children=1000, scan_storage="old")
    assert (r["deleted"], r["reason"]) == (False, "storage_registered")
    assert repos.requests.get(rid) is not None and repos.batches.get(bid) is not None


def test_repo_rejects_requests_that_are_not_that_storages_scan(client):
    repos = client.app.state.repos
    other, _ = _job(repos, storage="s1", target="a")
    rm, _ = _job(repos, storage="old", target="b", op="rm")
    for rid in (other, rm):
        r = repos.request_purges.delete_terminal(rid, actor="t", artifact_base=None, quiet_seconds=60,
                                                 scan_storage="old")
        assert r == {"deleted": False, "reason": "storage_history_mismatch"}
        assert repos.requests.get(rid) is not None
    # scan_storage 없이 부르면 기존 동작 그대로(일반 작업 삭제)
    assert repos.request_purges.delete_terminal(rm, actor="t", artifact_base=None, quiet_seconds=60)["deleted"]


def test_selection_puts_malformed_batch_ids_with_singles(client, db):
    repos = client.app.state.repos
    rid, _ = _job(repos, storage="old", target="a")
    db.execute("UPDATE requests SET batch_id = '' WHERE request_id = :r", {"r": rid})
    sel = repos.request_purges.scan_storage_selection("old")
    assert (sel["singles"], sel["batches"]) == ([rid], {})
    r = repos.request_purges.delete_terminal(rid, actor="t", artifact_base=None, quiet_seconds=60,
                                             scan_storage="old")
    assert r == {"deleted": False, "reason": "batch_child_not_deletable"}
