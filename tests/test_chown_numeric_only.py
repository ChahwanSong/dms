"""sync chown 은 숫자 uid:gid 만(2026-10-01 사용자 결정 -- domain.chown_problem 주석).

이름은 잡 컨테이너(LDAP NSS 없음)에서 제대로 풀리지 않는다: LDAP 사용자 이름은 못 찾아 미리보기가 사유 없는
preview_failed 로 끝나고, users 같은 이름은 데비안 기본 gid 로 풀려 실행에서 실패/무시되고, root 실행이면 실행
신원 이름이 uid 0 으로 /etc/passwd 에 덧붙어 목적지가 **조용히 root 소유**가 됐다. 그래서 세 곳이 막는다:
  - 제출 검증(단건·배치 생성·항목 추가/수정/교체) -> 422 chown_name_not_supported
  - 이름이 든 기존 배치(사용자 결정: 거부) -> 다시 돌리는 동작(확인·재실행·재스캔) 422, 자식 생성 시점엔 그
    항목만 Rejected(다른 배치는 막히지 않는다)
  - 규칙 전에 만들어진 대기 잡 -> stepper 제출 관문(_build_spec)에서 fail-closed
"""
import pytest

from dms.batch_orchestrator import BatchOrchestrator
from dms.domain import DataJobState, RequestState
from dms.execution import StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper

ADMIN = {"Authorization": "Bearer tok-shared"}
SYNC_ITEM = {"source_storage": "s1", "source": "a", "destination_storage": "s2", "destination": "b"}


def _storages(repos):
    for name in ("s1", "s2"):
        if repos.storages.get(name) is None:
            repos.storages.create(storage_name=name, mount_path=f"/mnt/{name}", managed_root=f"/mnt/{name}/dms",
                                  backend_type="cephfs", actor="test")


def _admin_session(client):
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "pw"}).status_code == 200


# --- 제출 검증 ----------------------------------------------------------------------------

def test_single_submit_rejects_a_name_and_accepts_numbers(client):
    _storages(client.app.state.repos)
    base = {"operation": "sync", **SYNC_ITEM, "run_as_root": False}
    for chown in ("alice:users", "alice", "10003:mig", "root"):
        r = client.post("/api/user/requests", json={**base, "options": {"chown": chown}}, headers=ADMIN)
        assert r.status_code == 422 and r.json()["detail"] == "chown_name_not_supported", chown
    r = client.post("/api/user/requests", json={**base, "options": {"chown": "10003:10000"}}, headers=ADMIN)
    assert r.status_code == 202, r.text


def test_batch_create_rejects_a_name(client):
    _admin_session(client)
    _storages(client.app.state.repos)
    r = client.post("/api/admin/batches", json={"operation": "sync", "max_concurrency": 1, "note": None,
                                                "options": {"chown": "alice:users"}, "items": [SYNC_ITEM]})
    assert r.status_code == 422 and r.json()["detail"] == "chown_name_not_supported"


# --- 이름이 든 기존 배치(규칙 전에 만들어짐) -----------------------------------------------------

def _legacy_batch(repos, *, status="Previewing", item_status=None):
    """검증을 거치지 않고 저장소에 직접 -- 규칙이 바뀌기 전에 만들어진 배치의 모양."""
    bid = repos.batches.create(operation="sync", requester_id="admin", actor="admin", max_concurrency=2,
                               options={"chown": "alice:users"}, items=[dict(SYNC_ITEM)], note=None,
                               status=status)
    if item_status is not None:
        repos.batches.set_item_status(bid, 0, item_status)
    return bid


def test_legacy_name_batch_item_edits_are_rejected(client):
    _admin_session(client)
    repos = client.app.state.repos
    _storages(repos)
    bid = _legacy_batch(repos)
    done = _legacy_batch(repos, status="Completed", item_status="Succeeded")   # 전체 교체는 종단 배치만
    edits = [
        client.put(f"/api/admin/batches/{bid}/items/0", json={**SYNC_ITEM, "destination": "c"}),
        client.post(f"/api/admin/batches/{bid}/items", json={**SYNC_ITEM, "destination": "d"}),
        client.put(f"/api/admin/batches/{done}/items", json={"items": [{**SYNC_ITEM, "destination": "e"}]}),
    ]
    for r in edits:
        assert r.status_code == 422 and r.json()["detail"] == "chown_name_not_supported", r.text
    for b in (bid, done):
        assert [it["payload"]["destination"] for it in repos.batches.list_items(b)] == ["b"]


def test_legacy_name_batch_cannot_be_run_again(client):
    _admin_session(client)
    repos = client.app.state.repos
    _storages(repos)
    cases = [
        (_legacy_batch(repos, status="PreviewReady"), ":confirm", None),
        (_legacy_batch(repos, status="Completed", item_status="Failed"), ":rerun-failed", None),
        (_legacy_batch(repos, status="Completed", item_status="Succeeded"), ":rescan", None),
        (_legacy_batch(repos, status="Completed", item_status="Failed"), "/items:rerun", {"seqs": [0]}),
    ]
    for bid, action, body in cases:
        before = repos.batches.get(bid)["status"]
        r = client.post(f"/api/admin/batches/{bid}{action}", json=body)
        assert r.status_code == 422 and r.json()["detail"] == "chown_name_not_supported", (action, r.text)
        assert repos.batches.get(bid)["status"] == before                      # 아무것도 바뀌지 않았다


