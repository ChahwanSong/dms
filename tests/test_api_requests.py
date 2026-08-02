ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def _login(client, name):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def test_submit_scan_and_poll(client):
    _login(client, "alice")
    r = client.post("/api/user/requests", json={
        "operation": "scan", "storage": "ceph-a", "target": "team/data",
        "options": {"summary_only": True}, "priority": "high"})
    assert r.status_code == 202
    rid = r.json()["request_id"]
    detail = client.get(f"/api/user/requests/{rid}").json()
    assert detail["state"] == "Pending"
    assert detail["operation"] == "scan"
    assert detail["transitions"][0]["to_state"] == "Pending"
    assert detail["resource_key"].startswith("data.scan:ceph-a:team/data:")


def test_validation_maps_to_422(client):
    _login(client, "bob")
    cases = [
        ({"operation": "scan", "storage": "s", "target": "/abs"}, "unsafe_path"),
        ({"operation": "rm", "storage": "s", "target": "a", "options": {}},
         "rm_recursive_required"),
        ({"operation": "sync", "source_storage": "s", "source": "a",
          "destination_storage": "s", "destination": "a/b"},
         "sync_destination_inside_source"),
        ({"operation": "scan", "storage": "s", "target": "a",
          "options": {"nope": 1}}, "unknown_option"),
        ({"operation": "scan", "storage": "s", "target": "a",
          "priority": "urgent"}, "invalid_priority"),
    ]
    for body, reason in cases:
        r = client.post("/api/user/requests", json=body)
        assert r.status_code == 422 and r.json()["detail"] == reason, body


def test_isolation_between_users_and_admin_sees_all(client):
    _login(client, "alice")
    rid = client.post("/api/user/requests", json={
        "operation": "scan", "storage": "s1", "target": "a"}).json()["request_id"]
    client.post("/api/auth/logout")
    _login(client, "eve")
    assert client.get(f"/api/user/requests/{rid}").status_code == 404
    assert client.get("/api/user/requests").json() == []
    assert client.get("/api/user/requests", headers=ADMIN).json()[0]["request_id"] == rid
