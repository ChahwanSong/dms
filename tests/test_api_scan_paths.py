ADMIN = {"Authorization": "Bearer tok-shared"}


def _signup_login(client, username):
    client.post("/api/auth/signup", json={"username": username, "password": "p"})
    client.post("/api/auth/login", json={"username": username, "password": "p"})


def _make_storage(client, name="ceph-a"):
    return client.post("/api/admin/storages", json={
        "storage_name": name, "mount_path": f"/mnt/{name}",
        "managed_root": f"/mnt/{name}/dms", "backend_type": "cephfs"},
        headers=ADMIN)


def test_requires_login(client):
    assert client.get("/api/user/scan-paths").status_code == 401
    assert client.post("/api/user/scan-paths", json={
        "storage_name": "ceph-a", "path": "data"}).status_code == 401


def test_register_appears_in_list_and_is_normalized(client, db):
    assert _make_storage(client).status_code == 201
    _signup_login(client, "u1")

    r = client.post("/api/user/scan-paths", json={
        "storage_name": "ceph-a", "path": "data//set1/"})
    assert r.status_code == 201
    body = r.json()
    assert body["storage_name"] == "ceph-a"
    # 정규화된 경로가 저장돼야 한다 (Task 3의 커버 판정이 문자열 비교에 의존한다).
    assert body["path"] == "data/set1"

    rows = client.get("/api/user/scan-paths").json()
    assert [row["path"] for row in rows] == ["data/set1"]


def test_other_users_list_excludes_it(client, db):
    assert _make_storage(client).status_code == 201
    _signup_login(client, "u1")
    assert client.post("/api/user/scan-paths", json={
        "storage_name": "ceph-a", "path": "data/set1"}).status_code == 201

    _signup_login(client, "u2")
    rows = client.get("/api/user/scan-paths").json()
    assert rows == []


def test_duplicate_registration_conflicts(client, db):
    assert _make_storage(client).status_code == 201
    _signup_login(client, "u1")
    assert client.post("/api/user/scan-paths", json={
        "storage_name": "ceph-a", "path": "data/set1"}).status_code == 201

    r = client.post("/api/user/scan-paths", json={
        "storage_name": "ceph-a", "path": "data/set1"})
    assert r.status_code == 409
    assert r.json()["detail"] == "scan_path_exists"


def test_unsafe_paths_rejected(client, db):
    assert _make_storage(client).status_code == 201
    _signup_login(client, "u1")

    for bad in ("/etc/passwd", "../escape", ""):
        r = client.post("/api/user/scan-paths", json={
            "storage_name": "ceph-a", "path": bad})
        assert r.status_code == 422, bad
        assert r.json()["detail"] == "unsafe_path", bad


def test_missing_or_inactive_storage_rejected(client, db):
    _signup_login(client, "u1")

    # 존재하지 않는 스토리지
    r = client.post("/api/user/scan-paths", json={
        "storage_name": "nope", "path": "data"})
    assert r.status_code == 422
    assert r.json()["detail"] == "storage_missing"

    # 존재하지만 비활성 스토리지
    assert _make_storage(client, "ceph-a").status_code == 201
    disable = client.put("/api/admin/storages/ceph-a", json={
        "mount_path": "/mnt/ceph-a", "managed_root": "/mnt/ceph-a/dms",
        "backend_type": "cephfs", "enabled": False}, headers=ADMIN)
    assert disable.status_code == 200

    _signup_login(client, "u1")
    r = client.post("/api/user/scan-paths", json={
        "storage_name": "ceph-a", "path": "data"})
    assert r.status_code == 422
    assert r.json()["detail"] == "storage_missing"


def test_delete_scoped_to_owner(client, db):
    assert _make_storage(client).status_code == 201
    _signup_login(client, "u1")
    created = client.post("/api/user/scan-paths", json={
        "storage_name": "ceph-a", "path": "data/set1"}).json()
    path_id = created["id"]

    # 타인이 지우려 하면 존재를 숨긴 404
    _signup_login(client, "u2")
    r = client.delete(f"/api/user/scan-paths/{path_id}")
    assert r.status_code == 404
    assert r.json()["detail"] == "scan_path_not_found"

    # 본인이 지우면 200, 목록에서 사라짐
    _signup_login(client, "u1")
    r = client.delete(f"/api/user/scan-paths/{path_id}")
    assert r.status_code == 200

    rows = client.get("/api/user/scan-paths").json()
    assert rows == []
