from dms.domain import DataJobState


def _admin(client):  # 세션 로그인(admin) — 기존 test_api_auth 패턴 재사용
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})


def test_create_and_get_batch(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 2,
        "options": {}, "note": "n", "items": [{"storage": "s1", "target": "a"}, {"storage": "s1", "target": "b"}]})
    assert r.status_code == 202
    bid = r.json()["batch_id"]
    assert r.json()["status"] == "Running"
    d = client.get(f"/api/admin/batches/{bid}").json()
    assert d["item_count"] == 2 and len(d["items"]) == 2


def test_create_sync_is_previewing(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "sync", "max_concurrency": 1,
        "options": {}, "note": None, "items": [{"source_storage": "s1", "source": "a",
        "destination_storage": "s2", "destination": "b"}]})
    assert r.json()["status"] == "Previewing"


def test_create_rejects_bad_item(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "items": [{"storage": "s1", "target": "../bad"}]})
    assert r.status_code == 422


def test_create_rejects_empty(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "items": []})
    assert r.status_code == 422 and r.json()["detail"] == "empty_batch"


# --- 슬라이스 32: 배치 실행 제어(priority/node_count)·동질성·mc 상한 ---

def test_create_with_priority_and_node_count(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 2,
        "options": {}, "note": None, "priority": "high", "node_count": 4,
        "items": [{"storage": "s1", "target": "a"}]})
    assert r.status_code == 202
    b = client.app.state.repos.batches.get(r.json()["batch_id"])
    assert b["priority"] == "high" and b["node_count"] == 4


def test_create_rejects_bad_priority(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "priority": "urgent",
        "items": [{"storage": "s1", "target": "a"}]})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_priority"


def test_create_rejects_bad_node_count(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "node_count": 0,
        "items": [{"storage": "s1", "target": "a"}]})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_node_count"


def test_create_rejects_mixed_scan_storage(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None,
        "items": [{"storage": "s1", "target": "a"}, {"storage": "s2", "target": "b"}]})
    assert r.status_code == 422 and r.json()["detail"] == "batch_storage_mixed"


def test_create_rejects_mixed_sync_pair(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "sync", "max_concurrency": 1,
        "options": {}, "note": None, "items": [
            {"source_storage": "s1", "source": "a",
             "destination_storage": "s2", "destination": "b"},
            {"source_storage": "s1", "source": "c",
             "destination_storage": "s3", "destination": "d"}]})
    assert r.status_code == 422 and r.json()["detail"] == "batch_storage_mixed"


def test_create_rejects_max_concurrency_over_64(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 65,
        "options": {}, "note": None, "items": [{"storage": "s1", "target": "a"}]})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_max_concurrency"


def test_create_without_priority_node_count_stores_null(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "items": [{"storage": "s1", "target": "a"}]})
    assert r.status_code == 202  # 기존 바디 무수정 호환(옵션 필드)
    b = client.app.state.repos.batches.get(r.json()["batch_id"])
    # null(모름) ≠ 0 — 미지정은 NULL(정책 기본)
    assert b["priority"] is None and b["node_count"] is None


# --- 노드당 프로세스 수 override: node_count 배선 관례의 미러 ---

def test_create_with_procs_per_node(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 2,
        "options": {}, "note": None, "procs_per_node": 4,
        "items": [{"storage": "s1", "target": "a"}]})
    assert r.status_code == 202
    b = client.app.state.repos.batches.get(r.json()["batch_id"])
    assert b["procs_per_node"] == 4


def test_create_rejects_bad_procs_per_node(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "procs_per_node": 0,
        "items": [{"storage": "s1", "target": "a"}]})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_procs_per_node"


def test_create_without_procs_per_node_stores_null(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "items": [{"storage": "s1", "target": "a"}]})
    assert r.status_code == 202  # 기존 바디 무수정 호환(옵션 필드)
    # null(모름) ≠ 0 — 미지정은 NULL(정책 기본)
    assert client.app.state.repos.batches.get(r.json()["batch_id"])["procs_per_node"] is None


