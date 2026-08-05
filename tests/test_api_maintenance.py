ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}
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


def test_control_state_put_never_locks_out_during_maintenance(client):
    # 관리자도 제출 경로에서는 예외가 아니지만, control-state PUT은 제출 경로가 아니므로
    # 유지보수 중에도 반드시 성공해야 한다 (락아웃 방지).
    _set(client, maintenance=True)
    res = _set(client, maintenance=False)
    assert res.status_code == 200
