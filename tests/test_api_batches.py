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


def test_confirm_requires_previewready(client):
    _admin(client)
    bid = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1,
        "options": {}, "note": None, "items": [{"storage": "s1", "target": "a"}]}).json()["batch_id"]
    r = client.post(f"/api/admin/batches/{bid}:confirm")     # scan은 Running이라 confirm 불가
    assert r.status_code == 409


def test_requires_admin(client):
    r = client.get("/api/admin/batches")
    assert r.status_code == 401


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