# --- 배치 이름(name): 등록 시 설정 ---

def test_create_with_name_stores_trimmed(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "name": "  8월 정기 스캔 1차  ",
        "items": [{"storage": "s1", "target": "a"}]})
    assert r.status_code == 202
    b = client.app.state.repos.batches.get(r.json()["batch_id"])
    assert b["name"] == "8월 정기 스캔 1차"


def test_create_name_blank_becomes_null(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "name": "   ",
        "items": [{"storage": "s1", "target": "a"}]})
    assert r.status_code == 202
    # 공백뿐인 이름은 "이름 없음"(NULL)이다 — 빈 문자열을 저장하지 않는다
    assert client.app.state.repos.batches.get(r.json()["batch_id"])["name"] is None


def test_create_without_name_stores_null(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "items": [{"storage": "s1", "target": "a"}]})
    assert r.status_code == 202  # 기존 바디 무수정 호환(옵션 필드)
    assert client.app.state.repos.batches.get(r.json()["batch_id"])["name"] is None


def test_create_name_at_limit_ok_over_limit_422(client):
    _admin(client)
    ok = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "name": "n" * 120,
        "items": [{"storage": "s1", "target": "a"}]})
    assert ok.status_code == 202
    bad = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "name": "n" * 121,
        "items": [{"storage": "s1", "target": "a"}]})
    assert bad.status_code == 422 and bad.json()["detail"] == "invalid_batch_name"


def test_confirm_requires_previewready(client):
    _admin(client)
    bid = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "items": [{"storage": "s1", "target": "a"}]}).json()["batch_id"]
    r = client.post(f"/api/admin/batches/{bid}:confirm")     # scan은 Running이라 confirm 불가
    assert r.status_code == 409


def test_requires_admin(client):
    r = client.get("/api/admin/batches")
    assert r.status_code == 401


# --- 슬라이스 32: :rescan(전체 재실행) ---

def _completed_scan_batch(client, *, statuses=("Succeeded", "Failed")):
    repo = client.app.state.repos.batches
    bid = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 2,
        "options": {}, "note": None,
        "items": [{"storage": "s1", "target": f"t{i}"} for i in range(len(statuses))]
        }).json()["batch_id"]
    for seq, st in enumerate(statuses):
        repo.set_item_status(bid, seq, st)
        if st == "Succeeded":
            repo.bump_counts(bid, succeeded=1)
        elif st == "Failed":
            repo.bump_counts(bid, failed=1)
    repo.set_status(bid, "Completed")
    return bid


def test_rescan_completed_scan_batch(client):
    _admin(client)
    bid = _completed_scan_batch(client)
    r = client.post(f"/api/admin/batches/{bid}:rescan")
    assert r.status_code == 200
    assert r.json() == {"status": "Running", "requeued": 2}
    repo = client.app.state.repos.batches
    assert all(it["status"] == "Queued" for it in repo.list_items(bid))
    b = repo.get(bid)
    assert b["status"] == "Running"
    assert b["succeeded_count"] == 0 and b["failed_count"] == 0


def test_rescan_completed_sync_batch_previews(client):
    _admin(client)
    repo = client.app.state.repos.batches
    bid = client.post("/api/admin/batches", json={"operation": "sync", "max_concurrency": 1,
        "options": {}, "note": None, "items": [{"source_storage": "s1", "source": "a",
        "destination_storage": "s2", "destination": "b"}]}).json()["batch_id"]
    repo.set_item_status(bid, 0, "Succeeded")
    repo.set_status(bid, "Completed")
    r = client.post(f"/api/admin/batches/{bid}:rescan")
    assert r.status_code == 200 and r.json()["status"] == "Previewing"


def test_rescan_running_batch_rejected(client):
    # 활성 자식 이중 실행 방지(전제 #14) — 종단 배치에서만 허용
    _admin(client)
    bid = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "items": [{"storage": "s1", "target": "a"}]}).json()["batch_id"]
    r = client.post(f"/api/admin/batches/{bid}:rescan")
    assert r.status_code == 409 and r.json()["detail"] == "batch_not_rescannable"


