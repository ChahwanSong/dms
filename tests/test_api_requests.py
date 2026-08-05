ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def _login(client, name):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def test_submit_scan_and_poll(client):
    # scan은 관리자 전용 제출이다 (test_scan_submission_is_admin_only 참고) — Bearer로 인증한다.
    r = client.post("/api/user/requests", headers=ADMIN, json={
        "operation": "scan", "storage": "ceph-a", "target": "team/data",
        "options": {"top_k": 5}, "priority": "high"})
    assert r.status_code == 202
    rid = r.json()["request_id"]
    detail = client.get(f"/api/user/requests/{rid}", headers=ADMIN).json()
    assert detail["state"] == "Pending"
    assert detail["operation"] == "scan"
    assert detail["transitions"][0]["to_state"] == "Pending"
    assert detail["resource_key"].startswith("data.scan:ceph-a:team/data:")


def test_validation_maps_to_422(client):
    _login(client, "bob")
    cases = [
        ({"operation": "rm", "storage": "s", "target": "a", "options": {}},
         "rm_recursive_required"),
        ({"operation": "rm", "storage": None, "target": "a",
          "options": {"recursive": True}}, "missing_storage"),
        ({"operation": "sync", "source_storage": "s", "source": "a",
          "destination_storage": "s", "destination": "a/b"},
         "sync_destination_inside_source"),
        ({"operation": "sync", "source_storage": "s", "source": "a",
          "destination": "b"}, "missing_destination_storage"),
    ]
    for body, reason in cases:
        r = client.post("/api/user/requests", json=body)
        assert r.status_code == 422 and r.json()["detail"] == reason, body

    # scan은 관리자 전용 제출이라 admin 인증으로 검증 오류를 확인한다.
    admin_cases = [
        ({"operation": "scan", "storage": "s", "target": "/abs"}, "unsafe_path"),
        ({"operation": "scan", "target": "a"}, "missing_storage"),
        ({"operation": "scan", "storage": "s", "target": "a",
          "options": {"nope": 1}}, "unknown_option"),
        ({"operation": "scan", "storage": "s", "target": "a",
          "priority": "urgent"}, "invalid_priority"),
    ]
    for body, reason in admin_cases:
        r = client.post("/api/user/requests", json=body, headers=ADMIN)
        assert r.status_code == 422 and r.json()["detail"] == reason, body


def test_isolation_between_users_and_admin_sees_all(client):
    _login(client, "alice")
    rid = client.post("/api/user/requests", json={
        "operation": "rm", "storage": "s1", "target": "a",
        "options": {"recursive": True}}).json()["request_id"]
    client.post("/api/auth/logout")
    _login(client, "eve")
    assert client.get(f"/api/user/requests/{rid}").status_code == 404
    assert client.get("/api/user/requests").json() == []
    assert client.get("/api/user/requests", headers=ADMIN).json()[0]["request_id"] == rid


def test_unknown_operation_is_422_not_500(client):
    # scan 관리자 게이트는 422 매핑 try 블록 앞에 있다 — 거기서 Operation(...)을
    # 구성하면 알 수 없는 연산이 ValueError로 새어 500이 된다.
    _login(client, "dave")
    r = client.post("/api/user/requests", json={"operation": "bogus", "storage": "s1"})
    assert r.status_code == 422
    assert r.json()["detail"] == "invalid_operation"


def test_scan_submission_is_admin_only(client):
    _login(client, "carol")
    r = client.post("/api/user/requests", json={
        "operation": "scan", "storage": "s1", "target": "a"})
    assert r.status_code == 403
    assert r.json()["detail"] == "scan_admin_only"

    r = client.post("/api/user/requests", headers=ADMIN, json={
        "operation": "scan", "storage": "s1", "target": "a"})
    assert r.status_code == 202

    # sync와 rm은 non-admin에게 여전히 영향받지 않는다.
    r = client.post("/api/user/requests", json={
        "operation": "rm", "storage": "s1", "target": "a",
        "options": {"recursive": True}})
    assert r.status_code == 202

    r = client.post("/api/user/requests", json={
        "operation": "sync", "source_storage": "s1", "source": "a",
        "destination_storage": "s2", "destination": "b"})
    assert r.status_code == 202
