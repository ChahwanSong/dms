"""direct(O_DIRECT) + bufsize 규칙이 옵션을 받는 모든 경로에 걸리는가(2026-10-27 d192, domain.direct_bufsize_ok 주석).

판정은 domain.validate_options 하나다 -- 단건 제출(build_data_payload)·배치 생성(항목마다 build_data_payload)·종단 배치
실행 설정 변경·다시 돌리는 동작(validate_batch_options)·자식 생성(orchestrator._materialize)·자동 확인 보류
(auto_confirm_hold)가 모두 그 함수를 지난다. 여기서는 그 「모두」를 경로별로 고정한다 -- 한 경로가 자기 검증을 따로
들면 거기만 8 MiB 버퍼가 새어 실행 중 MPI 중단으로 끝난다(drift [8]).

dms-k8s 이식(2026-10-28): k8s 신원 모드라 제출은 세션 인증만(공유 토큰 403 privileged_not_authorized)이고 모든 잡이 root
(run_as_root 생략 = root, 명시 false = 422)다 -- dms-ssc 판의 공유 토큰 + run_as_root False 제출은 여기서 게이트에 먼저 막힌다.
그래서 픽스처는 test_k8s_submit 의 세계(dmsk8s 실데이터 + 스토리지 4종 + 계정)이고, 단건은 두 주소 모양을 다 본다:
직접 경로(면제 관리자 ops)와 PV 주소(일반 사용자 alice -- 맵핑된 PV 안). 배치 CSV 해석(:parse-csv)은 옵션을 받지 않는다(옵션은
배치 단위 -- batch_csv._ROW_OPTIONS) -- CSV 로 만든 배치도 생성 라우트(POST /api/admin/batches)를 지나 여기 생성 시험이 덮는다.
"""
import pytest

from dms.batch_orchestrator import BatchOrchestrator, auto_confirm_hold
from dms.domain import direct_bufsize_ok
from dms.repositories import Repositories
# 같은 세계·헬퍼(test_k8s_submit) -- test_direct_dest_owner 와 같은 방식.
from test_k8s_submit import _login, _pv_sync, kclient, world  # noqa: F401

CEPH = "cephfs-dms"       # filesystem(직접 경로 -- 면제 관리자만)
SYNC_ITEM = {"source_storage": CEPH, "source": "a", "destination_storage": CEPH, "destination": "b"}
BAD = {"direct": True, "bufsize": 8_388_608}
CODE = "direct_bufsize_misaligned"


@pytest.mark.parametrize("bufsize, ok", [
    (4096, True), (65_536, True), (1_048_576, True), (4_194_304, True),
    (2048, False), (8_388_608, False), (1_000_000, False), (12_288, False), (True, False), (None, False)])
def test_direct_bufsize_ok_is_a_power_of_two_between_4k_and_4m(bufsize, ok):
    assert direct_bufsize_ok(bufsize) is ok


def test_single_submit_direct_path_rejects_misaligned_and_accepts_aligned(kclient):
    _login(kclient, "ops")                    # 특권 목록 + 세션 관리자 = 직접 경로를 쓸 수 있는 면제자
    base = {"operation": "sync", **SYNC_ITEM}    # run_as_root 생략 = root(k8s 모드)
    r = kclient.post("/api/user/requests", json={**base, "options": BAD})
    assert r.status_code == 422 and r.json()["detail"] == CODE
    r = kclient.post("/api/user/requests", json={**base, "options": {"direct": True, "bufsize": 1_048_576}})
    assert r.status_code == 202, r.text
    # direct 없이는 큰 버퍼도 정상(규칙은 direct 와 함께일 때만).
    r = kclient.post("/api/user/requests", json={**base, "destination": "c", "options": {"bufsize": 8_388_608}})
    assert r.status_code == 202, r.text