def test_rescan_previewready_batch_rejected(client):
    _admin(client)
    repo = client.app.state.repos.batches
    bid = client.post("/api/admin/batches", json={"operation": "sync", "max_concurrency": 1,
        "options": {}, "note": None, "items": [{"source_storage": "s1", "source": "a",
        "destination_storage": "s2", "destination": "b"}]}).json()["batch_id"]
    repo.set_status(bid, "PreviewReady")
    r = client.post(f"/api/admin/batches/{bid}:rescan")
    assert r.status_code == 409 and r.json()["detail"] == "batch_not_rescannable"


def test_rescan_cancelled_batch_allowed(client):
    # 취소된 배치의 item 은 cancel 이 전부 종단화(전제 #15) — 전체 재실행 의미
    _admin(client)
    bid = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "items": [{"storage": "s1", "target": "a"}]}).json()["batch_id"]
    client.post(f"/api/admin/batches/{bid}:cancel")
    r = client.post(f"/api/admin/batches/{bid}:rescan")
    assert r.status_code == 200 and r.json()["status"] == "Running"


def test_rescan_missing_batch_404(client):
    _admin(client)
    r = client.post("/api/admin/batches/nope:rescan")
    assert r.status_code == 404 and r.json()["detail"] == "batch_not_found"


def test_rescan_blocked_during_maintenance(client):
    _admin(client)
    bid = _completed_scan_batch(client)
    client.put("/api/admin/control-state",
               json={"maintenance": True, "drain": False, "reason": None})
    r = client.post(f"/api/admin/batches/{bid}:rescan")
    assert r.status_code == 503 and r.json()["detail"] == "maintenance_mode"


# --- 결과 항목 상세화: 배치 상세 items 의 자식 요청 조인 필드 ---

def _materialize_child(repos, bid, seq, *, target="a"):
    rid = repos.requests.create(operation="scan", requester_id="admin", actor="admin",
        resource_key=f"data.scan:s1:{target}:admin",
        payload={"storage": "s1", "target": target}, priority="mid", batch_id=bid)
    repos.batches.set_item_materialized(bid, seq, rid)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target=target, options={}, tool="dscan", worker_pool={},
        precondition={}, actor="planner")
    return rid, jid


def test_detail_items_join_child_request_fields(client):
    _admin(client)
    bid = _batch(client)
    repos = client.app.state.repos
    rid, jid = _materialize_child(repos, bid, 0)
    repos.data_jobs.set_artifact(jid, artifact_uri=None,
                                 result_summary={"files": 42, "bytes": 10})
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    repos.batches.set_item_status(bid, 0, "Succeeded")
    it = client.get(f"/api/admin/batches/{bid}").json()["items"][0]
    assert it["request_state"] == "Succeeded"
    assert it["files_count"] == 42
    # 완료 시각은 results.completed_at(종단의 정직한 원천) — 값이 실존해야 한다
    assert it["completed_at"]


def test_detail_items_nonterminal_child_has_null_completed_at(client):
    _admin(client)
    bid = _batch(client)
    repos = client.app.state.repos
    _materialize_child(repos, bid, 0)
    it = client.get(f"/api/admin/batches/{bid}").json()["items"][0]
    # 비종단: 상태는 실값(Pending), 완료 시각·파일 수는 None(모름) — null≠0
    assert it["request_state"] == "Pending"
    assert it["completed_at"] is None
    assert it["files_count"] is None


def test_detail_items_without_request_have_null_join_fields(client):
    _admin(client)
    bid = _batch(client)
    it = client.get(f"/api/admin/batches/{bid}").json()["items"][0]
    assert it["request_state"] is None
    assert it["completed_at"] is None
    assert it["files_count"] is None


# --- 배치 수정(메타데이터): PATCH {name?, note?} ---

