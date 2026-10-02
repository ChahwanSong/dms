"""종단 배치의 실행 설정 변경(PATCH /api/admin/batches/{id}/execution, 사용자 요청 2026-10-02).

배치를 취소(또는 완료)한 뒤 재실행할 때 동시 실행 상한·우선순위·노드 수·노드당 프로세스 수·연산 옵션을 바꿀 수
있어야 한다. 계약: 종단(Completed/Cancelled)만(활성·PreviewReady 409 batch_settings_locked), 생성과 같은 특권
게이트·검증, 키 부재 = 무접촉, options 는 통째 교체, 바뀐 키만 쓰고 audit_log 에 남긴다, 다음 재실행의 자식부터
새 값이 적용된다(orchestrator 가 materialize 시점에 배치 행을 읽는다)."""
import json

import pytest

from dms.batch_orchestrator import BatchOrchestrator


def _admin(client):  # test_api_batches 와 같은 세션 로그인(admin 은 기본 특권 allowlist 안)
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})


SCAN = {"operation": "scan", "max_concurrency": 2, "options": {"broken_limit": 50}, "note": None,
        "items": [{"storage": "s1", "target": "t0"}, {"storage": "s1", "target": "t1"}]}
SYNC = {"operation": "sync", "max_concurrency": 1, "options": {"delete": True, "chown": "1000:1000"},
        "note": None, "items": [{"source_storage": "s1", "source": "a",
                                 "destination_storage": "s2", "destination": "b"}]}


def _batch(client, body=SCAN, *, status="Completed"):
    repo = client.app.state.repos.batches
    bid = client.post("/api/admin/batches", json=body).json()["batch_id"]
    for it in repo.list_items(bid):
        repo.set_item_status(bid, it["seq"], "Succeeded")
    repo.set_status(bid, status)
    return bid


def _audits(db):
    return db.query("SELECT target_key, actor, before_state, after_state FROM audit_log "
                    "WHERE mutation_class = 'batch' AND operation = 'execution_settings' ORDER BY id")


def _patch(client, bid, body):
    return client.patch(f"/api/admin/batches/{bid}/execution", json=body)


@pytest.mark.parametrize("status", ["Completed", "Cancelled"])
def test_terminal_batch_settings_change_all_fields(client, db, status):
    _admin(client)
    bid = _batch(client, status=status)
    r = _patch(client, bid, {"max_concurrency": 5, "priority": "low", "node_count": 3,
                             "procs_per_node": 2, "options": {"broken_limit": 7, "verbose": True}})
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["max_concurrency"], got["priority"], got["node_count"], got["procs_per_node"]) == (5, "low", 3, 2)
    assert got["options"] == {"broken_limit": 7, "verbose": True}
    assert got["status"] == status                     # 설정 변경은 재실행이 아니다(상태 무접촉)
    b = client.app.state.repos.batches.get(bid)
    assert b["options"] == {"broken_limit": 7, "verbose": True} and b["node_count"] == 3
    # 감사: 바뀐 키만 before/after, 실행자 기록
    [a] = _audits(db)
    assert a["target_key"] == bid and a["actor"] == "admin"
    assert json.loads(a["before_state"]) == {"max_concurrency": 2, "priority": None, "node_count": None,
                                             "procs_per_node": None, "options": {"broken_limit": 50}}
    assert json.loads(a["after_state"]) == {"max_concurrency": 5, "priority": "low", "node_count": 3,
                                            "procs_per_node": 2, "options": {"broken_limit": 7, "verbose": True}}


@pytest.mark.parametrize("status", ["Running", "Previewing", "PreviewReady"])
def test_active_or_preview_ready_batch_is_locked(client, db, status):
    # 활성 배치는 자식이 이미 옛값으로 materialize 됐고, PreviewReady 는 자식이 옛 옵션으로 미리보기를 끝냈다 --
    # 바꾸면 "일부는 옛값, 일부는 새값"이다. 취소 후 바꾸는 것이 동선.
    _admin(client)
    bid = _batch(client, status=status)
    r = _patch(client, bid, {"node_count": 3})
    assert r.status_code == 409 and r.json()["detail"] == "batch_settings_locked"
    assert client.app.state.repos.batches.get(bid)["node_count"] is None
    assert _audits(db) == []


def test_partial_update_touches_only_sent_keys_and_null_resets_to_policy_default(client):
    _admin(client)
    bid = client.post("/api/admin/batches", json={**SCAN, "priority": "high", "node_count": 4,
                                                   "procs_per_node": 8}).json()["batch_id"]
    client.app.state.repos.batches.set_status(bid, "Cancelled")
    r = _patch(client, bid, {"node_count": None, "procs_per_node": 2})
    assert r.status_code == 200, r.text
    b = client.app.state.repos.batches.get(bid)
    # null = 정책 기본으로 되돌리기(null≠0), 보내지 않은 키(priority·mc·options)는 그대로
    assert b["node_count"] is None and b["procs_per_node"] == 2
    assert b["priority"] == "high" and b["max_concurrency"] == 2 and b["options"] == {"broken_limit": 50}


