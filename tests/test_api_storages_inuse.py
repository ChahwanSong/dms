from dms.repositories import Repositories


def _admin(client):
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})


def _seed_storage(client):
    client.post("/api/admin/storages", json={"storage_name": "s1", "mount_path": "/s1",
        "managed_root": "/s1/dms", "backend_type": "cephfs"})


def test_delete_blocked_when_referenced(client):
    _admin(client); _seed_storage(client)
    # s1을 참조하는 비종단 scan 요청 생성
    client.app.state.repos.requests.create(operation="scan", requester_id="admin",
        actor="admin", resource_key="k1", payload={"storage": "s1", "target": "a", "options": {}},
        priority="mid")
    r = client.delete("/api/admin/storages/s1")
    assert r.status_code == 409 and r.json()["detail"] == "storage_in_use"


def test_delete_allowed_when_not_referenced(client):
    _admin(client)
    client.post("/api/admin/storages", json={"storage_name": "s2", "mount_path": "/s2",
        "managed_root": "/s2/dms", "backend_type": "cephfs"})
    r = client.delete("/api/admin/storages/s2")
    assert r.status_code == 200


def test_referencing_sync_storages(db):
    repos = Repositories(db)
    repos.requests.create(operation="sync", requester_id="a", actor="a", resource_key="k",
        payload={"source_storage": "src", "source": "x", "destination_storage": "dst",
                 "destination": "y", "options": {}}, priority="mid")
    assert repos.requests.active_referencing_storage("src") is True
    assert repos.requests.active_referencing_storage("dst") is True
    assert repos.requests.active_referencing_storage("other") is False
