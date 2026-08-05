ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def test_control_state_requires_admin(client):
    assert client.get("/api/admin/control-state").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/control-state").status_code == 403


def test_control_state_get_defaults(client):
    body = client.get("/api/admin/control-state", headers=ADMIN).json()
    assert body["maintenance"] == 0
    assert body["drain"] == 0


def test_control_state_put_updates_and_returns_current(client):
    res = client.put("/api/admin/control-state",
                     json={"maintenance": True, "drain": False, "reason": "점검"},
                     headers=ADMIN)
    assert res.status_code == 200
    body = res.json()
    assert body["maintenance"] == 1 and body["drain"] == 0
    assert body["reason"] == "점검"
    assert client.get("/api/admin/control-state", headers=ADMIN).json()["maintenance"] == 1


def test_control_state_put_is_audited(client, db):
    client.put("/api/admin/control-state",
               json={"maintenance": False, "drain": True, "reason": None},
               headers=ADMIN)
    rows = db.query("SELECT * FROM audit_log WHERE mutation_class = 'control_state'")
    assert len(rows) == 1
    assert rows[0]["operation"] == "set"
