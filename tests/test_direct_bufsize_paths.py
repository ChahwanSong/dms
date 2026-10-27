"""direct(O_DIRECT) + bufsize 규칙이 옵션을 받는 모든 경로에 걸리는가(2026-10-27 d192, domain.direct_bufsize_ok 주석).

판정은 domain.validate_options 하나다 -- 단건 제출(build_data_payload)·배치 생성(항목마다 build_data_payload)·종단 배치
실행 설정 변경·다시 돌리는 동작(validate_batch_options)·자식 생성(orchestrator._materialize)·자동 확인 보류
(auto_confirm_hold)가 모두 그 함수를 지난다. 여기서는 그 「모두」를 경로별로 고정한다 -- 한 경로가 자기 검증을 따로
들면 거기만 8 MiB 버퍼가 새어 실행 중 MPI 중단으로 끝난다(drift [8])."""
from dms.batch_orchestrator import BatchOrchestrator, auto_confirm_hold
from dms.repositories import Repositories

ADMIN = {"Authorization": "Bearer tok-shared"}
SYNC_ITEM = {"source_storage": "s1", "source": "a", "destination_storage": "s2", "destination": "b"}
BAD = {"direct": True, "bufsize": 8_388_608}
CODE = "direct_bufsize_misaligned"


def _storages(repos):
    for name in ("s1", "s2"):
        if repos.storages.get(name) is None:
            repos.storages.create(storage_name=name, mount_path=f"/mnt/{name}", managed_root=f"/mnt/{name}/dms",
                                  backend_type="cephfs", actor="test")


def _admin_session(client):
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "pw"}).status_code == 200


def test_single_submit_rejects_misaligned_and_accepts_aligned(client):
    _storages(client.app.state.repos)
    base = {"operation": "sync", **SYNC_ITEM, "run_as_root": False}
    r = client.post("/api/user/requests", json={**base, "options": BAD}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == CODE
    r = client.post("/api/user/requests", json={**base, "options": {"direct": True, "bufsize": 1_048_576}},
                    headers=ADMIN)
    assert r.status_code == 202, r.text


def test_batch_create_rejects_misaligned(client):
    _admin_session(client)
    _storages(client.app.state.repos)
    r = client.post("/api/admin/batches", json={"operation": "sync", "max_concurrency": 1, "note": None,
                                                "options": BAD, "items": [SYNC_ITEM]})
    assert r.status_code == 422 and r.json()["detail"] == CODE


def _terminal_batch(client, options):
    repo = client.app.state.repos.batches
    bid = client.post("/api/admin/batches", json={"operation": "sync", "max_concurrency": 1, "note": None,
                                                  "options": options, "items": [SYNC_ITEM]}).json()["batch_id"]
    for it in repo.list_items(bid):
        repo.set_item_status(bid, it["seq"], "Succeeded")
    repo.set_status(bid, "Completed")
    return bid


def test_execution_settings_change_rejects_turning_direct_on_with_a_misaligned_buffer(client):
    _admin_session(client)
    _storages(client.app.state.repos)
    bid = _terminal_batch(client, {"bufsize": 8_388_608})          # direct 없이는 정상
    r = client.patch(f"/api/admin/batches/{bid}/execution", json={"options": BAD})
    assert r.status_code == 422 and r.json()["detail"] == CODE
    assert client.app.state.repos.batches.get(bid)["options"]["bufsize"] == 8_388_608   # 거부는 아무것도 안 쓴다
    assert "direct" not in client.app.state.repos.batches.get(bid)["options"]


def _legacy_batch(repos, *, status):
    """규칙 전에 저장된 배치 -- 검증을 거치지 않고 저장소에 직접(DB 가 신뢰 경계)."""
    return repos.batches.create(operation="sync", requester_id="admin", actor="admin", max_concurrency=1,
                                options=dict(BAD), items=[dict(SYNC_ITEM)], note=None, status=status)


def test_legacy_batch_cannot_be_rerun_and_its_child_is_rejected(client, db):
    _admin_session(client)
    repos = client.app.state.repos
    _storages(repos)
    done = _legacy_batch(repos, status="Completed")
    repos.batches.set_item_status(done, 0, "Succeeded")
    r = client.post(f"/api/admin/batches/{done}:rescan")
    assert r.status_code == 422 and r.json()["detail"] == CODE

    class _S:
        preview_ttl_seconds = 900

    live = _legacy_batch(repos, status="Previewing")
    BatchOrchestrator(repos, settings=_S()).run_once()
    item = repos.batches.list_items(live)[0]
    assert item["status"] == "Rejected" and item["reason_code"] == CODE and item["request_id"] is None


def test_auto_confirm_holds_a_misaligned_batch(db):
    # 자동 확인도 확인 순간 옵션을 지금 규칙으로 다시 본다 -- 보류 사유가 곧 이 코드다(사람이 실행 설정을 고친다).
    repos = Repositories(db)
    repos.accounts.create("admin", "pw", "admin", actor="t")

    class _S:
        allow_privileged_requesters = True
        privileged_requesters = frozenset({"admin"})

    batch = {"operation": "sync", "options": dict(BAD), "auto_confirm_by": "admin"}
    assert auto_confirm_hold(repos, _S(), batch) == CODE
    batch["options"] = {"direct": True, "bufsize": 4_194_304}
    assert auto_confirm_hold(repos, _S(), batch) is None
