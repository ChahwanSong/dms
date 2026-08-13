ADMIN = {"Authorization": "Bearer tok-shared"}
SCAN = {"operation": "scan", "storage": "s1", "target": "data", "priority": "mid"}


def _set(client, *, maintenance, drain=False):
    return client.put("/api/admin/control-state",
                      json={"maintenance": maintenance, "drain": drain, "reason": None},
                      headers=ADMIN)


def test_submit_blocked_during_maintenance(client):
    assert client.post("/api/user/requests", json=SCAN, headers=ADMIN).status_code == 202
    _set(client, maintenance=True)
    res = client.post("/api/user/requests", json=SCAN, headers=ADMIN)
    assert res.status_code == 503
    assert res.json()["detail"] == "maintenance_mode"


def test_submit_allowed_after_maintenance_off(client):
    _set(client, maintenance=True)
    assert client.post("/api/user/requests", json=SCAN, headers=ADMIN).status_code == 503
    _set(client, maintenance=False)
    assert client.post("/api/user/requests", json=SCAN, headers=ADMIN).status_code == 202


def test_drain_does_not_block_submission(client):
    _set(client, maintenance=False, drain=True)
    assert client.post("/api/user/requests", json=SCAN, headers=ADMIN).status_code == 202


def test_batch_create_blocked_during_maintenance(client):
    _set(client, maintenance=True)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 2,
        "options": {}, "note": "n", "items": [{"storage": "s1", "target": "a"}, {"storage": "s1", "target": "b"}]},
        headers=ADMIN)
    assert r.status_code == 503
    assert r.json()["detail"] == "maintenance_mode"


def test_rerun_failed_blocked_during_maintenance(client):
    # 배치 생성은 allowlist 세션 관례(통일 특권 게이트) — 토큰 생성은 403 이라
    # 세션 admin("admin"은 기본 allowlist)으로 만든다. 유지보수 차단 검증 대상인
    # :rerun-failed 호출 자체는 게이트 무관이라 기존 토큰 헤더 그대로다.
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 2,
        "options": {}, "note": "n", "items": [{"storage": "s1", "target": "a"}, {"storage": "s1", "target": "b"}]})
    bid = r.json()["batch_id"]
    client.app.state.repos.batches.set_item_status(bid, 0, "Failed")
    _set(client, maintenance=True)
    res = client.post(f"/api/admin/batches/{bid}:rerun-failed", headers=ADMIN)
    assert res.status_code == 503
    assert res.json()["detail"] == "maintenance_mode"


def test_control_state_put_never_locks_out_during_maintenance(client):
    # 관리자도 제출 경로에서는 예외가 아니지만, control-state PUT은 제출 경로가 아니므로
    # 유지보수 중에도 반드시 성공해야 한다 (락아웃 방지).
    _set(client, maintenance=True)
    res = _set(client, maintenance=False)
    assert res.status_code == 200