def test_single_submit_pv_path_rejects_misaligned_and_accepts_aligned(kclient):
    # PV 주소 제출도 같은 build_data_payload(→ validate_options)를 지난다(routes_requests._validated_payload) -- PV 블록은 그 반환
    # 뒤에 덧붙을 뿐 옵션을 따로 보지 않는다.
    _login(kclient, "alice")                  # team-a edit·team-b admin -- 기본 _pv_sync(team-a datasets → team-b scratch)
    r = kclient.post("/api/user/requests", json=_pv_sync(options=BAD))
    assert r.status_code == 422 and r.json()["detail"] == CODE
    r = kclient.post("/api/user/requests", json=_pv_sync(options={"direct": True, "bufsize": 4_194_304}))
    assert r.status_code == 202, r.text


def _batch(kclient, options, **extra):
    return kclient.post("/api/admin/batches", json={"operation": "sync", "max_concurrency": 1, "note": None,
                                                    "options": options, "items": [SYNC_ITEM], **extra})


def test_batch_create_rejects_misaligned(kclient):
    _login(kclient, "ops")
    r = _batch(kclient, BAD)
    assert r.status_code == 422 and r.json()["detail"] == CODE


def _terminal_batch(kclient, options):
    repo = kclient.app.state.repos.batches
    r = _batch(kclient, options)
    assert r.status_code == 202, r.text
    bid = r.json()["batch_id"]
    for it in repo.list_items(bid):
        repo.set_item_status(bid, it["seq"], "Succeeded")
    repo.set_status(bid, "Completed")
    return bid


def test_execution_settings_change_rejects_turning_direct_on_with_a_misaligned_buffer(kclient):
    _login(kclient, "ops")
    bid = _terminal_batch(kclient, {"bufsize": 8_388_608})          # direct 없이는 정상
    r = kclient.patch(f"/api/admin/batches/{bid}/execution", json={"options": BAD})
    assert r.status_code == 422 and r.json()["detail"] == CODE
    assert kclient.app.state.repos.batches.get(bid)["options"]["bufsize"] == 8_388_608   # 거부는 아무것도 안 쓴다
    assert "direct" not in kclient.app.state.repos.batches.get(bid)["options"]


def test_item_add_and_replace_recheck_the_batch_options(kclient, db):
    # 항목 추가·교체는 배치 옵션으로 각 행을 다시 본다(_item_validator·_items_validator -- build_data_payload). 규칙 전에 저장된
    # 배치(아래 _legacy_batch)라면 새 행이 그 옵션으로 422 다 -- 옛 옵션이 새 자식으로 새지 않는다.
    _login(kclient, "ops")
    repos = kclient.app.state.repos
    bid = _legacy_batch(repos, status="PreviewReady")
    r = kclient.post(f"/api/admin/batches/{bid}/items", json=dict(SYNC_ITEM, destination="c"))
    assert r.status_code == 422 and r.json()["detail"] == CODE, r.text
    r = kclient.put(f"/api/admin/batches/{bid}/items/0", json=dict(SYNC_ITEM, destination="d"))
    assert r.status_code == 422 and r.json()["detail"] == CODE, r.text
    done = _legacy_batch(repos, status="Completed")              # 전체 교체는 종단 배치만
    r = kclient.put(f"/api/admin/batches/{done}/items", json={"items": [dict(SYNC_ITEM, destination="e")]})
    assert r.status_code == 422 and r.json()["detail"] == CODE, r.text
    assert len(repos.batches.list_items(done)) == 1          # 거부는 아무것도 안 쓴다(원래 항목 하나 그대로)


def _legacy_batch(repos, *, status):
    """규칙 전에 저장된 배치 -- 검증을 거치지 않고 저장소에 직접(DB 가 신뢰 경계)."""
    return repos.batches.create(operation="sync", requester_id="ops", actor="ops", max_concurrency=1,
                                options=dict(BAD), items=[dict(SYNC_ITEM)], note=None, status=status,
                                auth_method="session")


def test_legacy_batch_cannot_be_rerun_and_its_child_is_rejected(kclient, db):
    _login(kclient, "ops")
    repos = kclient.app.state.repos
    done = _legacy_batch(repos, status="Completed")
    repos.batches.set_item_status(done, 0, "Succeeded")
    r = kclient.post(f"/api/admin/batches/{done}:rescan")
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
