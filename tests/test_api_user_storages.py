ADMIN = {"Authorization": "Bearer tok-shared"}
FORBIDDEN_FIELDS = ("mount_path", "managed_root", "status_detail")


def _login_user(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})


def test_requires_login(client):
    assert client.get("/api/user/storages").status_code == 401


def test_returns_minimal_fields_without_paths(client, db):
    # 스토리지 2개를 만든다 (기존 test_api_storages.py 방식 그대로)
    assert client.post("/api/admin/storages", json={
        "storage_name": "ceph-a", "mount_path": "/mnt/ceph",
        "managed_root": "/mnt/ceph/dms", "backend_type": "cephfs"},
        headers=ADMIN).status_code == 201
    assert client.post("/api/admin/storages", json={
        "storage_name": "gpfs-a", "mount_path": "/mnt/gpfs",
        "managed_root": "/mnt/gpfs/dms", "backend_type": "gpfs"},
        headers=ADMIN).status_code == 201

    _login_user(client)
    rows = client.get("/api/user/storages").json()
    assert rows, "활성 스토리지가 있어야 한다"
    for r in rows:
        assert set(r) == {"storage_name", "backend_type", "status"}
        for f in FORBIDDEN_FIELDS:
            assert f not in r


def test_excludes_disabled_storages(client, db):
    # 하나를 enabled=0 으로 만든 뒤(admin PUT 사용) 목록에서 빠지는지
    assert client.post("/api/admin/storages", json={
        "storage_name": "ceph-a", "mount_path": "/mnt/ceph",
        "managed_root": "/mnt/ceph/dms", "backend_type": "cephfs"},
        headers=ADMIN).status_code == 201
    r = client.put("/api/admin/storages/ceph-a", json={
        "mount_path": "/mnt/ceph", "managed_root": "/mnt/ceph/dms",
        "backend_type": "cephfs", "enabled": False}, headers=ADMIN)
    assert r.json()["enabled"] == 0

    _login_user(client)
    rows = client.get("/api/user/storages").json()
    assert "ceph-a" not in [row["storage_name"] for row in rows]


def test_sorted_by_name(client, db):
    # zz, aa 순서로 만들어도 aa, zz 로 나온다
    assert client.post("/api/admin/storages", json={
        "storage_name": "zz", "mount_path": "/mnt/zz",
        "managed_root": "/mnt/zz/dms", "backend_type": "cephfs"},
        headers=ADMIN).status_code == 201
    assert client.post("/api/admin/storages", json={
        "storage_name": "aa", "mount_path": "/mnt/aa",
        "managed_root": "/mnt/aa/dms", "backend_type": "cephfs"},
        headers=ADMIN).status_code == 201

    _login_user(client)
    rows = client.get("/api/user/storages").json()
    assert [row["storage_name"] for row in rows] == ["aa", "zz"]


def test_admin_can_also_read(client):
    # 관리자 Bearer 로도 200
    assert client.get("/api/user/storages", headers=ADMIN).status_code == 200
