import pytest

ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}
BODY = {"max_nodes": 3, "procs_per_node": 8, "queue": "dms-data",
        "default_priority": "mid", "max_priority": "high",
        "preview_timeout_seconds": 3600, "execution_timeout_seconds": 3600,
        "enabled": True}


def test_policies_require_admin(client):
    assert client.get("/api/admin/policies").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/policies").status_code == 403


def test_policy_crud(client):
    assert client.put("/api/admin/policies/dsync", json=BODY,
                      headers=ADMIN).status_code == 200
    assert client.get("/api/admin/policies/dsync",
                      headers=ADMIN).json()["max_nodes"] == 3
    listed = client.get("/api/admin/policies", headers=ADMIN).json()
    assert [p["tool"] for p in listed] == ["dsync"]
    assert client.get("/api/admin/policies/scan",
                      headers=ADMIN).status_code == 404
    assert client.put("/api/admin/policies/dcp", json=BODY,
                      headers=ADMIN).status_code == 422
    assert client.put("/api/admin/policies/scan",
                      json={**BODY, "max_nodes": 0},
                      headers=ADMIN).status_code == 422
