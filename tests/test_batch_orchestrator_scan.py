from dms.repositories import Repositories
from dms.batch_orchestrator import BatchOrchestrator
from dms.domain import RequestState

class _S:  # 최소 settings 더미
    preview_ttl_seconds = 900

def _orch(db):
    return BatchOrchestrator(Repositories(db), settings=_S())

def test_scan_throttles_materialize(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"cephfs-dms","target":f"a{i}"} for i in range(5)], status="Running")
    _orch(db).run_once()
    mats = [it for it in repos.batches.list_items(bid) if it["status"]=="Materialized"]
    assert len(mats)==2                      # 상한 2만 materialize
    # 각 materialize된 자식은 실제 Pending 요청
    for it in mats:
        assert repos.requests.get(it["request_id"])["state"]=="Pending"
        assert repos.requests.get(it["request_id"])["batch_id"]==bid

# --- 슬라이스 32: 배치 실행 제어(priority/node_count)의 자식 전달 ---

def test_batch_priority_flows_to_children(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"cephfs-dms","target":"a"}], status="Running",
        priority="high")
    _orch(db).run_once()
    it = repos.batches.list_items(bid)[0]
    assert repos.requests.get(it["request_id"])["priority"] == "high"

def test_batch_node_count_flows_to_child_payload(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"cephfs-dms","target":"a"}], status="Running",
        node_count=4)
    _orch(db).run_once()
    it = repos.batches.list_items(bid)[0]
    assert repos.requests.get(it["request_id"])["payload"]["node_count"] == 4

def test_batch_procs_per_node_flows_to_child_payload(db):
    # 노드당 프로세스 수 override: node_count 와 같은 build 후 주입 관례의 미러
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"cephfs-dms","target":"a"}], status="Running",
        procs_per_node=4)
    _orch(db).run_once()
    it = repos.batches.list_items(bid)[0]
    assert repos.requests.get(it["request_id"])["payload"]["procs_per_node"] == 4

def test_batch_without_controls_keeps_legacy_child_shape(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"cephfs-dms","target":"a"}], status="Running")
    _orch(db).run_once()
    it = repos.batches.list_items(bid)[0]
    req = repos.requests.get(it["request_id"])
    assert "node_count" not in req["payload"]        # 미지정 = 키 부재(null≠0)
    assert "procs_per_node" not in req["payload"]    # 미지정 = 키 부재(null≠0)
    assert req["priority"] == "mid"             # 정책 없음 폴백(기존 경로)

# --- 배치 특권 실행: owner_username·auth_method 의 자식 상속 ---

def test_batch_owner_username_flows_to_child_payload(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"cephfs-dms","target":"a"}], status="Running",
        owner_username="alice", auth_method="session")
    _orch(db).run_once()
    it = repos.batches.list_items(bid)[0]
    req = repos.requests.get(it["request_id"])
    assert req["payload"]["owner_username"] == "alice"
    assert req["auth_method"] == "session"   # planner 세션 재검증의 재료

def test_batch_without_owner_keeps_payload_shape_but_inherits_auth(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"cephfs-dms","target":"a"}], status="Running",
        auth_method="session")
    _orch(db).run_once()
    it = repos.batches.list_items(bid)[0]
    req = repos.requests.get(it["request_id"])
    assert "owner_username" not in req["payload"]   # None = 키 부재(기존 그대로)
    assert req["auth_method"] == "session"

def test_legacy_batch_row_falls_back_to_token_auth(db):
    # 구형 배치 행(auth_method 컬럼 NULL)은 기존 동작 그대로 token -- NULL(모름)을
    # session 으로 읽으면 조용한 특권 승격 경로가 열린다(fail-closed).
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"cephfs-dms","target":"a"}], status="Running")
    _orch(db).run_once()
    it = repos.batches.list_items(bid)[0]
    assert repos.requests.get(it["request_id"])["auth_method"] == "token"

def test_scan_aggregates_and_completes(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=5, options={}, note=None,
        items=[{"storage":"cephfs-dms","target":"a"},{"storage":"cephfs-dms","target":"b"}],
        status="Running")
    _orch(db).run_once()                     # 둘 다 materialize
    items = repos.batches.list_items(bid)
    # 자식 완료를 시뮬레이션: 하나 Succeeded, 하나 Failed
    repos.requests.set_state(items[0]["request_id"], RequestState.SUCCEEDED, actor="t")
    repos.requests.set_state(items[1]["request_id"], RequestState.FAILED, actor="t")
    _orch(db).run_once()                     # 집계 + Completed
    b = repos.batches.get(bid)
    assert b["status"]=="Completed" and b["succeeded_count"]==1 and b["failed_count"]==1
    sts = sorted(it["status"] for it in repos.batches.list_items(bid))
    assert sts==["Failed","Succeeded"]
