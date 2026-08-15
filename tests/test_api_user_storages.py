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


# --- 관리 디렉토리(managed_root) 노출: 관리자에게만 ---------------------------
# 사용자 보고: "작업 등록할 때 스토리지 이름은 보이는데 그 스토리지들의 관리
# 디렉토리가 표시가 안 돼서 정확한 path 를 알 수가 없다". 이 목록의 소비자인 배치
# 생성 위저드·scan/sync 제출 화면은 **입력 경로가 managed_root 기준 상대경로**라,
# 관리자가 그 뿌리를 모르면 절대경로를 조립할 수 없다. mount_path/status_detail 은
# 계속 숨긴다 — 화면이 필요로 하는 것은 뿌리 하나뿐이고, 은닉 범위는 필요한 만큼만
# 연다.
def _make_storage(client, name="ceph-a"):
    assert client.post("/api/admin/storages", json={
        "storage_name": name, "mount_path": "/mnt/ceph",
        "managed_root": "/mnt/ceph/dms", "backend_type": "cephfs"},
        headers=ADMIN).status_code == 201


def test_admin_sees_managed_root(client, db):
    _make_storage(client)
    rows = client.get("/api/user/storages", headers=ADMIN).json()
    assert [r["managed_root"] for r in rows] == ["/mnt/ceph/dms"]
    # 나머지 내부 경로·운영 정보는 관리자에게도 이 목록으로는 주지 않는다
    for r in rows:
        assert set(r) == {"storage_name", "backend_type", "status", "managed_root"}


def test_non_admin_never_sees_managed_root(client, db):
    _make_storage(client)
    _login_user(client)
    rows = client.get("/api/user/storages").json()
    assert rows
    for r in rows:
        assert "managed_root" not in r
        for f in FORBIDDEN_FIELDS:
            assert f not in r