def test_options_are_replaced_whole_not_merged(client):
    # 병합이면 delete·chown 을 끄는(키를 빼는) 표현이 없다
    _admin(client)
    bid = _batch(client, SYNC, status="Cancelled")
    r = _patch(client, bid, {"options": {"open_noatime": True}})
    assert r.status_code == 200, r.text
    assert client.app.state.repos.batches.get(bid)["options"] == {"open_noatime": True}


def test_no_change_writes_nothing(client, db):
    # 같은 값 재전송 = 쓰기 없음(updated_at 만 튀는 것도 거짓 변경 기록) -- 감사 행도 없다
    _admin(client)
    bid = _batch(client)
    before = client.app.state.repos.batches.get(bid)["updated_at"]
    r = _patch(client, bid, {"max_concurrency": 2, "options": {"broken_limit": 50}})
    assert r.status_code == 200
    assert client.app.state.repos.batches.get(bid)["updated_at"] == before
    assert _audits(db) == []
    # 빈 바디도 같은 무접촉
    assert _patch(client, bid, {}).status_code == 200 and _audits(db) == []


@pytest.mark.parametrize("body, code", [
    ({"max_concurrency": 0}, "invalid_max_concurrency"),
    ({"max_concurrency": 65}, "invalid_max_concurrency"),
    ({"max_concurrency": None}, "invalid_max_concurrency"),
    ({"priority": "urgent"}, "invalid_priority"),
    ({"priority": ""}, "invalid_priority"),
    ({"node_count": 0}, "invalid_node_count"),
    ({"procs_per_node": 2000}, "invalid_procs_per_node"),
    ({"options": None}, "invalid_option"),
    ({"options": {"bogus": 1}}, "unknown_option"),
    ({"options": {"verbose": True, "quiet": True}}, "invalid_option"),
    ({"options": {"broken_limit": -1}}, "invalid_option"),
])
def test_scan_settings_validation(client, db, body, code):
    _admin(client)
    bid = _batch(client)
    r = _patch(client, bid, body)
    assert r.status_code == 422 and r.json()["detail"] == code
    b = client.app.state.repos.batches.get(bid)
    assert b["max_concurrency"] == 2 and b["options"] == {"broken_limit": 50}     # 거부는 아무것도 안 쓴다
    assert _audits(db) == []


@pytest.mark.parametrize("options, code", [
    ({"chown": "alice:staff"}, "chown_name_not_supported"),
    ({"chmod": "X99"}, "invalid_option"),
    ({"bufsize": 1}, "invalid_option"),
])
def test_sync_options_use_creation_rules(client, options, code):
    _admin(client)
    bid = _batch(client, SYNC)
    r = _patch(client, bid, {"options": options})
    assert r.status_code == 422 and r.json()["detail"] == code


def test_legacy_name_chown_batch_can_be_fixed_and_rerun(client):
    # 옛 규칙(chown 이름)이 남은 배치는 재실행이 422 였다(_reject_stale_options) -- 실행 설정에서 숫자로 고치면
    # 다시 돌릴 수 있다. 생성은 이름을 거부하므로 저장값을 직접 옛 모양으로 만든다(DB 가 신뢰 경계).
    _admin(client)
    bid = _batch(client, SYNC, status="Cancelled")
    client.app.state.repos.batches._db.execute("UPDATE batches SET options = :o WHERE batch_id = :b",
                     {"o": json.dumps({"chown": "alice:staff"}), "b": bid})
    assert client.post(f"/api/admin/batches/{bid}:rescan").json()["detail"] == "chown_name_not_supported"
    # 다른 키만 바꾸는 요청도 남은 옛 옵션을 다시 검증한다 -- 결과로 남을 설정 전체가 지금 규칙에 맞아야 한다
    r = _patch(client, bid, {"node_count": 2})
    assert r.status_code == 422 and r.json()["detail"] == "chown_name_not_supported"
    assert _patch(client, bid, {"options": {"chown": "1000:1000"}}).status_code == 200
    r = client.post(f"/api/admin/batches/{bid}:rescan")
    assert r.status_code == 200 and r.json()["status"] == "Previewing"


class _S:  # orchestrator 최소 settings 더미(test_batch_orchestrator_scan 관례)
    preview_ttl_seconds = 900


