ADMIN = {"Authorization": "Bearer tok-shared"}
BODY = {"storage_name": "ceph-a", "mount_path": "/mnt/ceph",
        "managed_root": "/mnt/ceph/dms", "backend_type": "cephfs"}


def test_requires_admin(client):
    assert client.get("/api/admin/storages").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/storages").status_code == 403


def test_crud_flow(client):
    assert client.post("/api/admin/storages", json=BODY, headers=ADMIN).status_code == 201
    assert client.post("/api/admin/storages", json={
        **BODY, "managed_root": "/elsewhere"}, headers=ADMIN).status_code == 422
    rows = client.get("/api/admin/storages", headers=ADMIN).json()
    assert rows[0]["storage_name"] == "ceph-a"
    r = client.put("/api/admin/storages/ceph-a", json={
        "mount_path": "/mnt/ceph", "managed_root": "/mnt/ceph/dms",
        "backend_type": "cephfs", "enabled": False}, headers=ADMIN)
    assert r.json()["enabled"] == 0
    assert client.delete("/api/admin/storages/ceph-a",
                         headers=ADMIN).json()["storage_name"] == "ceph-a"
    assert client.put("/api/admin/storages/ceph-a", json={
        "mount_path": "/m", "managed_root": "/m", "backend_type": "cephfs",
        "enabled": True}, headers=ADMIN).status_code == 404
    audit = client.get("/api/admin/audit-log", headers=ADMIN).json()
    assert [a["operation"] for a in audit[:3]] == ["delete", "update", "create"]
