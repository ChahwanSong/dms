import pytest
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


# ---- 슬라이스 24 §2.2/§2.4: "/" 거부 + update 가드 ----

def test_create_root_filesystem_storage_is_rejected(client):
    # {mount "/", root "/"} 가 통과하면: 에이전트 statvfs("/")는 어느 노드에서나
    # 성공해 Ready, rm target "etc" 가 검증 통과(validate_rm_target 은 ""/"." 만
    # 거부), 잡 파드는 노드 루트를 hostPath 마운트한 채 drm 을 요청자 신원으로
    # 실행한다(설계 §2.2 시나리오). 등록 자체를 막는 것이 1차 방어다.
    _admin(client)
    r = client.post("/api/admin/storages", json={
        "storage_name": "rootfs", "mount_path": "/", "managed_root": "/",
        "backend_type": "cephfs"})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_storage"


def _active_request_on(client, storage):
    client.app.state.repos.requests.create(operation="scan", requester_id="admin",
        actor="admin", resource_key=f"k-{storage}",
        payload={"storage": storage, "target": "a", "options": {}}, priority="mid")


def test_update_path_change_blocked_while_referenced(client):
    # preview 에서 사용자가 확인한 경로와 execution 이 실제 도는 경로가 갈라지는
    # TOCTOU(확인 게이트 우회, 설계 §2.4) -- delete 가드와 대칭인 409.
    _admin(client); _seed_storage(client)
    _active_request_on(client, "s1")
    r = client.put("/api/admin/storages/s1", json={
        "mount_path": "/s1", "managed_root": "/s1/elsewhere",
        "backend_type": "cephfs", "enabled": True})
    assert r.status_code == 409 and r.json()["detail"] == "storage_in_use"


def test_update_enabled_toggle_allowed_while_referenced(client):
    # 비상 차단(비활성화)은 진행 중 잡이 있어도 돼야 한다(설계 §2.4) -- 가드가
    # "경로·백엔드 변경"에만 걸린다는 계약.
    _admin(client); _seed_storage(client)
    _active_request_on(client, "s1")
    r = client.put("/api/admin/storages/s1", json={
        "mount_path": "/s1", "managed_root": "/s1/dms",
        "backend_type": "cephfs", "enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] == 0


def test_update_trailing_slash_only_is_not_a_change(client):
    # 저장값은 normpath 정규화돼 있다 -- 후행 슬래시만 다른 PUT 이 "변경"으로
    # 오탐되면 enabled 토글 같은 무해 요청까지 409 가 된다. 비교도 같은 정규화로.
    _admin(client); _seed_storage(client)
    _active_request_on(client, "s1")
    r = client.put("/api/admin/storages/s1", json={
        "mount_path": "/s1/", "managed_root": "/s1/dms/",
        "backend_type": "cephfs", "enabled": True})
    assert r.status_code == 200


def test_update_path_change_allowed_when_not_referenced(client):
    _admin(client); _seed_storage(client)
    r = client.put("/api/admin/storages/s1", json={
        "mount_path": "/s1", "managed_root": "/s1/elsewhere",
        "backend_type": "cephfs", "enabled": True})
    assert r.status_code == 200 and r.json()["managed_root"] == "/s1/elsewhere"


# ---- 2026-11-16: 활성 배치도 「사용 중」 + 가드는 스토리지 행 잠금과 같은 트랜잭션 ----

def _batch_on(client, status, items=None, op="scan"):
    repos = client.app.state.repos
    items = items or [{"storage": "s1", "target": "a"}]
    return repos.batches.create(operation=op, requester_id="admin", actor="admin", max_concurrency=1,
                                options={"recursive": True} if op == "rm" else {}, items=items, note=None,
                                status=status, auth_method="session")


@pytest.mark.parametrize("status", ["Previewing", "PreviewReady", "Running", "Cancelling"])
def test_update_path_change_blocked_while_an_active_batch_references(client, status):
    # 배치의 자식 묶음 사이·회차 발사 직후엔 그 스토리지를 쓰는 **요청**이 없다 -- 요청만 보던 가드는 통과시켰고, 남은 항목이
    # 바뀐 경로에서 root 로 돌았다(자동 확인 배치·스케줄이면 사람 없이). 활성 배치의 항목도 본다.
    _admin(client); _seed_storage(client)
    _batch_on(client, status)
    assert not client.app.state.repos.requests.active_referencing_storage("s1")
    r = client.put("/api/admin/storages/s1", json={
        "mount_path": "/s1", "managed_root": "/s1/elsewhere", "backend_type": "cephfs", "enabled": True})
    assert r.status_code == 409 and r.json()["detail"] == "storage_in_use"
    assert client.app.state.repos.storages.get("s1")["managed_root"] == "/s1/dms"
    assert client.delete("/api/admin/storages/s1").status_code == 409
    # 비상 차단(비활성화)은 여전히 된다.
    r = client.put("/api/admin/storages/s1", json={
        "mount_path": "/s1", "managed_root": "/s1/dms", "backend_type": "cephfs", "enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] == 0


@pytest.mark.parametrize("status", ["Completed", "Cancelled"])
def test_terminal_batches_do_not_hold_the_storage(client, status):
    _admin(client); _seed_storage(client)
    _batch_on(client, status)
    r = client.put("/api/admin/storages/s1", json={
        "mount_path": "/s1", "managed_root": "/s1/other", "backend_type": "cephfs", "enabled": True})
    assert r.status_code == 200


def test_revived_batch_holds_the_storage_from_the_same_commit(client):
    # 회차 발사·사람의 :rescan 이 같은 몸통(rescan_if_terminal/_rescan_locked)으로 배치를 되살리는 커밋부터 경로 변경이 막힌다
    # -- 「발사 직후 첫 자식 전」 창(2026-11-16 리뷰 창 A)이 없다.
    _admin(client); _seed_storage(client)
    bid = _batch_on(client, "Completed", op="rm")
    assert client.app.state.repos.batches.rescan_if_terminal(bid)["status"] == "Previewing"
    r = client.put("/api/admin/storages/s1", json={
        "mount_path": "/s1", "managed_root": "/s1/home", "backend_type": "cephfs", "enabled": True})
    assert r.status_code == 409 and r.json()["detail"] == "storage_in_use"


def test_sync_batch_holds_both_storages_and_unknown_status_counts_as_active(client):
    _admin(client); _seed_storage(client)
    repos = client.app.state.repos
    _batch_on(client, "Running", op="sync", items=[{"source_storage": "s2", "source": "a",
                                                     "destination_storage": "s1", "destination": "b"}])
    assert repos.batches.active_referencing_storage("s1") is True
    assert repos.batches.active_referencing_storage("s2") is True
    assert repos.batches.active_referencing_storage("s3") is False
    bid = _batch_on(client, "Completed", items=[{"storage": "s3", "target": "x"}])
    repos.db.execute("UPDATE batches SET status = 'Weird' WHERE batch_id = :b", {"b": bid})
    assert repos.batches.active_referencing_storage("s3") is True          # 모르는 상태(변조)는 활성으로 -- fail-closed


def test_broken_item_payload_does_not_break_the_storage_guard(client):
    _admin(client); _seed_storage(client)
    bid = _batch_on(client, "Running")
    client.app.state.repos.db.execute("UPDATE batch_items SET payload = '{broken' WHERE batch_id = :b", {"b": bid})
    r = client.put("/api/admin/storages/s1", json={
        "mount_path": "/s1", "managed_root": "/s1/other", "backend_type": "cephfs", "enabled": True})
    assert r.status_code == 200
