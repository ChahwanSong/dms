from dms.repositories import Repositories
from dms.batch_orchestrator import BatchOrchestrator
from dms.domain import DataJobState

class _S:
    preview_ttl_seconds = 900

def _orch(db): return BatchOrchestrator(Repositories(db), settings=_S())

def _make_confirmpending(repos, request_id, fp="fp-x"):
    # 자식이 preview 완료(ConfirmPending)한 상태를 시뮬레이션
    plan = repos.data_jobs.create_plan(request_id, actor="planner")
    jid = repos.data_jobs.create_job(request_id, plan, operation="sync", priority="mid",
        source_storage="s1", destination_storage="s2", source="a", destination="b",
        options={}, tool="dsync", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint=fp, expires_at="2099-01-01T00:00:00Z",
                                artifact_uri="x")
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    return jid

def test_sync_previewing_to_previewready(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="sync", requester_id="admin", actor="admin",
        max_concurrency=2, options={},
        items=[{"source_storage":"s1","source":"a","destination_storage":"s2","destination":"b"}],
        note=None, status="Previewing")
    _orch(db).run_once()                                  # materialize (1개)
    it = repos.batches.list_items(bid)[0]
    _make_confirmpending(repos, it["request_id"])         # preview 완료 시뮬
    _orch(db).run_once()                                  # 전원 previewed → PreviewReady
    assert repos.batches.get(bid)["status"]=="PreviewReady"

def test_sync_running_confirms_children(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="sync", requester_id="admin", actor="admin",
        max_concurrency=2, options={},
        items=[{"source_storage":"s1","source":"a","destination_storage":"s2","destination":"b"}],
        note=None, status="Previewing")
    _orch(db).run_once()
    it = repos.batches.list_items(bid)[0]
    jid = _make_confirmpending(repos, it["request_id"], fp="fp-9")
    _orch(db).run_once()                                  # PreviewReady
    repos.batches.set_status(bid, "Running")              # 운영자 배치 confirm 시뮬
    _orch(db).run_once()                                  # 자식 confirm
    job = repos.data_jobs.get_job(jid)
    assert job["state"]=="Executing" and job["confirmed_fingerprint"]=="fp-9"