def test_new_settings_apply_to_children_of_the_next_rerun(client):
    # 이 기능의 요점: 바꾼 값이 다음 재실행의 자식에 실린다(orchestrator._materialize 가 배치 행을 읽는다).
    _admin(client)
    repos = client.app.state.repos
    bid = _batch(client, status="Cancelled")
    r = _patch(client, bid, {"max_concurrency": 1, "priority": "low", "node_count": 3,
                             "procs_per_node": 2, "options": {"broken_limit": 7}})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/admin/batches/{bid}:rescan").status_code == 200
    BatchOrchestrator(repos, settings=_S()).run_once()
    mats = [it for it in repos.batches.list_items(bid) if it["status"] == "Materialized"]
    assert len(mats) == 1                                   # 새 동시 실행 상한 1
    req = repos.requests.get(mats[0]["request_id"])
    assert req["priority"] == "low"
    assert req["payload"]["node_count"] == 3 and req["payload"]["procs_per_node"] == 2
    assert req["payload"]["options"]["broken_limit"] == 7


def test_requires_batch_privilege_gate(client):
    # 생성과 같은 3중 게이트 -- allowlist(기본 root·admin) 밖 관리자는 세션이어도 403. 옵션(delete·chown)·노드 수는
    # root 재실행의 행동을 바꾸므로 생성 게이트를 통과 못 하는 주체가 바꿀 수 있으면 우회로가 된다.
    _admin(client)
    bid = _batch(client)
    client.app.state.repos.accounts.create("ops2", "pw", "admin", actor="t")
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": "ops2", "password": "pw"}).status_code == 200
    r = _patch(client, bid, {"node_count": 3})
    assert r.status_code == 403 and r.json()["detail"] == "privileged_not_authorized"
    assert client.app.state.repos.batches.get(bid)["node_count"] is None


def test_owner_operation_items_are_not_changeable(client):
    # 실행 신원·연산·항목은 생성 시점 사실 -- 바디에 실어도 버려진다(pydantic 모델 밖 키)
    _admin(client)
    bid = _batch(client)
    r = _patch(client, bid, {"owner_username": "victim", "operation": "sync",
                             "items": [{"storage": "s1", "target": "evil"}], "node_count": 2})
    assert r.status_code == 200
    b = client.app.state.repos.batches.get(bid)
    assert b["owner_username"] is None and b["operation"] == "scan" and b["node_count"] == 2
    assert [it["payload"]["target"] for it in client.app.state.repos.batches.list_items(bid)] == ["t0", "t1"]


def test_missing_batch_404_and_maintenance_503(client):
    _admin(client)
    r = _patch(client, "nope", {"node_count": 2})
    assert r.status_code == 404 and r.json()["detail"] == "batch_not_found"
    bid = _batch(client)
    client.put("/api/admin/control-state", json={"maintenance": True, "drain": False, "reason": None})
    r = _patch(client, bid, {"node_count": 2})
    assert r.status_code == 503 and r.json()["detail"] == "maintenance_mode"


def test_settings_change_allowed_on_terminal_batch_without_items(client):
    # 항목을 전부 지운 종단 배치도 설정은 바꿀 수 있다(항목 규칙 empty_batch 는 설정 변경의 관심사가 아니다)
    _admin(client)
    bid = _batch(client)
    for seq in (0, 1):
        assert client.delete(f"/api/admin/batches/{bid}/items/{seq}").status_code == 200
    assert _patch(client, bid, {"node_count": 2}).status_code == 200


def test_repo_guard_refuses_when_batch_became_active(db):
    # 라우트가 종단을 본 뒤 :rescan 이 배치를 다시 돌리기 시작한 경합 -- SQL 가드가 0 행 = False, 쓰기·감사 없음
    from dms.repositories import Repositories
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin", max_concurrency=2,
                               options={}, note=None, items=[{"storage": "s1", "target": "a"}],
                               status="Running")
    assert repos.batches.update_execution_settings(bid, {"node_count": 3}, before={"node_count": None},
                                                   actor="admin") is False
    assert repos.batches.get(bid)["node_count"] is None and _audits(db) == []
    repos.batches.set_status(bid, "Cancelled")
    assert repos.batches.update_execution_settings(bid, {"node_count": 3}, before={"node_count": None},
                                                   actor="admin") is True
    assert repos.batches.get(bid)["node_count"] == 3


@pytest.mark.parametrize("fields", [{}, {"owner_username": "x"}, {"status": "Running"}])
def test_repo_rejects_fields_outside_settings_columns(db, fields):
    # 컬럼명이 SQL 로 조립되므로 허용 목록 밖 키(또는 빈 갱신)는 조용히 무시하지 않고 끊는다
    from dms.repositories import Repositories
    with pytest.raises(ValueError):
        Repositories(db).batches.update_execution_settings("b", fields, before={}, actor="a")


