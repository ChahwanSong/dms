import pytest

ADMIN = {"Authorization": "Bearer tok-shared"}
SCAN = {"operation": "scan", "storage": "s1", "target": "data", "priority": "mid"}


@pytest.fixture
def session_admin(client):
    # 컨트롤 상태 PUT 은 세션 관리자만(admin_session_required, 2026-10-07) -- 공유 토큰은 모든 노드
    # 에이전트가 쥔 자격이다. 유지보수 토글(_set)은 이 세션으로, 제출 경로 검증은 기존대로 토큰 헤더로
    # 부른다(Bearer 헤더가 세션 쿠키보다 우선하므로 두 신원이 한 클라이언트에서 섞이지 않는다).
    client.app.state.repos.accounts.create("opadm", "p", "admin", actor="t")
    assert client.post("/api/auth/login", json={"username": "opadm", "password": "p"}).status_code == 200


def _set(client, *, maintenance, drain=False):
    res = client.put("/api/admin/control-state",
                     json={"maintenance": maintenance, "drain": drain, "reason": None})
    # 토글이 조용히 거절되면(예: 403) 아래 단언이 공허하게 통과한다 -- 여기서 바로 터뜨린다.
    assert res.status_code == 200, res.text
    return res


def test_submit_blocked_during_maintenance(client, session_admin):
    assert client.post("/api/user/requests", json=SCAN, headers=ADMIN).status_code == 202
    _set(client, maintenance=True)
    res = client.post("/api/user/requests", json=SCAN, headers=ADMIN)
    assert res.status_code == 503
    assert res.json()["detail"] == "maintenance_mode"


def test_submit_allowed_after_maintenance_off(client, session_admin):
    _set(client, maintenance=True)
    assert client.post("/api/user/requests", json=SCAN, headers=ADMIN).status_code == 503
    _set(client, maintenance=False)
    assert client.post("/api/user/requests", json=SCAN, headers=ADMIN).status_code == 202


def test_drain_does_not_block_submission(client, session_admin):
    _set(client, maintenance=False, drain=True)
    assert client.post("/api/user/requests", json=SCAN, headers=ADMIN).status_code == 202


def test_batch_create_blocked_during_maintenance(client, session_admin):
    _set(client, maintenance=True)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 2,
        "options": {}, "note": "n", "items": [{"storage": "s1", "target": "a"}, {"storage": "s1", "target": "b"}]},
        headers=ADMIN)
    assert r.status_code == 503
    assert r.json()["detail"] == "maintenance_mode"


def test_rerun_failed_blocked_during_maintenance(client):
    # 배치 생성은 allowlist 세션 관례(통일 특권 게이트) — 토큰 생성은 403 이라
    # 세션 admin("admin"은 기본 allowlist)으로 만든다. :rerun-failed 도 이제(2026-10-07) 같은
    # 특권 게이트를 타므로 같은 세션 admin 으로 부른다 — 토큰이면 게이트로 403 이 된다. 다만
    # 유지보수 판정이 게이트보다 먼저라(토큰이어도 503) 그 순서도 함께 고정한다.
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 2,
        "options": {}, "note": "n", "items": [{"storage": "s1", "target": "a"}, {"storage": "s1", "target": "b"}]})
    bid = r.json()["batch_id"]
    client.app.state.repos.batches.set_item_status(bid, 0, "Failed")
    _set(client, maintenance=True)
    res = client.post(f"/api/admin/batches/{bid}:rerun-failed")
    assert res.status_code == 503
    assert res.json()["detail"] == "maintenance_mode"
    assert client.post(f"/api/admin/batches/{bid}:rerun-failed", headers=ADMIN).status_code == 503


def test_control_state_put_never_locks_out_during_maintenance(client, session_admin):
    # 관리자도 제출 경로에서는 예외가 아니지만, control-state PUT은 제출 경로가 아니므로
    # 유지보수 중에도 반드시 성공해야 한다 (락아웃 방지).
    _set(client, maintenance=True)
    res = _set(client, maintenance=False)
    assert res.status_code == 200