def _batch(client, **extra):
    body = {"operation": "scan", "max_concurrency": 1, "options": {}, "note": None,
            "items": [{"storage": "s1", "target": "a"}], **extra}
    return client.post("/api/admin/batches", json=body).json()["batch_id"]


def test_patch_updates_name_and_note(client):
    _admin(client)
    bid = _batch(client, name="이전 이름", note="이전 메모")
    r = client.patch(f"/api/admin/batches/{bid}",
                     json={"name": "  새 이름  ", "note": "새 메모"})
    assert r.status_code == 200
    assert r.json()["name"] == "새 이름" and r.json()["note"] == "새 메모"
    b = client.app.state.repos.batches.get(bid)
    assert b["name"] == "새 이름" and b["note"] == "새 메모"


def test_patch_partial_only_touches_sent_keys(client):
    _admin(client)
    bid = _batch(client, name="이름", note="메모")
    r = client.patch(f"/api/admin/batches/{bid}", json={"note": "메모2"})
    assert r.status_code == 200
    b = client.app.state.repos.batches.get(bid)
    # 키 부재 = 무접촉(부분 갱신) — name 이 지워지면 안 된다
    assert b["name"] == "이름" and b["note"] == "메모2"


def test_patch_empty_string_clears_to_null(client):
    _admin(client)
    bid = _batch(client, name="이름", note="메모")
    r = client.patch(f"/api/admin/batches/{bid}", json={"name": "", "note": ""})
    assert r.status_code == 200
    b = client.app.state.repos.batches.get(bid)
    # 지우기 계약: 빈 문자열 → NULL 저장(명시적 null 과 같은 결과로 단순화)
    assert b["name"] is None and b["note"] is None


def test_patch_rejects_overlong_name(client):
    _admin(client)
    bid = _batch(client)
    r = client.patch(f"/api/admin/batches/{bid}", json={"name": "n" * 121})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_batch_name"


def test_patch_missing_batch_404(client):
    _admin(client)
    r = client.patch("/api/admin/batches/nope", json={"name": "x"})
    assert r.status_code == 404 and r.json()["detail"] == "batch_not_found"


def test_patch_blocked_during_maintenance(client):
    _admin(client)
    bid = _batch(client)
    client.put("/api/admin/control-state",
               json={"maintenance": True, "drain": False, "reason": None})
    r = client.patch(f"/api/admin/batches/{bid}", json={"name": "x"})
    assert r.status_code == 503 and r.json()["detail"] == "maintenance_mode"


def test_patch_cannot_touch_items_or_execution_controls(client):
    # 즉시 실행 모델: 자식이 이미 materialize 되므로 items·실행 제어는 수정 불가 —
    # 바디에 실어 보내도 무시된다(메타데이터 두 키만 갱신).
    _admin(client)
    bid = _batch(client, priority="high")
    r = client.patch(f"/api/admin/batches/{bid}",
                     json={"name": "x", "priority": "low", "max_concurrency": 9,
                           "items": [{"storage": "s1", "target": "evil"}]})
    assert r.status_code == 200
    b = client.app.state.repos.batches.get(bid)
    assert b["priority"] == "high" and b["max_concurrency"] == 1
    items = client.app.state.repos.batches.list_items(bid)
    assert [it["payload"] for it in items] == [{"storage": "s1", "target": "a"}]


def test_cancel_running_batch_succeeds(client):
    _admin(client)
    bid = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "items": [{"storage": "s1", "target": "a"}]}).json()["batch_id"]
    r = client.post(f"/api/admin/batches/{bid}:cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "Cancelled"
    assert client.app.state.repos.batches.get(bid)["status"] == "Cancelled"


def test_cancel_completed_batch_rejected(client):
    _admin(client)
    bid = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "items": [{"storage": "s1", "target": "a"}]}).json()["batch_id"]
    client.app.state.repos.batches.set_status(bid, "Completed")
    r = client.post(f"/api/admin/batches/{bid}:cancel")
    assert r.status_code == 409
    assert r.json()["detail"] == "batch_not_cancelable"