# --- 적대적 리뷰(2026-10-02) 반영 ---

def _failed_sync_batch(client):
    repo = client.app.state.repos.batches
    bid = client.post("/api/admin/batches", json=SYNC).json()["batch_id"]
    repo.set_item_status(bid, 0, "Failed")
    repo.bump_counts(bid, failed=1)
    repo.set_status(bid, "Completed")
    return bid


@pytest.mark.parametrize("call", [
    lambda c, bid: c.post(f"/api/admin/batches/{bid}:rerun-failed"),
    lambda c, bid: c.post(f"/api/admin/batches/{bid}:rescan"),
    lambda c, bid: c.post(f"/api/admin/batches/{bid}/items:rerun", json={"seqs": [0]}),
], ids=["rerun-failed", "rescan", "items-rerun"])
def test_sync_rerun_after_option_change_goes_through_preview(client, call):
    # 바꾼 sync 옵션(delete 등)은 root 로 돈다 -- 어느 재실행 경로든 배치 미리보기 → 운영자 확인(:confirm)을 거쳐야 한다.
    # 예전 :rerun-failed 는 무조건 Running 이라 orchestrator 가 자식 미리보기를 자동 확인해 곧장 실행했다.
    _admin(client)
    repos = client.app.state.repos
    bid = _failed_sync_batch(client)
    assert _patch(client, bid, {"options": {"delete": True, "open_noatime": True}}).status_code == 200
    r = call(client, bid)
    assert r.status_code == 200 and r.json()["status"] == "Previewing"
    assert repos.batches.get(bid)["status"] == "Previewing"
    BatchOrchestrator(repos, settings=_S()).run_once()
    [it] = repos.batches.list_items(bid)
    assert it["status"] == "Materialized"
    assert repos.requests.get(it["request_id"])["payload"]["options"]["delete"] is True


def test_rerun_failed_scan_still_runs_directly(client):
    _admin(client)
    repo = client.app.state.repos.batches
    bid = client.post("/api/admin/batches", json=SCAN).json()["batch_id"]
    repo.set_item_status(bid, 0, "Failed")
    repo.set_item_status(bid, 1, "Succeeded")
    repo.set_status(bid, "Completed")
    r = client.post(f"/api/admin/batches/{bid}:rerun-failed")
    assert r.status_code == 200 and r.json() == {"status": "Running", "requeued": 1}


def test_orchestrator_rereads_batch_row_before_materializing(client):
    # 틱 스냅샷(list_active)을 잡은 뒤 취소 → 설정 변경 → 재실행이 끝나면, 스냅샷 그대로 굴리면 옛 상한·옵션으로
    # 자식이 생겼다(리뷰 재현). _drive 가 행을 다시 읽어 새 값으로 만든다.
    _admin(client)
    repos = client.app.state.repos
    bid = client.post("/api/admin/batches", json={**SCAN, "items": [
        {"storage": "s1", "target": f"t{i}"} for i in range(4)]}).json()["batch_id"]
    [snap] = [b for b in repos.batches.list_active() if b["batch_id"] == bid]
    assert client.post(f"/api/admin/batches/{bid}:cancel").status_code == 200
    assert _patch(client, bid, {"max_concurrency": 1, "node_count": 3,
                                "options": {"broken_limit": 7}}).status_code == 200
    assert client.post(f"/api/admin/batches/{bid}:rescan").status_code == 200
    BatchOrchestrator(repos, settings=_S())._drive(snap)
    mats = [it for it in repos.batches.list_items(bid) if it["status"] == "Materialized"]
    assert len(mats) == 1                                   # 새 상한(스냅샷은 2)
    payload = repos.requests.get(mats[0]["request_id"])["payload"]
    assert payload["node_count"] == 3 and payload["options"]["broken_limit"] == 7


def test_orchestrator_leaves_batch_cancelled_after_snapshot_alone(client):
    # 스냅샷 뒤 취소된 배치를 낡은 행으로 굴리면 "전 항목 종단 → Completed" 로 취소를 덮었다
    _admin(client)
    repos = client.app.state.repos
    bid = client.post("/api/admin/batches", json=SCAN).json()["batch_id"]
    [snap] = [b for b in repos.batches.list_active() if b["batch_id"] == bid]
    assert client.post(f"/api/admin/batches/{bid}:cancel").status_code == 200
    BatchOrchestrator(repos, settings=_S())._drive(snap)
    assert repos.batches.get(bid)["status"] == "Cancelled"
    assert all(it["request_id"] is None for it in repos.batches.list_items(bid))