class _S:
    preview_ttl_seconds = 900


def test_orchestrator_rejects_only_the_legacy_item_and_keeps_other_batches_moving(db):
    repos = Repositories(db)
    legacy = _legacy_batch(repos)
    healthy = repos.batches.create(operation="sync", requester_id="admin", actor="admin", max_concurrency=2,
                                   options={"chown": "10003:10000"}, items=[dict(SYNC_ITEM)], note=None,
                                   status="Previewing")
    BatchOrchestrator(repos, settings=_S()).run_once()                        # 예외 없이 한 틱이 끝난다
    item = repos.batches.list_items(legacy)[0]
    assert item["status"] == "Rejected" and item["reason_code"] == "chown_name_not_supported"
    assert item["request_id"] is None                                           # 자식 요청을 만들지 않았다
    assert repos.batches.get(legacy)["failed_count"] == 1
    assert repos.batches.list_items(healthy)[0]["request_id"] is not None      # 다른 배치는 그대로 진행
    ev = db.query_one("SELECT message FROM events WHERE event_type = 'batch_item_rejected'")
    assert "chown_name_not_supported" in ev["message"]
    BatchOrchestrator(repos, settings=_S()).run_once()                        # 다음 틱: 전 항목 종단 -> 완료
    assert repos.batches.get(legacy)["status"] == "Completed"


# --- 규칙 전에 만들어진 대기 잡: stepper 제출 관문 -------------------------------------------------

class _StepSettings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def _sync_job(repos, *, chown, key, tool="dsync"):
    _storages(repos)
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice", resource_key=key,
                                payload=dict(SYNC_ITEM), priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation="sync", priority="mid", source_storage="s1", source="a",
        destination_storage="s2", destination="b", options={"chown": chown}, tool=tool,
        worker_pool={"tool": tool, "identity": {"uid": 10001, "gid": 10000, "username": "alice",
                                                "groups": [], "privileged": False},
                     # nsync(노드 간)은 소스·목적지 후보를 따로 받는다(planner 모양)
                     "candidates": ({"primary": ["n1"]} if tool == "dsync"
                                    else {"source": ["n1"], "destination": ["n2"]}),
                     "process_count": 8, "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


@pytest.mark.parametrize("tool", ["dsync", "nsync"])
def test_pending_job_with_a_name_is_rejected_before_any_submission(db, tool):
    repos = Repositories(db)
    rid, jid = _sync_job(repos, chown="alice:users", key="k-name", tool=tool)
    _, ok_jid = _sync_job(repos, chown="10001:10000", key="k-num", tool=tool)
    adapter = StubExecutionAdapter()
    result = JobStepper(repos, adapter, settings=_StepSettings()).run_once()
    assert result[jid] == "Rejected"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "chown_name_not_supported"
    assert repos.requests.last_reason_code(rid) == "chown_name_not_supported"
    assert [s.job_id for s in adapter.submitted_specs()] == [ok_jid]           # 숫자 잡만 제출됐다
    ev = db.query_one("SELECT message FROM events WHERE event_type = 'chown_name_at_step'")
    assert jid in ev["message"]


@pytest.mark.parametrize("tool", ["dsync", "nsync"])
def test_executing_job_with_a_name_fails_closed(db, tool):
    # 미리보기를 통과하고 확인까지 된 옛 잡(root 로 이름이 uid 0 으로 풀리는 위험한 쪽)도 실행 제출 전에 끊는다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos, chown="alice", key="k-exec", tool=tool)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    adapter = StubExecutionAdapter()
    result = JobStepper(repos, adapter, settings=_StepSettings()).run_once()
    assert result[jid] == "Failed"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "chown_name_not_supported"
    assert adapter.submitted_specs() == []


def test_reject_only_while_queued_so_a_concurrent_delete_does_not_inflate_failed_count(db):
    # orchestrator 가 목록을 읽은 뒤 관리자가 그 항목을 지웠다면 거부도 실패 집계도 하지 않는다.
    repos = Repositories(db)
    bid = _legacy_batch(repos)
    repos.batches.add_item(bid, {**SYNC_ITEM, "destination": "c"})              # seq 1
    orch = BatchOrchestrator(repos, settings=_S())
    real = orch._materialize

    def delete_then_materialize(batch, item):
        if item["seq"] == 1:
            repos.batches.delete_item(bid, 1, only_queued=True)
        return real(batch, item)

    orch._materialize = delete_then_materialize
    orch.run_once()
    rows = {it["seq"]: it["status"] for it in repos.batches.list_items(bid)}
    assert rows == {0: "Rejected"}
    b = repos.batches.get(bid)
    assert b["failed_count"] == 1 and b["item_count"] == 1                      # 지워진 항목 몫은 없다
    assert repos.batches.reject_queued_item(bid, 0, reason_code="x") is False   # 이미 종단 -- 다시 세지 않는다
    assert repos.batches.get(bid)["failed_count"] == 1
