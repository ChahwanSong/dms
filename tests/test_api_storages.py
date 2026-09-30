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
    r = client.post("/api/admin/storages", json=BODY, headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "storage_exists"
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
    # 무한 스크롤 커서(2026-09-30): 한 쪽씩 뒤로 -- 이어 붙이면 전체와 같다.
    page1 = client.get("/api/admin/audit-log?limit=2", headers=ADMIN).json()
    page2 = client.get(f"/api/admin/audit-log?limit=2&before={page1[-1]['id']}", headers=ADMIN).json()
    assert [a["id"] for a in page1 + page2] == [a["id"] for a in audit[:4]]
    for bad in ("limit=0", "limit=201", "before=0", "before=x"):
        assert client.get(f"/api/admin/audit-log?{bad}", headers=ADMIN).status_code == 422
